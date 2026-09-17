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
