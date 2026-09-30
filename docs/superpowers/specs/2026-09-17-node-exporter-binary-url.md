# Spec: custom node-exporter binary via `binary-url`

## Problem

The charm installs node-exporter from two sources today, selected by the
`package-type` config option: the `prometheus-node-exporter` deb from the
Ubuntu archive (default) or the `node-exporter` snap pinned by the charm.

Neither carries our custom collectors — we run an in-house node_exporter build.
There is currently no way to point the charm at it.

## Requirement

Add a `binary-url` config option that takes precedence over `package-type`.
When set, the charm installs node-exporter from a prebuilt executable fetched
over HTTP(S) and owns its whole lifecycle, including a systemd service for
starting and stopping it.

## Decisions

| Question | Decision |
|---|---|
| What does `binary-url` point at? | A **raw executable**. Not a tarball, not a release page. Non-http(s) URLs and tarball-looking URLs are the operator's problem; the charm validates only the scheme and host. |
| Where does it land? | `/opt/prometheus/node-exporter/node_exporter` |
| When does the charm re-download? | Only when the configured URL differs from the URL recorded in `/opt/prometheus/node-exporter/.install-state.json`, or when the binary is missing. No checksum config. |
| Who does the service run as? | A dedicated unprivileged `node-exporter` system user created by the charm. |
| Interaction with `package-type` | A non-empty `binary-url` wins outright. `package-type` is ignored (and not validated) while it is set. |

## Behaviour

**Flavor resolution.** The charm resolves exactly one *flavor* per reconcile:
`binary` when `binary-url` is non-empty, otherwise the validated
`package-type` value (`apt` or `snap`).

**Third flavor, same rules.** `binary` participates in the existing
`SingletonSnapManager` reference counting under the pseudo-package name
`node-exporter-binary`, exactly as `apt` does under
`prometheus-node-exporter`. Switching to a flavor uninstalls the other two if
this unit is their last registered user. A co-located unit registered under a
different flavor puts this unit in `BlockedStatus`, as it does today for the
snap/deb pair.

**Same metrics.** The binary is launched with the same collector set and
listen port as the other two flavors. Its textfile collector reads
`/opt/prometheus/node-exporter/textfile-collector.d`, where the charm writes
its per-unit `otelcol_subordinate_charm_info` metric file.

**Version gating.** node_exporter exits at startup on an unknown flag. The apt
flavor already filters collector flags newer than the installed deb version;
the binary flavor does the same, reading the version from
`node_exporter --version`. Unknown version means the conservative subset.

**Service.** `/etc/systemd/system/node-exporter.service`, rendered by the
charm, `Restart=always`. Rewritten only when its content changes; the service
is restarted when the unit file changed, the binary was re-downloaded, the
service is not running, or a cross-flavor removal forced it.

**Removal.** The `remove` hook stops and disables the service, deletes the unit
file, and removes `/opt/prometheus/node-exporter` — best-effort, never failing
the hook. The system user is left in place.

## Non-goals

- Tarball or archive extraction.
- Checksum or signature verification.
- Detecting that the bytes at an unchanged URL have changed. Publish each build
  at a distinct URL.
- Removing the `node-exporter` system user on charm removal.

## Known trade-off

Running unprivileged means root-only collectors (notably `systemd`, which reads
the root-owned `/run/systemd/private` socket) log errors instead of producing
metrics. This matches the existing `apt` flavor, where the deb runs as the
`prometheus` user, so the three flavors stay consistent — but it is a real
difference from the `snap` flavor, which runs as root.
