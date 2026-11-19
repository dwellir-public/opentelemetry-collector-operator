# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Shared node_exporter command-line flag construction.

The charm installs node-exporter from three sources and configures all of them
with the same collector set and listen port. The snap takes its configuration
through `snap set`; the apt deb and the custom binary take command-line flags,
which are built here so those two flavors cannot drift apart.

node_exporter exits at startup when passed a flag it does not know, so
collectors newer than the installed version are filtered out.
"""

import re
from typing import Dict, List, Optional, Tuple

from constants import (
    NODE_EXPORTER_DISABLED_COLLECTORS,
    NODE_EXPORTER_ENABLED_COLLECTORS,
)

Version = Tuple[int, int, int]

# node_exporter version that introduced each charm-enabled collector, for the
# collectors newer than the oldest deb on a supported base (jammy ships 1.3.1).
# Ref: https://github.com/prometheus/node_exporter/blob/master/CHANGELOG.md
COLLECTOR_MIN_VERSIONS: Dict[str, Version] = {
    "drm": (1, 4, 0),
    "sysctl": (1, 5, 0),
}


def parse_version(raw: Optional[str]) -> Optional[Version]:
    """Parse the leading x.y.z out of a version string.

    Args:
        raw: A version string such as '1.3.1-1ubuntu0.22.04.3', or None.

    Returns:
        The (major, minor, patch) tuple, or None when nothing parseable was
        found. Callers treat None as "assume the oldest supported version".
    """
    if not raw:
        return None
    match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", raw)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def build_collector_flags(
    port: int,
    textfile_directory: str,
    version: Optional[Version] = None,
) -> List[str]:
    """Build the node_exporter CLI flags for the charm's collector set.

    Each entry of the collector sets maps to `--collector.<entry>` /
    `--no-collector.<entry>`, matching how the snap translates its
    `collectors`/`no-collectors` config internally. Flags are sorted so the
    file a caller renders only changes when the configuration changes.

    Collectors that the installed node_exporter version does not know are
    dropped (also when the version is unknown), because unknown flags make
    the daemon exit at startup.

    Args:
        port: Port node_exporter listens on.
        textfile_directory: Directory the textfile collector reads; it differs
            between the snap, apt and binary flavors.
        version: The installed node_exporter version, or None if unknown.

    Returns:
        The flags, in a stable order.
    """
    collectors = set()
    for collector in NODE_EXPORTER_ENABLED_COLLECTORS:
        minimum = COLLECTOR_MIN_VERSIONS.get(collector)
        if minimum is None or (version is not None and version >= minimum):
            collectors.add(collector)
    collectors.add(f"textfile.directory={textfile_directory}")
    flags = [f"--collector.{c}" for c in sorted(collectors)]
    flags += [f"--no-collector.{c}" for c in sorted(NODE_EXPORTER_DISABLED_COLLECTORS)]
    flags.append(f"--web.listen-address=:{port}")
    return flags
