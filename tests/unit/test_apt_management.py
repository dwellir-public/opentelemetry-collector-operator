# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: node-exporter installed from apt (prometheus-node-exporter deb)."""

from unittest.mock import patch

import pytest

import apt_management
from constants import NODE_EXPORTER_APT_TEXTFILE_DIRECTORY

# The autouse mock_apt_operations fixture (conftest.py) patches these attributes
# for charm-level tests; capture the real functions at import time to test their own logic.
_real_is_installed = apt_management.is_installed
_real_sync_node_exporter_config = apt_management.sync_node_exporter_config


def test_is_installed_true_and_false():
    with patch.object(apt_management.apt, "DebianPackage") as pkg:
        # GIVEN the package resolves as installed
        pkg.from_installed_package.return_value = object()
        # THEN is_installed is True
        assert _real_is_installed("prometheus-node-exporter") is True
        # GIVEN the package is not installed
        pkg.from_installed_package.side_effect = apt_management.apt.PackageNotFoundError("nope")
        # THEN is_installed is False
        assert _real_is_installed("prometheus-node-exporter") is False


def test_install_package_delegates_to_apt_lib():
    with patch.object(apt_management.apt, "add_package") as add:
        apt_management.install_package("prometheus-node-exporter")
    add.assert_called_once_with("prometheus-node-exporter", update_cache=True)


def test_install_package_raises_apt_install_error():
    with patch.object(
        apt_management.apt, "add_package", side_effect=apt_management.apt.PackageError("boom")
    ):
        with pytest.raises(apt_management.AptInstallError):
            apt_management.install_package("prometheus-node-exporter")


def test_remove_package_tolerates_missing_package():
    with patch.object(
        apt_management.apt,
        "remove_package",
        side_effect=apt_management.apt.PackageNotFoundError("nope"),
    ):
        # THEN no exception propagates
        apt_management.remove_package("prometheus-node-exporter")


def test_ensure_service_running_restarts_stopped_service():
    with (
        patch.object(apt_management.systemd, "service_running", return_value=False),
        patch.object(apt_management.systemd, "service_restart") as restart,
    ):
        # WHEN the service is not running
        apt_management.ensure_service_running("prometheus-node-exporter")
    # THEN it is restarted
    restart.assert_called_once_with("prometheus-node-exporter")


def test_ensure_service_running_noop_when_running():
    with (
        patch.object(apt_management.systemd, "service_running", return_value=True),
        patch.object(apt_management.systemd, "service_restart") as restart,
    ):
        # WHEN the service is already running
        apt_management.ensure_service_running("prometheus-node-exporter")
    # THEN nothing is restarted
    restart.assert_not_called()


def test_build_node_exporter_args_contains_all_flags():
    """The ARGS line enables the same collectors as the snap flavor, plus port and textfile dir."""
    # WHEN building the ARGS line for port 9100
    args = apt_management.build_node_exporter_args(9100)
    # THEN it is a single ARGS="..." line
    assert args.startswith('ARGS="') and args.endswith('"')
    # AND every enabled collector is present as an --collector flag
    for flag in (
        "--collector.drm",
        "--collector.logind",
        "--collector.systemd",
        "--collector.mountstats",
        "--collector.processes",
        "--collector.sysctl",
        "--collector.textfile",
    ):
        assert flag in args
    # AND the textfile directory points at the apt-flavor directory
    assert f"--collector.textfile.directory={NODE_EXPORTER_APT_TEXTFILE_DIRECTORY}" in args
    # AND the listen address uses the given port
    assert "--web.listen-address=:9100" in args


def test_build_node_exporter_args_is_deterministic():
    """Flag order is sorted so the file only changes when the config changes."""
    assert apt_management.build_node_exporter_args(9100) == apt_management.build_node_exporter_args(
        9100
    )


def test_sync_writes_defaults_file_and_restarts_on_change(tmp_path):
    # GIVEN no defaults file exists yet
    defaults = tmp_path / "prometheus-node-exporter"
    with (
        patch.object(apt_management.systemd, "service_restart") as restart,
        patch.object(apt_management.systemd, "service_running", return_value=True),
    ):
        # WHEN syncing the config
        _real_sync_node_exporter_config(9100, defaults_path=str(defaults))
    # THEN the file contains the ARGS line
    assert defaults.read_text().strip() == apt_management.build_node_exporter_args(9100)
    # AND the service is restarted (content changed from absent to present)
    restart.assert_called_once()


def test_sync_skips_restart_when_unchanged(tmp_path):
    # GIVEN a defaults file already holding the desired content
    defaults = tmp_path / "prometheus-node-exporter"
    defaults.write_text(apt_management.build_node_exporter_args(9100) + "\n")
    with (
        patch.object(apt_management.systemd, "service_restart") as restart,
        patch.object(apt_management.systemd, "service_running", return_value=True),
    ):
        # WHEN syncing with the same port
        _real_sync_node_exporter_config(9100, defaults_path=str(defaults))
    # THEN no restart happens
    restart.assert_not_called()


def test_sync_restarts_when_service_not_running(tmp_path):
    # GIVEN an up-to-date defaults file but a stopped service
    defaults = tmp_path / "prometheus-node-exporter"
    defaults.write_text(apt_management.build_node_exporter_args(9100) + "\n")
    with (
        patch.object(apt_management.systemd, "service_restart") as restart,
        patch.object(apt_management.systemd, "service_running", return_value=False),
    ):
        # WHEN syncing
        _real_sync_node_exporter_config(9100, defaults_path=str(defaults))
    # THEN the service is (re)started
    restart.assert_called_once()


def test_sync_force_restart(tmp_path):
    # GIVEN an up-to-date defaults file and a running service
    defaults = tmp_path / "prometheus-node-exporter"
    defaults.write_text(apt_management.build_node_exporter_args(9100) + "\n")
    with (
        patch.object(apt_management.systemd, "service_restart") as restart,
        patch.object(apt_management.systemd, "service_running", return_value=True),
    ):
        # WHEN syncing with force_restart (e.g. right after the snap flavor was removed)
        _real_sync_node_exporter_config(9100, defaults_path=str(defaults), force_restart=True)
    # THEN the service is restarted anyway
    restart.assert_called_once()
