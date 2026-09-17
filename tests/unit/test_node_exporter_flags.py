# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: shared node_exporter command-line flag construction."""

from node_exporter_flags import build_collector_flags, parse_version

LATEST = (1, 10, 2)


def test_parse_version_handles_deb_and_plain_versions():
    # GIVEN version strings from a deb and from a release tag
    # THEN only the leading x.y.z is kept
    assert parse_version("1.3.1-1ubuntu0.22.04.3") == (1, 3, 1)
    assert parse_version("1.7.0-1build2") == (1, 7, 0)
    assert parse_version("1.7-1") == (1, 7, 0)
    # AND unusable input yields None so callers fall back to the safe subset
    assert parse_version(None) is None
    assert parse_version("") is None
    assert parse_version("garbage") is None


def test_build_collector_flags_contains_every_enabled_collector():
    # WHEN building flags for a recent node_exporter
    flags = build_collector_flags(9100, "/tmp/textfile", version=LATEST)
    # THEN every charm-enabled collector is present
    for flag in (
        "--collector.drm",
        "--collector.logind",
        "--collector.systemd",
        "--collector.mountstats",
        "--collector.processes",
        "--collector.sysctl",
        "--collector.textfile",
    ):
        assert flag in flags
    # AND the textfile directory is the one the caller asked for
    assert "--collector.textfile.directory=/tmp/textfile" in flags
    # AND the listen address uses the given port
    assert "--web.listen-address=:9100" in flags


def test_build_collector_flags_is_directory_specific():
    """The deb and the custom binary read different textfile directories."""
    # WHEN two callers pass different directories
    apt_flags = build_collector_flags(9100, "/var/lib/prometheus/node-exporter", version=LATEST)
    bin_flags = build_collector_flags(9100, "/opt/prometheus/node-exporter/textfile-collector.d", version=LATEST)
    # THEN only the textfile.directory flag differs
    assert "--collector.textfile.directory=/var/lib/prometheus/node-exporter" in apt_flags
    assert "--collector.textfile.directory=/opt/prometheus/node-exporter/textfile-collector.d" in bin_flags


def test_build_collector_flags_drops_collectors_newer_than_the_binary():
    """node_exporter exits at startup on an unknown flag, so gate by version."""
    # WHEN building flags for 1.3.1, which predates drm (1.4.0) and sysctl (1.5.0)
    flags = build_collector_flags(9100, "/tmp/textfile", version=(1, 3, 1))
    # THEN the version-gated collectors are dropped
    assert "--collector.drm" not in flags
    assert "--collector.sysctl" not in flags
    # AND the ones 1.3.1 knows are kept
    assert "--collector.systemd" in flags


def test_build_collector_flags_is_conservative_when_version_unknown():
    # WHEN the installed version cannot be determined
    flags = build_collector_flags(9100, "/tmp/textfile", version=None)
    # THEN the version-gated collectors are dropped
    assert "--collector.drm" not in flags
    assert "--collector.sysctl" not in flags
    assert "--collector.systemd" in flags


def test_build_collector_flags_is_deterministic():
    """Flag order is sorted so rendered files only change when the config changes."""
    assert build_collector_flags(9100, "/tmp/textfile", LATEST) == build_collector_flags(
        9100, "/tmp/textfile", LATEST
    )


def test_build_collector_flags_returns_a_list_not_a_string():
    # The systemd ExecStart line and the deb ARGS line join this differently
    flags = build_collector_flags(9100, "/tmp/textfile", LATEST)
    assert isinstance(flags, list)
    assert all(f.startswith("--") for f in flags)
