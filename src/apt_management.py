# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Apt Package Management Module.

Counterpart of snap_management.py for the apt-installed node-exporter
(the `prometheus-node-exporter` deb from the Ubuntu archive).

The deb's systemd unit runs `prometheus-node-exporter $ARGS`, with ARGS read
from /etc/default/prometheus-node-exporter. This module writes that file so
the apt flavor captures the same collectors and listen port as the snap.
"""

import logging

import charms.operator_libs_linux.v0.apt as apt
import charms.operator_libs_linux.v1.systemd as systemd
from charmlibs.pathops import LocalPath

from constants import (
    NODE_EXPORTER_APT_DEFAULTS_PATH,
    NODE_EXPORTER_APT_SERVICE,
    NODE_EXPORTER_APT_TEXTFILE_DIRECTORY,
    NODE_EXPORTER_DISABLED_COLLECTORS,
    NODE_EXPORTER_ENABLED_COLLECTORS,
)

log = logging.getLogger(__name__)


class AptError(Exception):
    """Base exception for all apt-related errors."""


class AptInstallError(AptError):
    """Raised when there's an error installing a deb package."""


def is_installed(package_name: str) -> bool:
    """Return True if the deb package is installed."""
    try:
        apt.DebianPackage.from_installed_package(package_name)
        return True
    except apt.PackageNotFoundError:
        return False


def install_package(package_name: str) -> None:
    """Install a deb package from the Ubuntu archive.

    Raises:
        AptInstallError: if the package cannot be found or installed.
    """
    try:
        apt.add_package(package_name, update_cache=True)
        log.info(f"{package_name} deb has been installed")
    except (apt.PackageNotFoundError, apt.PackageError) as e:
        raise AptInstallError(f"Failed to install {package_name} from apt") from e


def remove_package(package_name: str) -> None:
    """Remove a deb package. Missing packages are ignored."""
    try:
        apt.remove_package(package_name)
        log.info(f"{package_name} deb has been removed")
    except apt.PackageNotFoundError:
        log.debug(f"{package_name} deb was not installed; nothing to remove")


def ensure_service_running(service_name: str) -> None:
    """Start the service if it is not running.

    Debs auto-start their service on install, but the service can be down if it
    failed to bind its port while the other node-exporter flavor still held it.
    """
    if not systemd.service_running(service_name):
        systemd.service_restart(service_name)


def build_node_exporter_args(port: int) -> str:
    """Build the ARGS line for /etc/default/prometheus-node-exporter.

    Each entry of the collector sets maps to `--collector.<entry>` /
    `--no-collector.<entry>`, matching how the snap translates its
    `collectors`/`no-collectors` config internally. Flags are sorted so the
    file only changes when the configuration changes.
    """
    collectors = NODE_EXPORTER_ENABLED_COLLECTORS | {
        f"textfile.directory={NODE_EXPORTER_APT_TEXTFILE_DIRECTORY}"
    }
    flags = [f"--collector.{c}" for c in sorted(collectors)]
    flags += [f"--no-collector.{c}" for c in sorted(NODE_EXPORTER_DISABLED_COLLECTORS)]
    flags.append(f"--web.listen-address=:{port}")
    return f'ARGS="{" ".join(flags)}"'


def sync_node_exporter_config(
    port: int,
    defaults_path: str = NODE_EXPORTER_APT_DEFAULTS_PATH,
    force_restart: bool = False,
) -> None:
    """Write the node-exporter defaults file and (re)start the service as needed.

    The service is restarted when the file content changed, when the service is
    not running (e.g. it failed to bind while the other package flavor still
    held the port), or when force_restart is set.
    """
    desired = build_node_exporter_args(port) + "\n"
    path = LocalPath(defaults_path)
    changed = not path.exists() or path.read_text() != desired
    if changed:
        path.write_text(desired)
    if changed or force_restart or not systemd.service_running(NODE_EXPORTER_APT_SERVICE):
        systemd.service_restart(NODE_EXPORTER_APT_SERVICE)
