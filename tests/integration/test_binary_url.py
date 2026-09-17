# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: node-exporter installed from a custom binary via binary-url."""

import jubilant
from helpers import ENABLE_BASIC_DEBUG_EXPORTERS, PATH_EXCLUDE, RETRY

BINARY_URL = "http://localhost:8099/node_exporter"
BINARY_PATH = "/opt/prometheus/node-exporter/node_exporter"
BINARY_DIR = "/opt/prometheus/node-exporter"
TEXTFILE_DIR = "/opt/prometheus/node-exporter/textfile-collector.d"


def settle(juju: jubilant.Juju):
    # otelcol is expected to be blocked on missing mandatory outgoing relations,
    # like in test_principal.py; agents must be idle either way.
    juju.wait(jubilant.all_agents_idle, error=jubilant.any_error, timeout=420)


def deb_installed(juju: jubilant.Juju) -> bool:
    output = juju.ssh(
        "otelcol/0",
        command="dpkg-query -W -f '${Status}' prometheus-node-exporter 2>/dev/null || true",
    )
    return "install ok installed" in output


def path_exists(juju: jubilant.Juju, path: str) -> bool:
    return "yes" in juju.ssh("otelcol/0", command=f"test -e {path} && echo yes || echo no")


def service_state(juju: jubilant.Juju) -> str:
    return juju.ssh(
        "otelcol/0", command="systemctl is-active node-exporter 2>/dev/null || true"
    ).strip()


def node_exporter_serving_metrics(juju: jubilant.Juju) -> bool:
    output = juju.ssh("otelcol/0", command="curl -s http://localhost:9100/metrics | head -5")
    return "node_" in output or "# HELP" in output


def test_deploy(juju: jubilant.Juju, charm: str):
    # GIVEN an OpenTelemetry Collector charm with default config and a principal
    juju.deploy(
        charm,
        app="otelcol",
        config={"path_exclude": PATH_EXCLUDE, **ENABLE_BASIC_DEBUG_EXPORTERS},
    )
    juju.deploy("ubuntu", channel="latest/stable", base="ubuntu@24.04")
    # WHEN they are related
    juju.integrate("otelcol:juju-info", "ubuntu:juju-info")
    # THEN the model settles on the default apt flavor
    settle(juju)
    assert deb_installed(juju)


def test_publish_a_custom_binary_on_the_machine(juju: jubilant.Juju):
    """Serve a real node_exporter executable over HTTP from the unit's machine.

    The payload is the deb's own binary, copied before the charm purges the deb,
    so the test needs no internet access and no hardcoded upstream version.
    """
    # GIVEN the deb's node_exporter binary, copied out to a served directory
    juju.ssh(
        "otelcol/0",
        command=(
            "sudo mkdir -p /srv/ne && "
            "sudo cp /usr/bin/prometheus-node-exporter /srv/ne/node_exporter && "
            "sudo sh -c 'cd /srv/ne && setsid nohup python3 -m http.server 8099 "
            ">/tmp/ne-http.log 2>&1 </dev/null &'"
        ),
    )
    # THEN the binary is downloadable from the URL the charm will be given
    _assert_binary_is_served(juju)


@RETRY
def _assert_binary_is_served(juju: jubilant.Juju):
    output = juju.ssh(
        "otelcol/0", command=f"curl -sf -o /dev/null -w '%{{http_code}}' {BINARY_URL} || true"
    )
    assert "200" in output


def test_switch_to_the_custom_binary(juju: jubilant.Juju):
    # WHEN binary-url is set
    juju.config("otelcol", {"binary-url": BINARY_URL})
    settle(juju)
    # THEN the charm takes over from the deb
    _assert_binary_flavor(juju)


@RETRY
def _assert_binary_flavor(juju: jubilant.Juju):
    # The deb is gone and the binary is installed where the charm puts it
    assert not deb_installed(juju)
    assert path_exists(juju, BINARY_PATH)
    # AND the charm-managed systemd service is running it
    assert service_state(juju) == "active"
    # AND it serves metrics on the node-exporter port
    assert node_exporter_serving_metrics(juju)


@RETRY
def test_service_runs_as_the_dedicated_user(juju: jubilant.Juju):
    # THEN systemd runs the binary unprivileged, as the charm-created user
    output = juju.ssh("otelcol/0", command="systemctl show -p User --value node-exporter")
    assert output.strip() == "node-exporter"


@RETRY
def test_service_uses_the_charm_collector_flags(juju: jubilant.Juju):
    # THEN ExecStart points the textfile collector at the binary flavor's directory
    output = juju.ssh("otelcol/0", command="systemctl show -p ExecStart --value node-exporter")
    assert f"--collector.textfile.directory={TEXTFILE_DIR}" in output
    assert "--web.listen-address=:9100" in output


@RETRY
def test_info_metric_scraped_from_the_binary_textfile_dir(juju: jubilant.Juju):
    # THEN the per-unit info metric the charm writes under /opt is served
    output = juju.ssh(
        "otelcol/0",
        command="curl -s http://localhost:9100/metrics | grep otelcol_subordinate_charm_info || true",
    )
    assert "otelcol_subordinate_charm_info" in output


def test_invalid_binary_url_blocks(juju: jubilant.Juju):
    # WHEN binary-url is set to something that is not an http(s) URL
    juju.config("otelcol", {"binary-url": "not-a-url"})
    settle(juju)
    # THEN the unit reports the problem instead of erroring
    _assert_blocked_on_binary_url(juju)
    # Restore a working URL for the next test
    juju.config("otelcol", {"binary-url": BINARY_URL})
    settle(juju)


@RETRY
def _assert_blocked_on_binary_url(juju: jubilant.Juju):
    status = juju.status()
    unit = status.apps["otelcol"].units["otelcol/0"]
    assert "binary-url" in unit.workload_status.message


def test_switch_back_to_apt(juju: jubilant.Juju):
    # WHEN binary-url is cleared
    juju.config("otelcol", {"binary-url": ""})
    settle(juju)
    # THEN the charm falls back to the default apt flavor and cleans up after itself
    _assert_apt_flavor_restored(juju)


@RETRY
def _assert_apt_flavor_restored(juju: jubilant.Juju):
    assert deb_installed(juju)
    assert not path_exists(juju, BINARY_DIR)
    assert service_state(juju) != "active"
    assert node_exporter_serving_metrics(juju)
