# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Custom Binary Installation Module.

Third counterpart of snap_management.py and apt_management.py, for the
node_exporter built in-house and published as a plain executable (the
`binary-url` config option). Neither the upstream snap nor the Ubuntu archive
deb carries our custom collectors, so this flavor takes precedence over both
whenever a URL is configured.

Unlike the snap and the deb there is no packaging to lean on, so this module
owns the whole lifecycle: a dedicated unprivileged `node-exporter` system user,
the download into /opt/prometheus/node-exporter, a charm-rendered systemd unit,
and a state file recording which URL is currently installed so the binary is
re-downloaded only when the configuration changes.
"""

import http.client
import json
import logging
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Optional
from urllib.error import URLError
from urllib.parse import urlparse

from charmlibs.pathops import LocalPath

from constants import (
    NODE_EXPORTER_BINARY_GROUP,
    NODE_EXPORTER_BINARY_NOLOGIN_SHELL,
    NODE_EXPORTER_BINARY_PATH,
    NODE_EXPORTER_BINARY_STATE_PATH,
    NODE_EXPORTER_BINARY_USER,
)
from node_exporter_flags import Version, parse_version

log = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 300
VERSION_TIMEOUT_SECONDS = 30


class BinaryError(Exception):
    """Base exception for all custom-binary errors."""


class BinaryUrlError(BinaryError):
    """Raised when the configured binary-url is not a usable http(s) URL."""


class BinaryDownloadError(BinaryError):
    """Raised when the node-exporter binary cannot be fetched or installed."""


class BinaryServiceError(BinaryError):
    """Raised when the charm-managed systemd service fails to (re)start."""


def validate_url(url: str) -> None:
    """Check that url is a well-formed http(s) URL.

    Restricting the scheme keeps a config typo from turning into a local file
    read or an unexpected protocol handler.

    Raises:
        BinaryUrlError: if the scheme is not http/https or there is no host.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise BinaryUrlError(f"binary-url must be an http(s) URL, got: {url!r}")


def is_installed(binary_path: str = NODE_EXPORTER_BINARY_PATH) -> bool:
    """Return True if the custom node-exporter binary is present on disk."""
    return LocalPath(binary_path).exists()


def installed_url(state_path: str = NODE_EXPORTER_BINARY_STATE_PATH) -> Optional[str]:
    """Return the URL the installed binary was downloaded from, or None.

    None is returned for a missing, unreadable or malformed state file, which
    makes the caller re-download rather than trust an unidentified binary.
    """
    path = LocalPath(state_path)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text())
    except (ValueError, OSError):
        log.warning(f"ignoring malformed node-exporter state file: {state_path}")
        return None
    if not isinstance(state, dict):
        log.warning(f"ignoring malformed node-exporter state file: {state_path}")
        return None
    url = state.get("url")
    return url if isinstance(url, str) else None


def ensure_user(
    user: str = NODE_EXPORTER_BINARY_USER, group: str = NODE_EXPORTER_BINARY_GROUP
) -> None:
    """Create the unprivileged system user the service runs as, if it is missing.

    Raises:
        BinaryError: if useradd is unavailable or fails.
    """
    try:
        pwd.getpwnam(user)
        return
    except KeyError:
        pass
    log.info(f"creating the {user} system user")
    group_flag = "--user-group" if user == group else f"--gid={group}"
    try:
        subprocess.run(
            [
                "useradd",
                "--system",
                "--no-create-home",
                "--shell",
                NODE_EXPORTER_BINARY_NOLOGIN_SHELL,
                group_flag,
                user,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise BinaryError(f"Failed to create the {user} system user") from e


def download_binary(
    url: str,
    binary_path: str = NODE_EXPORTER_BINARY_PATH,
    state_path: str = NODE_EXPORTER_BINARY_STATE_PATH,
) -> None:
    """Download the node-exporter binary and record the URL it came from.

    The download lands in a temporary file next to the destination and is then
    renamed into place: a partial download never replaces a working binary, and
    the rename succeeds even while the previous binary is executing, because it
    unlinks the old inode instead of writing into it.

    The state file is written only after a successful install, so a failed
    download leaves the previously recorded URL intact.

    Raises:
        BinaryUrlError: if url is not an http(s) URL.
        BinaryDownloadError: if the download or the install fails.
    """
    validate_url(url)
    destination = LocalPath(binary_path)
    log.info(f"downloading the node-exporter binary from {url}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(destination.parent), prefix=".node_exporter.")
    except OSError as e:
        raise BinaryDownloadError(f"Failed to prepare {destination.parent} for download") from e
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_file:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                shutil.copyfileobj(response, tmp_file)
        os.chmod(tmp_name, 0o755)
        os.replace(tmp_name, binary_path)
    except (URLError, http.client.HTTPException, OSError, ValueError) as e:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise BinaryDownloadError(f"Failed to download node-exporter from {url}") from e
    try:
        LocalPath(state_path).write_text(json.dumps({"url": url}) + "\n")
    except OSError as e:
        raise BinaryDownloadError(
            f"node-exporter installed from {url} but the install record could not be "
            f"written to {state_path}"
        ) from e
    log.info(f"installed the node-exporter binary at {binary_path}")


def binary_version(binary_path: str = NODE_EXPORTER_BINARY_PATH) -> Optional[Version]:
    """Return the version the installed binary reports, or None if unknown.

    `node_exporter --version` prints a banner such as
    'node_exporter, version 1.10.2 (branch: HEAD, revision: ...)', on stderr in
    some releases and on stdout in others, so both are searched. None makes the
    flag builder fall back to the conservative collector set.
    """
    try:
        result = subprocess.run(
            [binary_path, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(f"could not determine the node-exporter binary version: {e}")
        return None
    match = re.search(r"version\s+v?(\d+\.\d+(?:\.\d+)?)", f"{result.stdout}\n{result.stderr}")
    return parse_version(match.group(1)) if match else None
