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

# All collectors are known by this version, so no flag is filtered out.
LATEST = (1, 10, 2)


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


def test_parse_version():
    # Deb version strings parse down to the leading x.y.z
    assert apt_management._parse_version("1.3.1-1ubuntu0.22.04.3") == (1, 3, 1)
    assert apt_management._parse_version("1.7.0-1build2") == (1, 7, 0)
    assert apt_management._parse_version("1.7-1") == (1, 7, 0)
    assert apt_management._parse_version(None) is None
    assert apt_management._parse_version("garbage") is None


def test_build_node_exporter_args_contains_all_flags():
    """The ARGS line enables the same collectors as the snap flavor, plus port and textfile dir."""
    # WHEN building the ARGS line for port 9100 on a recent node_exporter
    args = apt_management.build_node_exporter_args(9100, version=LATEST)
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


def test_build_node_exporter_args_filters_unknown_collectors_on_old_versions():
    """Flags unknown to the installed version would make node_exporter exit at startup.

    Regression: jammy's deb is 1.3.1, which predates the drm (1.4.0) and sysctl (1.5.0)
    collectors; passing them left the service unable to start.
    """
    # WHEN building ARGS for jammy's node_exporter 1.3.1
    args = apt_management.build_node_exporter_args(9100, version=(1, 3, 1))
    # THEN version-gated collectors are dropped
    assert "--collector.drm" not in args
    assert "--collector.sysctl" not in args
    # AND collectors that 1.3.1 knows are kept
    for flag in (
        "--collector.logind",
        "--collector.systemd",
        "--collector.mountstats",
        "--collector.processes",
        "--collector.textfile",
    ):
        assert flag in args


def test_build_node_exporter_args_conservative_when_version_unknown():
    # WHEN the installed version cannot be determined
    args = apt_management.build_node_exporter_args(9100, version=None)
    # THEN the version-gated collectors are dropped (safe subset)
    assert "--collector.drm" not in args
    assert "--collector.sysctl" not in args
    assert "--collector.systemd" in args


def test_build_node_exporter_args_is_deterministic():
    """Flag order is sorted so the file only changes when the config changes."""
    assert apt_management.build_node_exporter_args(
        9100, version=LATEST
    ) == apt_management.build_node_exporter_args(9100, version=LATEST)


def _sync(port, defaults, force_restart=False, running=True, version="1.10.2-1"):
    """Run the real sync with systemd and the installed version mocked."""
    with (
        patch.object(apt_management.systemd, "service_restart") as restart,
        patch.object(apt_management.systemd, "service_running", return_value=running),
        patch.object(apt_management, "installed_version", return_value=version),
    ):
        _real_sync_node_exporter_config(port, defaults_path=str(defaults), force_restart=force_restart)
    return restart


def test_sync_writes_defaults_file_and_restarts_on_change(tmp_path):
    # GIVEN no defaults file exists yet
    defaults = tmp_path / "prometheus-node-exporter"
    # WHEN syncing the config
    restart = _sync(9100, defaults)
    # THEN the file contains the ARGS line
    assert defaults.read_text().strip() == apt_management.build_node_exporter_args(
        9100, version=LATEST
    )
    # AND the service is restarted (content changed from absent to present)
    restart.assert_called_once()


def test_sync_skips_restart_when_unchanged(tmp_path):
    # GIVEN a defaults file already holding the desired content
    defaults = tmp_path / "prometheus-node-exporter"
    defaults.write_text(apt_management.build_node_exporter_args(9100, version=LATEST) + "\n")
    # WHEN syncing with the same port
    restart = _sync(9100, defaults)
    # THEN no restart happens
    restart.assert_not_called()


def test_sync_restarts_when_service_not_running(tmp_path):
    # GIVEN an up-to-date defaults file but a stopped service
    defaults = tmp_path / "prometheus-node-exporter"
    defaults.write_text(apt_management.build_node_exporter_args(9100, version=LATEST) + "\n")
    # WHEN syncing
    restart = _sync(9100, defaults, running=False)
    # THEN the service is (re)started
    restart.assert_called_once()


def test_sync_force_restart(tmp_path):
    # GIVEN an up-to-date defaults file and a running service
    defaults = tmp_path / "prometheus-node-exporter"
    defaults.write_text(apt_management.build_node_exporter_args(9100, version=LATEST) + "\n")
    # WHEN syncing with force_restart (e.g. right after the snap flavor was removed)
    restart = _sync(9100, defaults, force_restart=True)
    # THEN the service is restarted anyway
    restart.assert_called_once()


def test_sync_writes_version_appropriate_args(tmp_path):
    # GIVEN jammy's node_exporter 1.3.1 is installed
    defaults = tmp_path / "prometheus-node-exporter"
    # WHEN syncing
    _sync(9100, defaults, version="1.3.1-1ubuntu0.22.04.3")
    # THEN the written ARGS exclude the collectors 1.3.1 does not know
    content = defaults.read_text()
    assert "--collector.drm" not in content
    assert "--collector.systemd" in content


def test_sync_raises_apt_service_error_on_restart_failure(tmp_path):
    # GIVEN systemctl restart fails
    defaults = tmp_path / "prometheus-node-exporter"
    with (
        patch.object(
            apt_management.systemd,
            "service_restart",
            side_effect=apt_management.systemd.SystemdError("boom"),
        ),
        patch.object(apt_management.systemd, "service_running", return_value=True),
        patch.object(apt_management, "installed_version", return_value="1.10.2-1"),
    ):
        # THEN a typed error is raised for the charm to turn into BlockedStatus
        with pytest.raises(apt_management.AptServiceError):
            _real_sync_node_exporter_config(9100, defaults_path=str(defaults))
