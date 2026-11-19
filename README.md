# OpenTelemetry Collector Operator for Machines

[![CharmHub Badge](https://charmhub.io/opentelemetry-collector/badge.svg)](https://charmhub.io/opentelemetry-collector)
[![Release](https://github.com/canonical/opentelemetry-collector-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/opentelemetry-collector-operator/actions/workflows/release.yaml)
[![Discourse Status](https://img.shields.io/discourse/status?server=https%3A%2F%2Fdiscourse.charmhub.io&style=flat&label=CharmHub%20Discourse)](https://discourse.charmhub.io)

This repository contains the source code for a Charmed Operator that drives [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector), a vendor-agnostic way to receive, process and export telemetry data, on machines (LXD, MAAS, etc).

## Usage
Assuming you have access to a bootstrapped Juju controller, you can:

```bash
$ juju deploy opentelemetry-collector
```
The `opentelemetry-collector` operator for machines is a subordinate charm.

It needs to be related to a principal charm. The principal charm needs to support relations through the `cos-agent` or `juju-info` interfaces.
For example, if you have the `ubuntu` principal, you can relate it to this charm:

```bash
$ juju integrate ubuntu opentelemetry-collector
```

Given that this charm is a subordinate, it scales up and down with its principal. As a result, if the `ubuntu` principal is scaled up to 2, a new machine is spun up and the `opentelemetry-collector` charm will be deployed in it and related to `ubuntu`.

## Snap

This charm uses the [Opentelemetry Collector snap](https://github.com/canonical/opentelemetry-collector-snap/).
When deployed, the charm will write a configuration file for the workload based on available integrations, configurations, etc and 
write it to the machine hosting the snap at `/etc/otelcol/config.d`.

## node-exporter

Alongside the collector, this charm manages a node-exporter on the same machine
and scrapes it. There are three mutually exclusive sources for it:

| Source | Selected by | Installed at | Service |
| --- | --- | --- | --- |
| Ubuntu archive deb (default) | `package-type=apt` | `prometheus-node-exporter` package | `prometheus-node-exporter.service` |
| Snap pinned by the charm | `package-type=snap` | `node-exporter` snap | snap service |
| Custom build | `binary-url=<url>` | `/opt/prometheus/node-exporter/node_exporter` | `node-exporter.service` |

A non-empty `binary-url` takes precedence over `package-type`. Use it to run a
node_exporter build with collectors that are in neither upstream package:

```bash
$ juju config opentelemetry-collector \
    binary-url=https://example.com/builds/node_exporter-1.10.2-custom
```

The URL must point directly at the executable. The charm downloads it, creates
an unprivileged `node-exporter` system user, and writes a systemd unit that runs
it with the same collectors and listen port as the other two sources. It
re-downloads only when the URL changes, so give each build its own URL.

All three sources are configured identically by the charm, and only one may be
in use across all collector units sharing a machine — mixing them puts the units
in blocked status, since they would fight over the node-exporter port.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and the [contributing](https://github.com/canonical/opentelemetry-collector-operator/blob/main/CONTRIBUTING.md) doc for developer guidance.
