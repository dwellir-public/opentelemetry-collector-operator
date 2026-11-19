# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: the charm deploys and collects host metrics on Ubuntu 26.04."""

import jubilant
from helpers import ENABLE_BASIC_DEBUG_EXPORTERS, PATH_EXCLUDE, RETRY

BASE = "ubuntu@26.04"


def test_deploy(juju: jubilant.Juju, charm_26_04: str):
    # GIVEN an OpenTelemetry Collector charm with default config and a 26.04 principal
    juju.deploy(
        charm_26_04,
        app="otelcol",
        base=BASE,
        config={"path_exclude": PATH_EXCLUDE, **ENABLE_BASIC_DEBUG_EXPORTERS},
    )
    juju.deploy("ubuntu", channel="latest/stable", base=BASE)
    # WHEN they are related
    juju.integrate("otelcol:juju-info", "ubuntu:juju-info")
    # THEN the model settles without errors; otelcol is expected to be blocked on
    # missing mandatory outgoing relations, like in test_principal.py
    juju.wait(jubilant.all_agents_idle, error=jubilant.any_error, timeout=420)


def test_machine_runs_26_04(juju: jubilant.Juju):
    # THEN the unit really landed on a 26.04 machine
    assert 'VERSION_ID="26.04"' in juju.ssh("otelcol/0", command="cat /etc/os-release")


@RETRY
def test_collector_snap_is_running(juju: jubilant.Juju):
    # THEN the collector snap service is active
    output = juju.ssh("otelcol/0", command="snap services opentelemetry-collector")
    assert "active" in output
    assert "inactive" not in output


@RETRY
def test_node_exporter_deb_serves_metrics_with_charm_collectors(juju: jubilant.Juju):
    # THEN node-exporter (the deb, by default) serves metrics on its port
    output = juju.ssh("otelcol/0", command="curl -s http://localhost:9100/metrics | head -5")
    assert "# HELP" in output
    # AND it runs with the version-gated collectors, which the 26.04 deb supports
    output = juju.ssh(
        "otelcol/0", command="ps ax | grep node-exporter | egrep -v 'grep|snapfuse'"
    )
    assert "collector.drm" in output
    assert "collector.sysctl" in output
