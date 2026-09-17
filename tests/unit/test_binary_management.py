# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: node-exporter installed from a custom-built binary (binary-url)."""

import io
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import binary_management

# The autouse mock_binary_operations fixture (conftest.py, added in Task 4) patches
# these attributes for charm-level tests; capture the real functions at import time
# so this file exercises their own logic.
_real_ensure_user = binary_management.ensure_user
_real_is_installed = binary_management.is_installed
_real_sync_node_exporter_service = binary_management.sync_node_exporter_service
_real_remove = binary_management.remove


def _urlopen_returning(payload: bytes):
    """Build a urlopen mock whose context manager yields payload."""
    response = MagicMock()
    response.__enter__ = MagicMock(return_value=io.BytesIO(payload))
    response.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=response)


# --- URL validation ---------------------------------------------------------


def test_validate_url_accepts_http_and_https():
    # GIVEN well-formed URLs
    # THEN no exception is raised
    binary_management.validate_url("http://example.com/node_exporter")
    binary_management.validate_url("https://example.com/builds/node_exporter-1.2.3")


@pytest.mark.parametrize(
    "url",
    ["", "node_exporter", "file:///tmp/node_exporter", "ftp://example.com/x", "https://"],
)
def test_validate_url_rejects_everything_else(url):
    # GIVEN a URL that is not an http(s) URL with a host
    # THEN a typed error is raised for the charm to turn into BlockedStatus
    with pytest.raises(binary_management.BinaryUrlError):
        binary_management.validate_url(url)


# --- install state ----------------------------------------------------------


def test_is_installed_reflects_the_binary_on_disk(tmp_path):
    binary = tmp_path / "node_exporter"
    # GIVEN no binary on disk
    assert _real_is_installed(str(binary)) is False
    # WHEN the binary exists
    binary.write_bytes(b"ELF")
    # THEN it is reported installed
    assert _real_is_installed(str(binary)) is True


def test_installed_url_reads_the_state_file(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"url": "https://example.com/v1"}))
    # THEN the recorded URL comes back
    assert binary_management.installed_url(str(state)) == "https://example.com/v1"


def test_installed_url_is_none_when_state_is_missing_or_unusable(tmp_path):
    # GIVEN no state file
    assert binary_management.installed_url(str(tmp_path / "missing.json")) is None
    # GIVEN a state file that is not JSON
    broken = tmp_path / "broken.json"
    broken.write_text("not json {{")
    assert binary_management.installed_url(str(broken)) is None
    # GIVEN JSON that is not an object
    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2, 3]")
    assert binary_management.installed_url(str(not_object)) is None
    # GIVEN an object with no url key
    no_url = tmp_path / "nourl.json"
    no_url.write_text(json.dumps({"sha": "abc"}))
    assert binary_management.installed_url(str(no_url)) is None


# --- system user ------------------------------------------------------------


def test_ensure_user_is_a_no_op_when_the_user_exists():
    with (
        patch.object(binary_management.pwd, "getpwnam", return_value=object()),
        patch.object(binary_management.subprocess, "run") as run,
    ):
        _real_ensure_user("node-exporter", "node-exporter")
    # THEN useradd is not invoked
    run.assert_not_called()


def test_ensure_user_creates_an_unprivileged_system_user():
    with (
        patch.object(binary_management.pwd, "getpwnam", side_effect=KeyError("nope")),
        patch.object(binary_management.subprocess, "run") as run,
    ):
        _real_ensure_user("node-exporter", "node-exporter")
    argv = run.call_args.args[0]
    # THEN a system user with no home and no login shell is created
    assert argv[0] == "useradd"
    assert "--system" in argv
    assert "--no-create-home" in argv
    assert "--user-group" in argv
    assert argv[-1] == "node-exporter"


def test_ensure_user_raises_binary_error_when_useradd_fails():
    with (
        patch.object(binary_management.pwd, "getpwnam", side_effect=KeyError("nope")),
        patch.object(
            binary_management.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "useradd"),
        ),
    ):
        with pytest.raises(binary_management.BinaryError):
            _real_ensure_user("node-exporter", "node-exporter")


# --- download ---------------------------------------------------------------


def test_download_binary_writes_an_executable_and_records_the_url(tmp_path):
    binary = tmp_path / "node_exporter"
    state = tmp_path / "state.json"
    # GIVEN a URL serving the binary
    with patch.object(binary_management.urllib.request, "urlopen", _urlopen_returning(b"ELFDATA")):
        # WHEN downloading
        binary_management.download_binary(
            "https://example.com/v1", binary_path=str(binary), state_path=str(state)
        )
    # THEN the payload is on disk and executable
    assert binary.read_bytes() == b"ELFDATA"
    assert binary.stat().st_mode & 0o111
    # AND the URL it came from is recorded
    assert json.loads(state.read_text())["url"] == "https://example.com/v1"


def test_download_binary_creates_the_install_directory(tmp_path):
    binary = tmp_path / "opt" / "prometheus" / "node-exporter" / "node_exporter"
    state = binary.parent / "state.json"
    with patch.object(binary_management.urllib.request, "urlopen", _urlopen_returning(b"ELF")):
        binary_management.download_binary(
            "https://example.com/v1", binary_path=str(binary), state_path=str(state)
        )
    assert binary.exists()


def test_download_binary_rejects_a_non_http_url(tmp_path):
    with pytest.raises(binary_management.BinaryUrlError):
        binary_management.download_binary(
            "file:///etc/passwd",
            binary_path=str(tmp_path / "node_exporter"),
            state_path=str(tmp_path / "state.json"),
        )


def test_download_binary_keeps_the_old_binary_when_the_fetch_fails(tmp_path):
    binary = tmp_path / "node_exporter"
    state = tmp_path / "state.json"
    # GIVEN a working binary already installed
    binary.write_bytes(b"OLD")
    state.write_text(json.dumps({"url": "https://example.com/v1"}))
    # WHEN the new download fails mid-flight
    with patch.object(
        binary_management.urllib.request, "urlopen", side_effect=OSError("connection reset")
    ):
        with pytest.raises(binary_management.BinaryDownloadError):
            binary_management.download_binary(
                "https://example.com/v2", binary_path=str(binary), state_path=str(state)
            )
    # THEN the old binary and its recorded URL are untouched
    assert binary.read_bytes() == b"OLD"
    assert json.loads(state.read_text())["url"] == "https://example.com/v1"
    # AND no temporary download file is left behind (an autouse fixture in
    # conftest.py also populates tmp_path with an unrelated directory, so this
    # checks for the specific leftover rather than an exact directory listing)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".node_exporter.")]
    assert leftovers == []


def test_download_binary_raises_when_the_directory_cannot_be_prepared(tmp_path):
    binary = tmp_path / "sub" / "node_exporter"
    state = tmp_path / "sub" / "state.json"
    # GIVEN the destination directory cannot be created (e.g. permission denied)
    with patch.object(
        binary_management.LocalPath, "mkdir", side_effect=OSError("permission denied")
    ):
        # THEN a typed error is raised instead of a bare OSError
        with pytest.raises(binary_management.BinaryDownloadError):
            binary_management.download_binary(
                "https://example.com/v1", binary_path=str(binary), state_path=str(state)
            )
    # AND nothing was written
    assert not binary.exists()


def test_download_binary_raises_when_a_temp_file_cannot_be_created(tmp_path):
    binary = tmp_path / "node_exporter"
    state = tmp_path / "state.json"
    # GIVEN a working binary already installed
    binary.write_bytes(b"OLD")
    state.write_text(json.dumps({"url": "https://example.com/v1"}))
    # WHEN the temp file for the new download cannot be created (e.g. disk full)
    with patch.object(
        binary_management.tempfile, "mkstemp", side_effect=OSError("no space left on device")
    ):
        # THEN a typed error is raised instead of a bare OSError
        with pytest.raises(binary_management.BinaryDownloadError):
            binary_management.download_binary(
                "https://example.com/v2", binary_path=str(binary), state_path=str(state)
            )
    # AND the old binary and its recorded URL are untouched
    assert binary.read_bytes() == b"OLD"
    assert json.loads(state.read_text())["url"] == "https://example.com/v1"


def test_download_binary_raises_a_distinct_error_when_the_state_file_cannot_be_written(tmp_path):
    binary = tmp_path / "node_exporter"
    state = tmp_path / "state.json"
    # GIVEN a URL serving the binary but a state file that cannot be written
    # (e.g. a read-only mount)
    with (
        patch.object(binary_management.urllib.request, "urlopen", _urlopen_returning(b"ELF")),
        patch.object(
            binary_management.LocalPath,
            "write_text",
            side_effect=OSError("read-only file system"),
        ),
    ):
        # THEN a typed error is raised instead of a bare OSError
        with pytest.raises(binary_management.BinaryDownloadError):
            binary_management.download_binary(
                "https://example.com/v1", binary_path=str(binary), state_path=str(state)
            )
    # AND the binary was still installed even though the install record was not
    assert binary.read_bytes() == b"ELF"


# --- version ----------------------------------------------------------------


def test_binary_version_parses_the_version_banner():
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="node_exporter, version 1.10.2 (branch: HEAD)\n"
    )
    with patch.object(binary_management.subprocess, "run", return_value=completed):
        assert binary_management.binary_version("/opt/x/node_exporter") == (1, 10, 2)


def test_binary_version_is_none_when_the_binary_cannot_be_run():
    with patch.object(binary_management.subprocess, "run", side_effect=OSError("ENOEXEC")):
        # THEN callers fall back to the conservative collector set
        assert binary_management.binary_version("/opt/x/node_exporter") is None


def test_binary_version_is_none_when_the_output_has_no_version():
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="hello", stderr="")
    with patch.object(binary_management.subprocess, "run", return_value=completed):
        assert binary_management.binary_version("/opt/x/node_exporter") is None


# --- systemd unit rendering -------------------------------------------------


def test_build_unit_file_runs_the_binary_with_the_charm_collectors():
    # WHEN rendering the unit for a recent binary
    unit = binary_management.build_unit_file(
        9100,
        binary_path="/opt/prometheus/node-exporter/node_exporter",
        user="node-exporter",
        group="node-exporter",
        textfile_directory="/opt/prometheus/node-exporter/textfile-collector.d",
        version=(1, 10, 2),
    )
    # THEN it is a valid-looking unit with the three sections
    assert "[Unit]" in unit and "[Service]" in unit and "[Install]" in unit
    # AND ExecStart runs the binary with the charm's collectors and port
    assert "ExecStart=/opt/prometheus/node-exporter/node_exporter " in unit
    assert "--collector.drm" in unit
    assert "--collector.textfile.directory=/opt/prometheus/node-exporter/textfile-collector.d" in unit
    assert "--web.listen-address=:9100" in unit
    # AND it runs unprivileged and restarts on failure
    assert "User=node-exporter" in unit
    assert "Group=node-exporter" in unit
    assert "Restart=always" in unit
    assert "WantedBy=multi-user.target" in unit


def test_build_unit_file_honours_the_port_override():
    unit = binary_management.build_unit_file(9200, version=(1, 10, 2))
    assert "--web.listen-address=:9200" in unit


def test_build_unit_file_gates_collectors_by_version():
    # GIVEN a binary built from an old node_exporter base
    unit = binary_management.build_unit_file(9100, version=(1, 3, 1))
    # THEN flags it would not recognise are absent
    assert "--collector.drm" not in unit
    assert "--collector.systemd" in unit


def test_build_unit_file_is_deterministic():
    assert binary_management.build_unit_file(9100, version=(1, 10, 2)) == (
        binary_management.build_unit_file(9100, version=(1, 10, 2))
    )


# --- service sync -----------------------------------------------------------


def _sync(tmp_path, url="https://example.com/v1", port=9100, force_restart=False, running=True,
          installed_url=None, binary_exists=False, payload=b"ELF"):
    """Run sync_node_exporter_service with systemd, useradd and urlopen mocked.

    Returns (restart_mock, unit_path, binary_path, state_path).
    """
    binary = tmp_path / "node_exporter"
    state = tmp_path / "state.json"
    unit = tmp_path / "node-exporter.service"
    if binary_exists:
        binary.write_bytes(b"OLD")
    if installed_url is not None:
        state.write_text(json.dumps({"url": installed_url}))
    with (
        patch.object(binary_management.systemd, "service_restart") as restart,
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(binary_management.systemd, "service_running", return_value=running),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen", _urlopen_returning(payload)),
    ):
        _real_sync_node_exporter_service(
            url,
            port,
            binary_path=str(binary),
            state_path=str(state),
            unit_path=str(unit),
            service="node-exporter",
            force_restart=force_restart,
        )
    return restart, unit, binary, state


def test_sync_downloads_writes_the_unit_and_starts_the_service(tmp_path):
    # GIVEN nothing installed yet
    restart, unit, binary, state = _sync(tmp_path)
    # THEN the binary is fetched, the unit written and the service started
    assert binary.read_bytes() == b"ELF"
    assert "ExecStart=" in unit.read_text()
    assert json.loads(state.read_text())["url"] == "https://example.com/v1"
    restart.assert_called_once()


def test_sync_creates_the_service_user(tmp_path):
    binary = tmp_path / "node_exporter"
    with (
        patch.object(binary_management.systemd, "service_restart"),
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(binary_management.systemd, "service_running", return_value=True),
        patch.object(binary_management, "ensure_user") as ensure_user,
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen", _urlopen_returning(b"ELF")),
    ):
        _real_sync_node_exporter_service(
            "https://example.com/v1",
            9100,
            binary_path=str(binary),
            state_path=str(tmp_path / "state.json"),
            unit_path=str(tmp_path / "node-exporter.service"),
        )
    ensure_user.assert_called_once()


def test_sync_skips_the_download_when_the_url_is_unchanged(tmp_path):
    # GIVEN the binary already installed from this exact URL
    binary = tmp_path / "node_exporter"
    binary.write_bytes(b"OLD")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"url": "https://example.com/v1"}))
    unit = tmp_path / "node-exporter.service"
    unit.write_text(
        binary_management.build_unit_file(
            9100, binary_path=str(binary), version=(1, 10, 2)
        )
    )
    with (
        patch.object(binary_management.systemd, "service_restart") as restart,
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(binary_management.systemd, "service_running", return_value=True),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen") as urlopen,
    ):
        _real_sync_node_exporter_service(
            "https://example.com/v1",
            9100,
            binary_path=str(binary),
            state_path=str(state),
            unit_path=str(unit),
        )
    # THEN nothing is fetched and the service is left alone
    urlopen.assert_not_called()
    restart.assert_not_called()
    assert binary.read_bytes() == b"OLD"


def test_sync_redownloads_when_the_url_changes(tmp_path):
    # GIVEN a binary installed from a different URL
    restart, _, binary, state = _sync(
        tmp_path, url="https://example.com/v2", installed_url="https://example.com/v1",
        binary_exists=True, payload=b"NEW",
    )
    # THEN the new build replaces the old one and the service restarts
    assert binary.read_bytes() == b"NEW"
    assert json.loads(state.read_text())["url"] == "https://example.com/v2"
    restart.assert_called_once()


def test_sync_redownloads_when_the_binary_is_missing(tmp_path):
    # GIVEN the state file claims this URL but the binary is gone
    restart, _, binary, _ = _sync(
        tmp_path, installed_url="https://example.com/v1", binary_exists=False
    )
    assert binary.exists()
    restart.assert_called_once()


def test_sync_restarts_when_only_the_port_changed(tmp_path):
    # GIVEN an installed binary and a unit file rendered for port 9100
    binary = tmp_path / "node_exporter"
    binary.write_bytes(b"OLD")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"url": "https://example.com/v1"}))
    unit = tmp_path / "node-exporter.service"
    unit.write_text(binary_management.build_unit_file(9100, binary_path=str(binary), version=(1, 10, 2)))
    with (
        patch.object(binary_management.systemd, "service_restart") as restart,
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload") as daemon_reload,
        patch.object(binary_management.systemd, "service_running", return_value=True),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen") as urlopen,
    ):
        # WHEN syncing with a different port
        _real_sync_node_exporter_service(
            "https://example.com/v1", 9200,
            binary_path=str(binary), state_path=str(state), unit_path=str(unit),
        )
    # THEN nothing is re-downloaded but the unit is rewritten and reloaded
    urlopen.assert_not_called()
    assert "--web.listen-address=:9200" in unit.read_text()
    daemon_reload.assert_called_once()
    restart.assert_called_once()


def test_sync_restarts_when_the_service_is_not_running(tmp_path):
    # GIVEN everything up to date but the service down
    binary = tmp_path / "node_exporter"
    binary.write_bytes(b"OLD")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"url": "https://example.com/v1"}))
    unit = tmp_path / "node-exporter.service"
    unit.write_text(binary_management.build_unit_file(9100, binary_path=str(binary), version=(1, 10, 2)))
    with (
        patch.object(binary_management.systemd, "service_restart") as restart,
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(binary_management.systemd, "service_running", return_value=False),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen") as urlopen,
    ):
        _real_sync_node_exporter_service(
            "https://example.com/v1", 9100,
            binary_path=str(binary), state_path=str(state), unit_path=str(unit),
        )
    urlopen.assert_not_called()
    restart.assert_called_once()


def test_sync_force_restart(tmp_path):
    """Used right after a competing flavor was uninstalled and freed the port."""
    binary = tmp_path / "node_exporter"
    binary.write_bytes(b"OLD")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"url": "https://example.com/v1"}))
    unit = tmp_path / "node-exporter.service"
    unit.write_text(binary_management.build_unit_file(9100, binary_path=str(binary), version=(1, 10, 2)))
    with (
        patch.object(binary_management.systemd, "service_restart") as restart,
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(binary_management.systemd, "service_running", return_value=True),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen"),
    ):
        _real_sync_node_exporter_service(
            "https://example.com/v1", 9100,
            binary_path=str(binary), state_path=str(state), unit_path=str(unit),
            force_restart=True,
        )
    restart.assert_called_once()


def test_sync_raises_binary_service_error_on_restart_failure(tmp_path):
    binary = tmp_path / "node_exporter"
    with (
        patch.object(
            binary_management.systemd,
            "service_restart",
            side_effect=binary_management.systemd.SystemdError("boom"),
        ),
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(binary_management.systemd, "service_running", return_value=True),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen", _urlopen_returning(b"ELF")),
    ):
        # THEN a typed error is raised for the charm to turn into BlockedStatus
        with pytest.raises(binary_management.BinaryServiceError):
            _real_sync_node_exporter_service(
                "https://example.com/v1", 9100,
                binary_path=str(binary),
                state_path=str(tmp_path / "state.json"),
                unit_path=str(tmp_path / "node-exporter.service"),
            )


def test_sync_raises_binary_service_error_when_daemon_reload_raises_oserror(tmp_path):
    """systemd._systemctl only converts CalledProcessError; a bare OSError (e.g.

    systemctl missing from PATH) must still surface as BinaryServiceError, not OSError.
    """
    binary = tmp_path / "node_exporter"
    with (
        patch.object(
            binary_management.systemd,
            "daemon_reload",
            side_effect=OSError("systemctl not found"),
        ),
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "service_restart"),
        patch.object(binary_management.systemd, "service_running", return_value=True),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen", _urlopen_returning(b"ELF")),
    ):
        # THEN a typed error is raised, not the bare OSError
        with pytest.raises(binary_management.BinaryServiceError):
            _real_sync_node_exporter_service(
                "https://example.com/v1", 9100,
                binary_path=str(binary),
                state_path=str(tmp_path / "state.json"),
                unit_path=str(tmp_path / "node-exporter.service"),
            )


def test_sync_raises_binary_service_error_when_restart_raises_oserror(tmp_path):
    binary = tmp_path / "node_exporter"
    with (
        patch.object(
            binary_management.systemd,
            "service_restart",
            side_effect=OSError("systemctl not found"),
        ),
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(binary_management.systemd, "service_running", return_value=True),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen", _urlopen_returning(b"ELF")),
    ):
        # THEN a typed error is raised, not the bare OSError
        with pytest.raises(binary_management.BinaryServiceError):
            _real_sync_node_exporter_service(
                "https://example.com/v1", 9100,
                binary_path=str(binary),
                state_path=str(tmp_path / "state.json"),
                unit_path=str(tmp_path / "node-exporter.service"),
            )


def test_sync_raises_binary_service_error_when_unit_file_write_fails(tmp_path):
    """A read-only /etc, ENOSPC, etc. writing the unit file must not raise a bare OSError."""
    binary = tmp_path / "node_exporter"
    state = tmp_path / "state.json"
    # GIVEN the binary is already installed from the configured URL, so only the
    # (missing) unit file needs writing, isolating the failure from the download.
    binary.write_bytes(b"OLD")
    state.write_text(json.dumps({"url": "https://example.com/v1"}))
    with (
        patch.object(binary_management.systemd, "service_restart"),
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(binary_management.systemd, "service_running", return_value=True),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen", _urlopen_returning(b"ELF")),
        patch.object(
            binary_management.LocalPath,
            "write_text",
            side_effect=OSError("read-only file system"),
        ),
    ):
        # THEN a typed error is raised, not the bare OSError
        with pytest.raises(binary_management.BinaryServiceError):
            _real_sync_node_exporter_service(
                "https://example.com/v1", 9100,
                binary_path=str(binary),
                state_path=str(state),
                unit_path=str(tmp_path / "node-exporter.service"),
            )


def test_sync_restarts_when_service_running_raises_oserror(tmp_path):
    """When the running-state cannot be determined, the safe direction is to restart."""
    binary = tmp_path / "node_exporter"
    binary.write_bytes(b"OLD")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"url": "https://example.com/v1"}))
    unit = tmp_path / "node-exporter.service"
    unit.write_text(binary_management.build_unit_file(9100, binary_path=str(binary), version=(1, 10, 2)))
    with (
        patch.object(binary_management.systemd, "service_restart") as restart,
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(
            binary_management.systemd,
            "service_running",
            side_effect=OSError("systemctl not found"),
        ),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management, "binary_version", return_value=(1, 10, 2)),
        patch.object(binary_management.urllib.request, "urlopen") as urlopen,
    ):
        # WHEN everything else is unchanged but the running-state check itself fails
        _real_sync_node_exporter_service(
            "https://example.com/v1", 9100,
            binary_path=str(binary), state_path=str(state), unit_path=str(unit),
        )
    # THEN nothing is re-downloaded but the service is restarted rather than left alone
    urlopen.assert_not_called()
    restart.assert_called_once()


def test_sync_propagates_download_failure(tmp_path):
    with (
        patch.object(binary_management.systemd, "service_restart"),
        patch.object(binary_management.systemd, "service_enable"),
        patch.object(binary_management.systemd, "daemon_reload"),
        patch.object(binary_management.systemd, "service_running", return_value=True),
        patch.object(binary_management, "ensure_user"),
        patch.object(binary_management, "is_installed", _real_is_installed),
        patch.object(binary_management.urllib.request, "urlopen", side_effect=OSError("404")),
    ):
        with pytest.raises(binary_management.BinaryDownloadError):
            _real_sync_node_exporter_service(
                "https://example.com/v1", 9100,
                binary_path=str(tmp_path / "node_exporter"),
                state_path=str(tmp_path / "state.json"),
                unit_path=str(tmp_path / "node-exporter.service"),
            )


def test_sync_rejects_a_non_http_url(tmp_path):
    with pytest.raises(binary_management.BinaryUrlError):
        _real_sync_node_exporter_service(
            "file:///etc/passwd", 9100,
            binary_path=str(tmp_path / "node_exporter"),
            state_path=str(tmp_path / "state.json"),
            unit_path=str(tmp_path / "node-exporter.service"),
        )


# --- removal ----------------------------------------------------------------


def test_remove_stops_the_service_and_deletes_everything(tmp_path):
    # GIVEN an installed binary, state file and unit file
    directory = tmp_path / "node-exporter"
    directory.mkdir()
    (directory / "node_exporter").write_bytes(b"ELF")
    (directory / "state.json").write_text("{}")
    unit = tmp_path / "node-exporter.service"
    unit.write_text("[Unit]\n")
    with (
        patch.object(binary_management.systemd, "service_stop") as stop,
        patch.object(binary_management.systemd, "service_disable") as disable,
        patch.object(binary_management.systemd, "daemon_reload") as daemon_reload,
    ):
        # WHEN removing
        _real_remove(
            unit_path=str(unit), service="node-exporter", directory=str(directory)
        )
    # THEN the service is stopped and disabled, and the files are gone
    stop.assert_called_once_with("node-exporter")
    disable.assert_called_once_with("node-exporter")
    daemon_reload.assert_called_once()
    assert not unit.exists()
    assert not directory.exists()


def test_remove_is_best_effort_when_systemd_fails(tmp_path):
    """Removal runs from the remove hook, where raising would wedge the unit."""
    directory = tmp_path / "node-exporter"
    directory.mkdir()
    unit = tmp_path / "node-exporter.service"
    unit.write_text("[Unit]\n")
    with (
        patch.object(
            binary_management.systemd,
            "service_stop",
            side_effect=binary_management.systemd.SystemdError("no such unit"),
        ),
        patch.object(binary_management.systemd, "service_disable"),
        patch.object(binary_management.systemd, "daemon_reload"),
    ):
        # THEN no exception propagates
        _real_remove(
            unit_path=str(unit), service="node-exporter", directory=str(directory)
        )
    # AND the files are still cleaned up
    assert not unit.exists()
    assert not directory.exists()


def test_remove_is_best_effort_when_service_stop_raises_oserror(tmp_path):
    """systemd._systemctl only converts CalledProcessError; a bare OSError (e.g.

    systemctl missing from PATH) from service_stop must not escape remove() either.
    """
    directory = tmp_path / "node-exporter"
    directory.mkdir()
    (directory / "node_exporter").write_bytes(b"ELF")
    unit = tmp_path / "node-exporter.service"
    unit.write_text("[Unit]\n")
    with (
        patch.object(
            binary_management.systemd,
            "service_stop",
            side_effect=OSError("systemctl not found"),
        ),
        patch.object(binary_management.systemd, "service_disable"),
        patch.object(binary_management.systemd, "daemon_reload"),
    ):
        # THEN no exception propagates
        _real_remove(
            unit_path=str(unit), service="node-exporter", directory=str(directory)
        )
    # AND the files are still cleaned up
    assert not unit.exists()
    assert not directory.exists()


def test_remove_tolerates_nothing_being_installed(tmp_path):
    with (
        patch.object(binary_management.systemd, "service_stop"),
        patch.object(binary_management.systemd, "service_disable"),
        patch.object(binary_management.systemd, "daemon_reload"),
    ):
        # THEN no exception propagates for absent paths
        _real_remove(
            unit_path=str(tmp_path / "absent.service"),
            service="node-exporter",
            directory=str(tmp_path / "absent"),
        )
