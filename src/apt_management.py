# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Apt Package Management Module.

Counterpart of snap_management.py for the apt-installed node-exporter
(the `prometheus-node-exporter` deb from the Ubuntu archive).

The deb's systemd unit runs `prometheus-node-exporter $ARGS`, with ARGS read
from /etc/default/prometheus-node-exporter. This module writes that file so
the apt flavor captures the same collectors and listen port as the snap.

Unlike the snap (pinned to a charm-chosen revision), the deb version depends
on the machine's Ubuntu release (e.g. 1.3.1 on jammy, 1.7.0 on noble), so
collector flags that the installed version does not know are filtered out —
node_exporter exits on unknown flags, which would leave the service down.
"""

import logging
import re
from typing import Optional, Tuple

import charms.operator_libs_linux.v0.apt as apt
import charms.operator_libs_linux.v1.systemd as systemd
from charmlibs.pathops import LocalPath

from constants import (
    NODE_EXPORTER_APT_DEFAULTS_PATH,
    NODE_EXPORTER_APT_PACKAGE,
    NODE_EXPORTER_APT_SERVICE,
    NODE_EXPORTER_APT_TEXTFILE_DIRECTORY,
    NODE_EXPORTER_DISABLED_COLLECTORS,
    NODE_EXPORTER_ENABLED_COLLECTORS,
)

log = logging.getLogger(__name__)

# node_exporter version that introduced each charm-enabled collector, for the
# collectors newer than the oldest deb on a supported base (jammy ships 1.3.1).
# Ref: https://github.com/prometheus/node_exporter/blob/master/CHANGELOG.md
NODE_EXPORTER_COLLECTOR_MIN_VERSIONS = {
    "drm": (1, 4, 0),
    "sysctl": (1, 5, 0),
}


class AptError(Exception):
    """Base exception for all apt-related errors."""


class AptInstallError(AptError):
    """Raised when there's an error installing a deb package."""


class AptServiceError(AptError):
    """Raised when a deb's systemd service fails to (re)start."""


def is_installed(package_name: str) -> bool:
    """Return True if the deb package is installed."""
    try:
        apt.DebianPackage.from_installed_package(package_name)
        return True
    except apt.PackageNotFoundError:
        return False


def installed_version(package_name: str) -> Optional[str]:
    """Return the installed deb version string (e.g. '1.3.1-1ubuntu0.22.04.3'), or None."""
    try:
        return apt.DebianPackage.from_installed_package(package_name).version.number
    except apt.PackageNotFoundError:
        return None


def _parse_version(raw: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """Parse the leading x.y.z out of a deb version string."""
    if not raw:
        return None
    match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", raw)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


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


def build_node_exporter_args(port: int, version: Optional[Tuple[int, int, int]] = None) -> str:
    """Build the ARGS line for /etc/default/prometheus-node-exporter.

    Each entry of the collector sets maps to `--collector.<entry>` /
    `--no-collector.<entry>`, matching how the snap translates its
    `collectors`/`no-collectors` config internally. Flags are sorted so the
    file only changes when the configuration changes.

    Collectors that the installed node_exporter version does not know are
    dropped (also when the version is unknown), because unknown flags make
    the daemon exit at startup.
    """
    collectors = set()
    for collector in NODE_EXPORTER_ENABLED_COLLECTORS:
        minimum = NODE_EXPORTER_COLLECTOR_MIN_VERSIONS.get(collector)
        if minimum is None or (version is not None and version >= minimum):
            collectors.add(collector)
    collectors.add(f"textfile.directory={NODE_EXPORTER_APT_TEXTFILE_DIRECTORY}")
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

    Raises:
        AptServiceError: if the service fails to (re)start.
    """
    version = _parse_version(installed_version(NODE_EXPORTER_APT_PACKAGE))
    desired = build_node_exporter_args(port, version) + "\n"
    path = LocalPath(defaults_path)
    changed = not path.exists() or path.read_text() != desired
    if changed:
        path.write_text(desired)
    if changed or force_restart or not systemd.service_running(NODE_EXPORTER_APT_SERVICE):
        try:
            systemd.service_restart(NODE_EXPORTER_APT_SERVICE)
        except systemd.SystemdError as e:
            raise AptServiceError(f"Failed to restart {NODE_EXPORTER_APT_SERVICE}") from e
