# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: switching node-exporter to and from a custom binary (binary-url)."""

from typing import Dict, Union
from unittest.mock import patch

from ops.testing import State

import apt_management
import binary_management
from constants import NODE_EXPORTER_APT_PACKAGE, NODE_EXPORTER_BINARY_PACKAGE
from singleton_snap import SingletonSnapManager

URL = "https://example.com/builds/node_exporter-custom-1.10.2"
BINARY: Dict[str, Union[str, int, float, bool]] = {"binary-url": URL}


def test_install_hook_registers_the_binary_not_the_snap_or_deb(ctx):
    # GIVEN a binary-url
    with patch("charm.event", return_value="install"), patch("charm.install_snap"):
        # WHEN the install hook runs
        ctx.run(ctx.on.install(), State(config=BINARY))
    # THEN only the binary pseudo-package is registered for node-exporter
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"otelcol_0"}
    assert SingletonSnapManager.get_units("node-exporter") == set()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_APT_PACKAGE) == set()
    # AND the otelcol snap is still registered
    assert SingletonSnapManager.get_units("opentelemetry-collector") == {"otelcol_0"}


def test_install_hook_does_not_install_the_node_exporter_snap(ctx):
    with (
        patch("charm.event", return_value="install"),
        patch("charm.install_snap") as mock_install,
    ):
        ctx.run(ctx.on.install(), State(config=BINARY))
    installed = {call.args[0] for call in mock_install.call_args_list}
    assert "opentelemetry-collector" in installed
    assert "node-exporter" not in installed


def test_binary_mode_syncs_the_service_with_the_configured_url_and_port(
    ctx, mock_binary_operations
):
    # GIVEN a binary-url and a node_exporter port override
    state = State(config={**BINARY, "ports": "node_exporter=9200"})
    # WHEN the reconciler runs
    ctx.run(ctx.on.config_changed(), state)
    # THEN the service is synced with that URL and port
    mock_binary_operations["sync_service"].assert_called_once()
    args, kwargs = mock_binary_operations["sync_service"].call_args
    assert args[0] == URL
    assert args[1] == 9200


def test_binary_mode_does_not_configure_the_snap(ctx, mock_binary_operations):
    # GIVEN binary mode
    with patch("charm.OpenTelemetryCollectorCharm._configure_node_exporter") as mock_cfg:
        # WHEN the reconciler runs
        ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the snap-flavor configuration path is never invoked
    mock_cfg.assert_not_called()


def test_binary_mode_writes_the_info_metric_file(ctx, mock_binary_operations, tmp_path):
    # GIVEN binary mode (the textfile dir is redirected into tmp_path by conftest)
    ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN this unit's info metric file exists in the textfile directory
    assert (tmp_path / "textfile-collector.d" / "otelcol_0.prom").exists()


def test_switch_apt_to_binary_removes_the_deb(ctx, mock_apt_operations, mock_binary_operations):
    # GIVEN this unit previously registered the deb and the deb is installed
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    mock_apt_operations["is_installed"].return_value = True
    with patch("apt_management.remove_package") as mock_remove_deb:
        # WHEN a binary-url is configured
        ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the deb is removed and its registration dropped
    mock_remove_deb.assert_called_once_with(NODE_EXPORTER_APT_PACKAGE)
    assert SingletonSnapManager.get_units(NODE_EXPORTER_APT_PACKAGE) == set()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"otelcol_0"}
    # AND the service is restarted anyway, because the deb just freed the port
    assert mock_binary_operations["sync_service"].call_args.kwargs["force_restart"] is True


def test_switch_snap_to_binary_removes_the_snap(
    ctx, mock_binary_operations, mock_snap_operations
):
    # GIVEN this unit previously registered the node-exporter snap, which is present
    SingletonSnapManager("otelcol/0").register("node-exporter", 2)
    mock_snap_operations.present = True
    with patch("charm.OpenTelemetryCollectorCharm._remove_snap") as mock_remove:
        # WHEN a binary-url is configured
        ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the snap is removed and its registration dropped
    mock_remove.assert_called_once_with("node-exporter")
    assert SingletonSnapManager.get_units("node-exporter") == set()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"otelcol_0"}


def test_switch_to_binary_keeps_a_deb_used_by_another_unit(
    ctx, mock_apt_operations, mock_binary_operations
):
    # GIVEN another co-located unit still uses the deb
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    SingletonSnapManager("other/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    mock_apt_operations["is_installed"].return_value = True
    with patch("apt_management.remove_package") as mock_remove_deb:
        # WHEN this unit switches to a binary-url
        ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the deb is NOT removed
    mock_remove_deb.assert_not_called()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_APT_PACKAGE) == {"other_0"}


def test_switch_binary_to_apt_removes_the_binary(ctx, mock_apt_operations, mock_binary_operations):
    # GIVEN this unit previously used a custom binary, which is installed
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    mock_binary_operations["is_installed"].return_value = True
    with patch("apt_management.install_package"):
        # WHEN binary-url is cleared, falling back to the default apt flavor
        ctx.run(ctx.on.config_changed(), State(config={"binary-url": ""}))
    # THEN the binary and its service are removed and the registration dropped
    mock_binary_operations["remove"].assert_called_once()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == set()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_APT_PACKAGE) == {"otelcol_0"}
    # AND the deb service is force-restarted, because the binary just freed the port
    assert mock_apt_operations["sync_config"].call_args.kwargs["force_restart"] is True


def test_switch_binary_to_apt_keeps_a_binary_used_by_another_unit(
    ctx, mock_apt_operations, mock_binary_operations
):
    # GIVEN another co-located unit still uses the custom binary
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    SingletonSnapManager("other/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    mock_binary_operations["is_installed"].return_value = True
    with patch("apt_management.install_package"):
        # WHEN this unit clears binary-url
        ctx.run(ctx.on.config_changed(), State(config={"binary-url": ""}))
    # THEN the binary is NOT removed
    mock_binary_operations["remove"].assert_not_called()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"other_0"}


def test_blocked_when_another_unit_uses_a_different_flavor(ctx, mock_binary_operations):
    # GIVEN a co-located unit of another app registered the deb
    SingletonSnapManager("other/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    # WHEN this unit reconciles with a binary-url
    state_out = ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN it blocks with an actionable message
    assert state_out.unit_status.name == "blocked"
    assert "binary-url" in state_out.unit_status.message


def test_no_conflict_when_all_units_use_the_binary(ctx, mock_binary_operations):
    # GIVEN another unit also using the custom binary
    SingletonSnapManager("other/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the unit is not blocked over a flavor conflict
    assert "different node-exporter flavor" not in getattr(state_out.unit_status, "message", "")


def test_download_failure_blocks_instead_of_crashing(ctx, mock_binary_operations):
    # GIVEN the binary cannot be fetched
    mock_binary_operations["sync_service"].side_effect = binary_management.BinaryDownloadError(
        "404"
    )
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the hook completes with a blocked status instead of raising
    assert state_out.unit_status.name == "blocked"
    assert "Failed to download" in state_out.unit_status.message


def test_service_failure_blocks_instead_of_crashing(ctx, mock_binary_operations):
    # GIVEN the service will not start
    mock_binary_operations["sync_service"].side_effect = binary_management.BinaryServiceError(
        "boom"
    )
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the hook completes with a blocked status instead of raising
    assert state_out.unit_status.name == "blocked"
    assert "failed to start" in state_out.unit_status.message


def test_remove_hook_removes_the_binary(ctx, mock_binary_operations):
    # GIVEN this unit uses a custom binary, which is installed
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    mock_binary_operations["is_installed"].return_value = True
    with patch("charm.event", return_value="remove"):
        # WHEN the remove hook runs
        ctx.run(ctx.on.remove(), State(config=BINARY))
    # THEN the binary and its service are removed and the registration dropped
    mock_binary_operations["remove"].assert_called_once()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == set()


def test_remove_hook_keeps_a_binary_used_by_another_unit(ctx, mock_binary_operations):
    # GIVEN another co-located unit still uses the custom binary
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    SingletonSnapManager("other/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    mock_binary_operations["is_installed"].return_value = True
    with patch("charm.event", return_value="remove"):
        ctx.run(ctx.on.remove(), State(config=BINARY))
    # THEN the binary is NOT removed
    mock_binary_operations["remove"].assert_not_called()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"other_0"}


def test_switch_to_binary_blocks_when_deb_removal_fails(
    ctx, mock_apt_operations, mock_binary_operations
):
    # GIVEN this unit previously registered the deb, which is installed, but its removal fails
    # (e.g. a held dpkg lock)
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    mock_apt_operations["is_installed"].return_value = True
    with patch("apt_management.remove_package", side_effect=apt_management.AptError("boom")):
        # WHEN a binary-url is configured
        state_out = ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the hook completes with a blocked status instead of raising, because the deb
    # still holds the node-exporter port
    assert state_out.unit_status.name == "blocked"
    assert NODE_EXPORTER_APT_PACKAGE in state_out.unit_status.message


def test_switch_to_snap_blocks_when_deb_removal_fails(
    ctx, mock_apt_operations, mock_snap_operations
):
    # GIVEN this unit previously registered the deb, which is installed, but its removal fails
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    mock_apt_operations["is_installed"].return_value = True
    with (
        patch("apt_management.remove_package", side_effect=apt_management.AptError("boom")),
        patch("charm.install_snap"),
    ):
        # WHEN this unit switches to package-type=snap
        state_out = ctx.run(ctx.on.config_changed(), State(config={"package-type": "snap"}))
    # THEN the hook completes with a blocked status instead of raising
    assert state_out.unit_status.name == "blocked"
    assert NODE_EXPORTER_APT_PACKAGE in state_out.unit_status.message
