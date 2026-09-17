"""Charm constants, for better testability."""

from typing import Final, Set

SERVICE_NAME: Final[str] = "otelcol"
CUSTOM_COMPONENT_ID: Final[str] = "_custom"
CERT_DIR: Final[str] = "/var/snap/opentelemetry-collector/common/certs"
EXTERNAL_CONFIG_SECRETS_DIR: Final[str] = "/var/snap/opentelemetry-collector/common/external_config_secrets"
SERVER_CERT_PATH: Final[str] = "/var/snap/opentelemetry-collector/common/otelcol-server-cert.crt"
SERVER_CERT_PRIVATE_KEY_PATH: Final[str] = "/var/snap/opentelemetry-collector/common/otelcol-private-key.key"
RECV_CA_CERT_FOLDER_PATH: Final[str] = "/usr/local/share/ca-certificates/juju_receive-ca-cert"
SERVER_CA_CERT_PATH: Final[str] = "/usr/local/share/ca-certificates/juju_receive-ca-cert/cos-ca.crt"
CONFIG_FOLDER: Final[str] = "/etc/otelcol/config.d"
LOGROTATE_PATH: Final[str] = "/etc/logrotate.d/otelcol"
LOGROTATE_SRC_PATH: Final[str] = "src/logrotate.d/otelcol"
METRICS_RULES_SRC_PATH: Final[str] = "src/prometheus_alert_rules"
METRICS_RULES_DEST_PATH: Final[str] = "prometheus_alert_rules"
LOKI_RULES_SRC_PATH: Final[str] = "src/loki_alert_rules"
LOKI_RULES_DEST_PATH: Final[str] = "loki_alert_rules"
DASHBOARDS_SRC_PATH: Final[str] = "src/grafana_dashboards"
DASHBOARDS_DEST_PATH: Final[str] = "grafana_dashboards"
# NOTE: this file path is hardcoded in src/logrotate.d/otelcol as well
INTERNAL_TELEMETRY_LOG_FILE: Final[str] = "/var/snap/opentelemetry-collector/common/otelcol.log"
# SNAP_COMMON dir: https://snapcraft.io/docs/data-locations#p-94053-system-data
FILE_STORAGE_DIRECTORY: Final[str] = "/var/snap/opentelemetry-collector/common/"

NODE_EXPORTER_TEXTFILE_DIRECTORY: Final[str] = "/var/snap/node-exporter/common/textfile-collector.d"
# Ref: https://github.com/prometheus/node_exporter?tab=readme-ov-file#collectors
NODE_EXPORTER_DISABLED_COLLECTORS: Final[Set[str]] = set()
# NOTE: the flavor-specific `textfile.directory=<dir>` entry is appended at the call
# sites, because the directory differs between the snap and apt installations.
NODE_EXPORTER_ENABLED_COLLECTORS: Final[Set[str]] = {
    "drm",
    "logind",
    "systemd",
    "mountstats",
    "processes",
    "sysctl",
    "textfile",
}

# node-exporter installed from the Ubuntu archive (package-type=apt).
# The deb is configured via its ARGS defaults file so it captures the same
# collectors and listen port as the snap flavor.
NODE_EXPORTER_APT_PACKAGE: Final[str] = "prometheus-node-exporter"
NODE_EXPORTER_APT_SERVICE: Final[str] = "prometheus-node-exporter"
NODE_EXPORTER_APT_DEFAULTS_PATH: Final[str] = "/etc/default/prometheus-node-exporter"
NODE_EXPORTER_APT_TEXTFILE_DIRECTORY: Final[str] = "/var/lib/prometheus/node-exporter"

# node-exporter built in-house and installed from a URL (binary-url config).
# Takes precedence over package-type when set. Unlike the snap and the deb
# there is no packaging, so the charm owns the user, the service and the paths.
NODE_EXPORTER_BINARY_DIR: Final[str] = "/opt/prometheus/node-exporter"
NODE_EXPORTER_BINARY_PATH: Final[str] = "/opt/prometheus/node-exporter/node_exporter"
# Records the URL the installed binary came from, so it is re-downloaded only
# when the binary-url config changes.
NODE_EXPORTER_BINARY_STATE_PATH: Final[str] = "/opt/prometheus/node-exporter/.install-state.json"
NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY: Final[str] = (
    "/opt/prometheus/node-exporter/textfile-collector.d"
)
NODE_EXPORTER_BINARY_SERVICE: Final[str] = "node-exporter"
NODE_EXPORTER_BINARY_UNIT_PATH: Final[str] = "/etc/systemd/system/node-exporter.service"
NODE_EXPORTER_BINARY_USER: Final[str] = "node-exporter"
NODE_EXPORTER_BINARY_GROUP: Final[str] = "node-exporter"
# Pseudo-package name, used only for SingletonSnapManager reference counting so
# the binary flavor is tracked across co-located units like the snap and deb are.
NODE_EXPORTER_BINARY_PACKAGE: Final[str] = "node-exporter-binary"
