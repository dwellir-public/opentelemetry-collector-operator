# Custom node-exporter binary (`binary-url`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `binary-url` config option that installs node-exporter from a custom-built executable under a charm-managed systemd service, taking precedence over the existing `package-type` (apt/snap) sources.

**Architecture:** node-exporter already has two interchangeable *flavors* behind one config option: the `prometheus-node-exporter` deb (`src/apt_management.py`) and the `node-exporter` snap (`src/snap_management.py`). This adds a third, `binary`, in a new `src/binary_management.py` that mirrors the apt module's shape. Collector-flag construction, currently private to `apt_management`, moves to a shared `src/node_exporter_flags.py` so the deb and the binary stay in lockstep. `src/charm.py` grows a `_node_exporter_flavor` property (binary wins when `binary-url` is non-empty) and its single-flavor sync becomes a three-way dispatch; the existing `SingletonSnapManager` reference counting is reused unchanged by registering the binary under the pseudo-package name `node-exporter-binary`.

**Tech Stack:** Python 3.10+, `ops` (Juju operator framework), `ops.testing` Scenario (`Context`/`State`), pytest, `charmlibs.pathops.LocalPath`, `charms.operator_libs_linux.v1.systemd`, `urllib.request` (stdlib), tox (`tox -e unit`, `tox -e lint`, `tox -e static`).

**Spec:** `docs/superpowers/specs/2026-09-17-node-exporter-binary-url.md`

## Global Constraints

- Ruff config (`pyproject.toml`): `line-length = 99`, docstring convention `google`, rules `E,W,F,C,N,R,D` — **every module, class, function and method in `src/` needs a docstring**. Tests are exempt from `D100`–`D103`.
- Copyright header on every new file in `src/` and `tests/`, matching existing 2026 files:
  ```python
  # Copyright 2026 Canonical Ltd.
  # See LICENSE file for licensing details.
  ```
- Python 3.10 target for pyright (`pythonVersion = "3.10"`), `pythonPlatform = "All"`. Use `typing.Optional` / `typing.Tuple` / `typing.List`, not `X | None`, to match the surrounding code.
- All filesystem paths live in `src/constants.py` as `Final[str]`; no path literals in `charm.py` or the management modules.
- **No new runtime dependencies.** `pyproject.toml` `dependencies` stays untouched — use the stdlib (`urllib.request`, `json`, `subprocess`, `pwd`, `tempfile`, `shutil`).
- **Hook failures are forbidden for workload problems.** A download or service failure must return an error string that the reconciler turns into `BlockedStatus`, never raise out of the hook. This is a hard-won rule in this charm — see commit `99aeea7` ("stop crashing on service failure").
- Tests are BDD-commented in the existing house style: `# GIVEN` / `# WHEN` / `# THEN` / `# AND`.
- Run verification with `tox` from the repo root. Single test: `uv run --frozen --isolated --extra=dev pytest tests/unit/<file>::<test> -v` (or `tox -e unit -- tests/unit/<file>::<test>`).
- Config option name is exactly `binary-url` (hyphen, matching `package-type`). Default `""`.
- Install paths, fixed by the spec: binary `/opt/prometheus/node-exporter/node_exporter`, textfile dir `/opt/prometheus/node-exporter/textfile-collector.d`, state file `/opt/prometheus/node-exporter/.install-state.json`, unit file `/etc/systemd/system/node-exporter.service`, service name `node-exporter`, system user and group `node-exporter`.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `src/node_exporter_flags.py` | Shared `node_exporter` CLI flag construction + version parsing. Used by both the apt and binary flavors. No I/O. |
| `src/binary_management.py` | The whole custom-binary lifecycle: URL validation, system user, download + state file, systemd unit rendering, service sync, removal. Counterpart of `apt_management.py`. |
| `tests/unit/test_node_exporter_flags.py` | Unit tests for the shared flag builder. |
| `tests/unit/test_binary_management.py` | Unit tests for `binary_management`, against `tmp_path` with systemd/subprocess/urlopen mocked. |
| `tests/unit/test_binary_url_config.py` | Charm-level tests for the config option: validation, precedence over `package-type`. |
| `tests/unit/test_binary_flavor_switching.py` | Charm-level tests for switching to/from the binary flavor and the cross-flavor conflict. |
| `tests/integration/test_binary_url.py` | Deploys the charm with a `binary-url` and asserts the service serves metrics; switches back to apt. |

**Modified**

| File | Change |
|---|---|
| `src/constants.py` | Add the `NODE_EXPORTER_BINARY_*` constants. |
| `src/apt_management.py` | Delegate flag building and version parsing to `node_exporter_flags`; drop the now-duplicated private helpers. Public API unchanged. |
| `src/charm.py` | `_node_exporter_flavor` property; three-way sync dispatch; generalised flavor-conflict check; binary-aware textfile dir, install hook, remove hook. |
| `charmcraft.yaml` | Add the `binary-url` config option; note the precedence in `package-type`'s description. |
| `tests/unit/conftest.py` | `mock_binary_operations` autouse fixture; patch the binary textfile dir. |
| `README.md` | Short "node-exporter" section documenting the three flavors. |

---

## Task 1: Extract shared node-exporter flag building

The deb and the custom binary both launch `node_exporter` with command-line
flags, and both must use the charm's collector set. That logic currently lives
inside `apt_management.build_node_exporter_args`. Move it out before adding a
second caller, so the two flavors cannot drift.

This is a behaviour-preserving refactor: `tests/unit/test_apt_management.py`
must keep passing untouched.

**Files:**
- Create: `src/node_exporter_flags.py`
- Create: `tests/unit/test_node_exporter_flags.py`
- Modify: `src/apt_management.py` (imports, delete `NODE_EXPORTER_COLLECTOR_MIN_VERSIONS` and `_parse_version`, rewrite `build_node_exporter_args`)

**Interfaces:**
- Consumes: `constants.NODE_EXPORTER_ENABLED_COLLECTORS`, `constants.NODE_EXPORTER_DISABLED_COLLECTORS` (both already exist).
- Produces, for Tasks 2–3:
  - `node_exporter_flags.Version = Tuple[int, int, int]`
  - `node_exporter_flags.parse_version(raw: Optional[str]) -> Optional[Version]`
  - `node_exporter_flags.build_collector_flags(port: int, textfile_directory: str, version: Optional[Version] = None) -> List[str]`
  - `node_exporter_flags.COLLECTOR_MIN_VERSIONS: Dict[str, Version]`
  - `apt_management._parse_version` stays importable (it is re-bound to `parse_version`) because `tests/unit/test_apt_management.py` calls it directly.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_node_exporter_flags.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tox -e unit -- tests/unit/test_node_exporter_flags.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'node_exporter_flags'`

- [ ] **Step 3: Write the module**

Create `src/node_exporter_flags.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `tox -e unit -- tests/unit/test_node_exporter_flags.py`
Expected: PASS (7 tests)

- [ ] **Step 5: Rewire `apt_management` onto the shared module**

In `src/apt_management.py`:

Replace the `import re` line and the `constants` import block with:

```python
import logging
from typing import Optional, Tuple

import charms.operator_libs_linux.v0.apt as apt
import charms.operator_libs_linux.v1.systemd as systemd
from charmlibs.pathops import LocalPath

from constants import (
    NODE_EXPORTER_APT_DEFAULTS_PATH,
    NODE_EXPORTER_APT_PACKAGE,
    NODE_EXPORTER_APT_SERVICE,
    NODE_EXPORTER_APT_TEXTFILE_DIRECTORY,
)
from node_exporter_flags import build_collector_flags
from node_exporter_flags import parse_version as _parse_version
```

Delete the whole `NODE_EXPORTER_COLLECTOR_MIN_VERSIONS` dict and the `_parse_version` function definition (the import above replaces it; `tests/unit/test_apt_management.py` calls `apt_management._parse_version` and must keep working).

Replace the body of `build_node_exporter_args` with:

```python
def build_node_exporter_args(port: int, version: Optional[Tuple[int, int, int]] = None) -> str:
    """Build the ARGS line for /etc/default/prometheus-node-exporter.

    The deb's systemd unit runs `prometheus-node-exporter $ARGS`. The flags
    themselves are built by node_exporter_flags, shared with the custom-binary
    flavor, so both stay configured identically.
    """
    flags = build_collector_flags(port, NODE_EXPORTER_APT_TEXTFILE_DIRECTORY, version)
    return f'ARGS="{" ".join(flags)}"'
```

Also trim the now-stale module docstring paragraph about version filtering down to a pointer:

```
Unlike the snap (pinned to a charm-chosen revision), the deb version depends
on the machine's Ubuntu release (e.g. 1.3.1 on jammy, 1.7.0 on noble), so
collector flags that the installed version does not know are filtered out by
node_exporter_flags — node_exporter exits on unknown flags, which would leave
the service down.
```

- [ ] **Step 6: Run the full unit suite to verify nothing regressed**

Run: `tox -e unit`
Expected: PASS — in particular every test in `tests/unit/test_apt_management.py`, unmodified.

- [ ] **Step 7: Lint and type-check**

Run: `tox -e lint && tox -e static`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/node_exporter_flags.py src/apt_management.py tests/unit/test_node_exporter_flags.py
git commit -m "refactor: extract shared node-exporter flag building

The custom-binary flavor needs the same collector flags as the deb.
Move the builder out of apt_management so the two cannot drift.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Binary acquisition — constants, URL validation, system user, download

The first half of `binary_management`: everything needed to get a trusted-by-
the-operator executable onto disk under a dedicated user, and to know later
whether it needs re-fetching.

The download goes to a temporary file in the destination directory and is then
`os.replace`d into place. That matters for two reasons: a half-finished
download never replaces a working binary, and `os.replace` unlinks the old
inode rather than writing into it, so it succeeds even while the old binary is
still executing (writing into a running executable would fail with `ETXTBSY`).

**Files:**
- Modify: `src/constants.py` (append the `NODE_EXPORTER_BINARY_*` block)
- Create: `src/binary_management.py`
- Create: `tests/unit/test_binary_management.py`

**Interfaces:**
- Consumes: `node_exporter_flags.parse_version` (Task 1).
- Produces, for Task 3 and Task 5:
  - `binary_management.BinaryError(Exception)`, and subclasses `BinaryUrlError`, `BinaryDownloadError`, `BinaryServiceError`
  - `binary_management.validate_url(url: str) -> None`
  - `binary_management.is_installed(binary_path: str = NODE_EXPORTER_BINARY_PATH) -> bool`
  - `binary_management.installed_url(state_path: str = NODE_EXPORTER_BINARY_STATE_PATH) -> Optional[str]`
  - `binary_management.ensure_user(user: str = ..., group: str = ...) -> None`
  - `binary_management.download_binary(url: str, binary_path: str = ..., state_path: str = ...) -> None`
  - `binary_management.binary_version(binary_path: str = ...) -> Optional[Version]`
- Produces, for Task 5 (constants):
  - `NODE_EXPORTER_BINARY_DIR`, `NODE_EXPORTER_BINARY_PATH`, `NODE_EXPORTER_BINARY_STATE_PATH`, `NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY`, `NODE_EXPORTER_BINARY_SERVICE`, `NODE_EXPORTER_BINARY_UNIT_PATH`, `NODE_EXPORTER_BINARY_USER`, `NODE_EXPORTER_BINARY_GROUP`, `NODE_EXPORTER_BINARY_PACKAGE`

- [ ] **Step 1: Add the constants**

Append to `src/constants.py`, after the existing apt block:

```python
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
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_binary_management.py`:

```python
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
    # AND no temporary file is left behind
    assert sorted(p.name for p in tmp_path.iterdir()) == ["node_exporter", "state.json"]


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `tox -e unit -- tests/unit/test_binary_management.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'binary_management'`

- [ ] **Step 4: Write the module**

Create `src/binary_management.py`:

```python
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
                "/usr/sbin/nologin",
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    log.info(f"downloading the node-exporter binary from {url}")
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(destination.parent), prefix=".node_exporter.")
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
    LocalPath(state_path).write_text(json.dumps({"url": url}) + "\n")
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `tox -e unit -- tests/unit/test_binary_management.py`
Expected: PASS (15 tests)

- [ ] **Step 6: Lint and type-check**

Run: `tox -e lint && tox -e static`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/constants.py src/binary_management.py tests/unit/test_binary_management.py
git commit -m "feat: add binary management module for custom node-exporter

Fetches a prebuilt node_exporter over http(s) into
/opt/prometheus/node-exporter, under a dedicated system user, recording
the source URL so it is only re-downloaded when the URL changes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Binary service — systemd unit, sync, removal

The second half of `binary_management`: render the unit that runs the binary,
converge the machine onto the configured URL and port, and tear it all down
again on a flavor switch or charm removal.

The restart policy mirrors `apt_management.sync_node_exporter_config`: restart
when something actually changed, when the service is down (it may have failed
to bind the port while another flavor still held it mid-switch), or when the
caller forces it after removing a competing flavor.

**Files:**
- Modify: `src/binary_management.py` (add `build_unit_file`, `sync_node_exporter_service`, `remove`)
- Modify: `tests/unit/test_binary_management.py` (append the tests below)

**Interfaces:**
- Consumes: everything Task 2 produced, plus `node_exporter_flags.build_collector_flags` (Task 1) and `charms.operator_libs_linux.v1.systemd`.
- Produces, for Task 5:
  - `binary_management.build_unit_file(port: int, binary_path: str = ..., user: str = ..., group: str = ..., textfile_directory: str = ..., version: Optional[Version] = None) -> str`
  - `binary_management.sync_node_exporter_service(url: str, port: int, binary_path: str = ..., state_path: str = ..., unit_path: str = ..., service: str = ..., force_restart: bool = False) -> None`
  - `binary_management.remove(unit_path: str = ..., service: str = ..., directory: str = ...) -> None`
  - The module-level attribute `binary_management.systemd`, which `tests/unit/conftest.py` patches.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_binary_management.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tox -e unit -- tests/unit/test_binary_management.py`
Expected: FAIL with `AttributeError: module 'binary_management' has no attribute 'build_unit_file'` (and the systemd patches failing, since `binary_management.systemd` does not exist yet).

- [ ] **Step 3: Extend the module**

In `src/binary_management.py`, add `from textwrap import dedent` to the imports, add the systemd import next to the other charm-lib imports:

```python
import charms.operator_libs_linux.v1.systemd as systemd
```

extend the `constants` import to also pull in:

```python
    NODE_EXPORTER_BINARY_DIR,
    NODE_EXPORTER_BINARY_SERVICE,
    NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY,
    NODE_EXPORTER_BINARY_UNIT_PATH,
```

extend the `node_exporter_flags` import to `build_collector_flags, parse_version, Version`, and append:

```python
def build_unit_file(
    port: int,
    binary_path: str = NODE_EXPORTER_BINARY_PATH,
    user: str = NODE_EXPORTER_BINARY_USER,
    group: str = NODE_EXPORTER_BINARY_GROUP,
    textfile_directory: str = NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY,
    version: Optional[Version] = None,
) -> str:
    """Render the systemd unit that runs the custom node-exporter binary.

    The collector flags come from the same builder the apt flavor uses, so all
    three package flavors expose the same metrics on the same port.

    The service runs unprivileged, like the Ubuntu archive deb does. Collectors
    that need root (notably `systemd`, which reads the root-owned
    /run/systemd/private socket) will log errors instead of producing metrics;
    that is the same trade-off the apt flavor already makes.
    """
    flags = " ".join(build_collector_flags(port, textfile_directory, version))
    return dedent(f"""\
        # Managed by the opentelemetry-collector charm. Do not edit.
        [Unit]
        Description=Prometheus Node Exporter (custom build, managed by the opentelemetry-collector charm)
        Documentation=https://github.com/prometheus/node_exporter
        Wants=network-online.target
        After=network-online.target

        [Service]
        Type=simple
        User={user}
        Group={group}
        ExecStart={binary_path} {flags}
        Restart=always
        RestartSec=5
        NoNewPrivileges=true
        ProtectHome=true

        [Install]
        WantedBy=multi-user.target
        """)


def sync_node_exporter_service(
    url: str,
    port: int,
    binary_path: str = NODE_EXPORTER_BINARY_PATH,
    state_path: str = NODE_EXPORTER_BINARY_STATE_PATH,
    unit_path: str = NODE_EXPORTER_BINARY_UNIT_PATH,
    service: str = NODE_EXPORTER_BINARY_SERVICE,
    force_restart: bool = False,
) -> None:
    """Converge the custom-binary flavor onto the configured URL and port.

    The binary is re-downloaded only when the configured URL differs from the
    one recorded in the state file, or when the binary is missing; the unit
    file is rewritten only when its rendered content changes. The service is
    restarted when either changed, when it is not running (e.g. it could not
    bind the port while another flavor still held it mid-switch), or when
    force_restart is set.

    Raises:
        BinaryUrlError: if url is not an http(s) URL.
        BinaryDownloadError: if the binary could not be downloaded.
        BinaryServiceError: if systemd could not be reloaded or the service
            could not be enabled or (re)started.
    """
    validate_url(url)
    ensure_user()

    downloaded = False
    if not is_installed(binary_path) or installed_url(state_path) != url:
        download_binary(url, binary_path=binary_path, state_path=state_path)
        downloaded = True

    desired = build_unit_file(port, binary_path=binary_path, version=binary_version(binary_path))
    unit = LocalPath(unit_path)
    unit_changed = not unit.exists() or unit.read_text() != desired
    if unit_changed:
        unit.write_text(desired)

    if unit_changed or downloaded:
        try:
            systemd.daemon_reload()
        except systemd.SystemdError as e:
            raise BinaryServiceError("Failed to reload systemd") from e

    if unit_changed or downloaded or force_restart or not systemd.service_running(service):
        try:
            systemd.service_enable(service)
            systemd.service_restart(service)
        except systemd.SystemdError as e:
            raise BinaryServiceError(f"Failed to restart {service}") from e


def remove(
    unit_path: str = NODE_EXPORTER_BINARY_UNIT_PATH,
    service: str = NODE_EXPORTER_BINARY_SERVICE,
    directory: str = NODE_EXPORTER_BINARY_DIR,
) -> None:
    """Stop the service and delete the unit file, binary and install directory.

    Every step is best-effort and logs instead of raising: this runs from the
    remove hook and from flavor switches, where a failure must not wedge the
    unit. The `node-exporter` system user is deliberately left behind; it is
    harmless and may still own files elsewhere.
    """
    try:
        systemd.service_stop(service)
        systemd.service_disable(service)
    except systemd.SystemdError as e:
        log.warning(f"failed to stop the {service} service: {e}")
    try:
        LocalPath(unit_path).unlink(missing_ok=True)
        systemd.daemon_reload()
    except (OSError, systemd.SystemdError) as e:
        log.warning(f"failed to remove the {service} unit file: {e}")
    shutil.rmtree(directory, ignore_errors=True)
    log.info("the custom node-exporter binary has been removed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `tox -e unit -- tests/unit/test_binary_management.py`
Expected: PASS (all tests, ~31)

- [ ] **Step 5: Lint and type-check**

Run: `tox -e lint && tox -e static`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/binary_management.py tests/unit/test_binary_management.py
git commit -m "feat: run the custom node-exporter binary under systemd

Renders /etc/systemd/system/node-exporter.service with the same collector
flags as the other flavors, restarting only when the binary, the unit or
the service state actually changed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: The `binary-url` config option and flavor resolution

Expose the option and teach the charm which of the three flavors it is in.
Nothing installs anything yet — that is Task 5 — but after this task the charm
validates the URL, blocks on a bad one, and stops validating `package-type`
while a `binary-url` is set (it is ignored in that case, so an invalid value
there must not block).

**Files:**
- Modify: `charmcraft.yaml` (add `binary-url`, amend `package-type`'s description)
- Modify: `src/charm.py:1017-1020` region (add two properties next to `_node_exporter_package_type`), and `src/charm.py:240-243` (validation)
- Modify: `tests/unit/conftest.py` (add `mock_binary_operations`, patch the binary textfile dir)
- Create: `tests/unit/test_binary_url_config.py`

**Interfaces:**
- Consumes: `binary_management.validate_url`, `binary_management.BinaryUrlError` (Task 2).
- Produces, for Task 5:
  - `OpenTelemetryCollectorCharm._node_exporter_binary_url -> str` (stripped; `""` when unset)
  - `OpenTelemetryCollectorCharm._node_exporter_flavor -> str` (`"binary"` | `"snap"` | `"apt"`)
  - The `mock_binary_operations` fixture, yielding `{"is_installed", "sync_service", "remove"}` mocks.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_binary_url_config.py`:

```python
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: node-exporter binary-url config option."""

from ops.testing import State

URL = "https://example.com/builds/node_exporter-custom-1.10.2"


def test_default_binary_url_is_empty_and_does_not_block(ctx):
    # GIVEN no explicit binary-url config
    # WHEN any hook runs the reconciler
    state_out = ctx.run(ctx.on.update_status(), State())
    # THEN the unit is not blocked over binary-url
    assert "binary-url" not in getattr(state_out.unit_status, "message", "")


def test_valid_binary_url_does_not_block(ctx):
    # GIVEN a well-formed http(s) URL
    state = State(config={"binary-url": URL})
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), state)
    # THEN the unit is not blocked over binary-url
    assert "binary-url" not in getattr(state_out.unit_status, "message", "")


def test_invalid_binary_url_blocks(ctx):
    for value in ("not-a-url", "file:///tmp/node_exporter", "ftp://example.com/node_exporter"):
        # GIVEN a URL that is not http(s)
        state = State(config={"binary-url": value})
        # WHEN the reconciler runs
        state_out = ctx.run(ctx.on.config_changed(), state)
        # THEN the unit blocks with an actionable message
        assert state_out.unit_status.name == "blocked"
        assert state_out.unit_status.message == (
            "Invalid binary-url config: must be an http(s) URL to a node_exporter executable"
        )


def test_whitespace_only_binary_url_is_treated_as_unset(ctx):
    # GIVEN a binary-url that is only whitespace
    state = State(config={"binary-url": "   "})
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), state)
    # THEN it falls back to package-type rather than blocking on a bad URL
    assert "binary-url" not in getattr(state_out.unit_status, "message", "")


def test_binary_url_takes_precedence_over_package_type(ctx):
    for package_type in ("apt", "snap"):
        # GIVEN both a binary-url and a package-type
        state = State(config={"binary-url": URL, "package-type": package_type})
        # WHEN the reconciler runs
        with ctx(ctx.on.config_changed(), state) as manager:
            charm = manager.charm
            manager.run()
        # THEN the charm resolves to the binary flavor
        assert charm._node_exporter_flavor == "binary"


def test_package_type_is_not_validated_while_binary_url_is_set(ctx):
    """package-type is ignored under a binary-url, so a stale value must not block."""
    # GIVEN a binary-url plus a package-type value that would otherwise block
    state = State(config={"binary-url": URL, "package-type": "flatpak"})
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), state)
    # THEN the unit is not blocked over package-type
    assert "package-type" not in getattr(state_out.unit_status, "message", "")


def test_flavor_falls_back_to_package_type_without_a_binary_url(ctx):
    for package_type, expected in (("apt", "apt"), ("snap", "snap")):
        # GIVEN no binary-url
        state = State(config={"package-type": package_type})
        # WHEN the reconciler runs
        with ctx(ctx.on.config_changed(), state) as manager:
            charm = manager.charm
            manager.run()
        # THEN the flavor is the configured package-type
        assert charm._node_exporter_flavor == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tox -e unit -- tests/unit/test_binary_url_config.py`
Expected: FAIL — Scenario rejects the unknown `binary-url` config key, and `_node_exporter_flavor` does not exist.

- [ ] **Step 3: Add the config option**

In `charmcraft.yaml`, immediately after the `package-type` option (i.e. at the end of the `config.options` block, before `actions:`), add:

```yaml
    binary-url:
      type: string
      default: ""
      description: |
        URL of a prebuilt node_exporter executable to install instead of the
        snap or the deb. Leave empty (the default) to use `package-type`.

        Use this when you run a custom node_exporter build whose collectors are
        not in the upstream snap or in the Ubuntu archive package. The URL must
        be http(s) and must point directly at the executable — not at a tarball,
        a release page, or a redirect to one.

        When set, this option takes precedence over `package-type`: the snap and
        the deb are uninstalled (unless another collector unit on the same
        machine still uses them) and the charm takes over the whole lifecycle:

          * the binary is downloaded to
            /opt/prometheus/node-exporter/node_exporter
          * it runs as a dedicated, unprivileged `node-exporter` system user
            created by the charm
          * a charm-managed systemd unit, node-exporter.service, starts and
            stops it and restarts it on failure
          * the textfile collector reads
            /opt/prometheus/node-exporter/textfile-collector.d

        The binary is re-downloaded only when this URL changes, so publish each
        build at its own URL (for example one containing the version or the
        commit). Re-uploading a new build over the same URL will not be picked
        up.

        Like the `apt` flavor, the service runs unprivileged, so collectors that
        need root — notably `systemd` — report errors instead of metrics. The
        `snap` flavor is the only one that runs as root.

        Example:
          juju config opentelemetry-collector \
            binary-url=https://example.com/builds/node_exporter-1.10.2-custom
```

Amend the end of the `package-type` description, after the existing final paragraph, with:

```
        This option is ignored while `binary-url` is set: a custom build takes
        precedence over both package sources.
```

- [ ] **Step 4: Add the charm properties and validation**

In `src/charm.py`:

Add the import next to `import apt_management`:

```python
import binary_management
```

Add the two properties immediately after `_node_exporter_package_type`:

```python
    @property
    def _node_exporter_binary_url(self) -> str:
        """URL of a custom node-exporter build, or '' when none is configured."""
        return cast(str, self.config.get("binary-url")).strip()

    @property
    def _node_exporter_flavor(self) -> str:
        """Which node-exporter source this unit uses: 'binary', 'snap' or 'apt'.

        A non-empty binary-url wins over package-type, because the upstream snap
        and deb do not carry our custom collectors.
        """
        if self._node_exporter_binary_url:
            return "binary"
        return self._node_exporter_package_type
```

Replace the package-type validation block in `_reconcile` with:

```python
        # Validate the node-exporter flavor config. A non-empty binary-url takes
        # precedence over package-type, which is then not validated at all: it is
        # ignored, so a stale value must not block the unit.
        if binary_url := self._node_exporter_binary_url:
            try:
                binary_management.validate_url(binary_url)
            except binary_management.BinaryUrlError as e:
                logger.error(f"Invalid binary-url config: {e}")
                self.unit.status = BlockedStatus(
                    "Invalid binary-url config: must be an http(s) URL to a node_exporter executable"
                )
                return
        elif self._node_exporter_package_type not in ("snap", "apt"):
            self.unit.status = BlockedStatus("Invalid package-type config: must be 'snap' or 'apt'")
            return
```

- [ ] **Step 5: Import the binary textfile constant in `charm.py`**

Add to the `from constants import (...)` block in `src/charm.py`, keeping the
list alphabetical:

```python
    NODE_EXPORTER_BINARY_PACKAGE,
    NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY,
```

Ruff's `F401` will flag `NODE_EXPORTER_BINARY_PACKAGE` as unused until Task 5
uses it. If that blocks the commit, import only
`NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY` here and add
`NODE_EXPORTER_BINARY_PACKAGE` in Task 5 Step 3.

- [ ] **Step 6: Add the test fixture**

In `tests/unit/conftest.py`, add a fixture next to `mock_apt_operations`:

```python
@pytest.fixture(autouse=True)
def mock_binary_operations():
    """Mock the custom node-exporter binary lifecycle (binary-url flavor)."""
    with (
        patch("binary_management.ensure_user"),
        patch("binary_management.systemd.service_restart"),
        patch("binary_management.systemd.service_enable"),
        patch("binary_management.systemd.service_stop"),
        patch("binary_management.systemd.service_disable"),
        patch("binary_management.systemd.daemon_reload"),
        patch("binary_management.systemd.service_running", return_value=True),
        patch("binary_management.is_installed", return_value=False) as mock_is_installed,
        patch("binary_management.sync_node_exporter_service") as mock_sync,
        patch("binary_management.remove") as mock_remove,
    ):
        yield {
            "is_installed": mock_is_installed,
            "sync_service": mock_sync,
            "remove": mock_remove,
        }
```

Note this deliberately does **not** patch `binary_management.validate_url`: the
charm-level tests above assert on real URL validation.

In the existing `mock_ensure_directory` fixture, add the binary textfile
directory to the patch list so the charm writes its info-metric file into
`tmp_path`:

```python
        patch("charm.NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY", str(node_exporter_dir)),
```

(Step 5 added that name to `charm.py`; without it the patch fails with
`AttributeError`.)

- [ ] **Step 7: Run the test to verify it passes**

Run: `tox -e unit -- tests/unit/test_binary_url_config.py`
Expected: PASS (7 tests)

- [ ] **Step 8: Run the full unit suite**

Run: `tox -e unit`
Expected: PASS — in particular `tests/unit/test_package_type_config.py`, which must be unaffected.

- [ ] **Step 9: Lint and type-check**

Run: `tox -e lint && tox -e static`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add charmcraft.yaml src/charm.py tests/unit/conftest.py tests/unit/test_binary_url_config.py
git commit -m "feat: add binary-url config option with validation

A non-empty binary-url selects the custom-binary node-exporter flavor and
takes precedence over package-type, which is then ignored and no longer
validated.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Wire the binary flavor into the charm lifecycle

Make the third flavor real: install it, switch to and from it, refuse to fight
a co-located unit over the port, and clean it up on removal.

The existing `_sync_node_exporter_package` handles exactly two flavors with the
"drop the other one" logic inlined twice. With three flavors that shape does not
scale, so it is split into a dispatcher, one method per flavor, and three small
`_drop_node_exporter_*` helpers. The apt and snap paths keep their existing
behaviour exactly — `tests/unit/test_package_type_switching.py` must keep
passing untouched.

**Files:**
- Modify: `src/charm.py` — `_managed_snaps`, `_node_exporter_flavor_conflict`, `_install_snaps`, `_remove_node_exporter`, `_node_exporter_textfile_dir`, and `_sync_node_exporter_package` split into five methods
- Create: `tests/unit/test_binary_flavor_switching.py`

**Interfaces:**
- Consumes: `binary_management.{is_installed, sync_node_exporter_service, remove, BinaryDownloadError, BinaryError}` (Tasks 2–3); `_node_exporter_flavor`, `_node_exporter_binary_url` (Task 4); `constants.NODE_EXPORTER_BINARY_PACKAGE`, `constants.NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY` (Task 2).
- Produces: nothing beyond this task; Task 6 only exercises the charm through Juju.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_binary_flavor_switching.py`:

```python
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: switching node-exporter to and from a custom binary (binary-url)."""

from unittest.mock import patch

from ops.testing import State

import binary_management
from constants import NODE_EXPORTER_APT_PACKAGE, NODE_EXPORTER_BINARY_PACKAGE
from singleton_snap import SingletonSnapManager

URL = "https://example.com/builds/node_exporter-custom-1.10.2"
BINARY = {"binary-url": URL}


def test_install_hook_registers_the_binary_not_the_snap_or_deb(ctx):
    # GIVEN a binary-url
    with patch("charm.event", return_value="install"), patch("charm.install_snap"):
        # WHEN the install hook runs
        ctx.run(ctx.on.install(), State(config=BINARY))
    # THEN only the binary pseudo-package is registered for node-exporter
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"otelcol_0"}
    assert SingletonSnapManager.get_units("node-exporter") == set()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_APT_PACKAGE) == set()
    # AND the otelcol snap is still registered
    assert SingletonSnapManager.get_units("opentelemetry-collector") == {"otelcol_0"}


def test_install_hook_does_not_install_the_node_exporter_snap(ctx):
    with (
        patch("charm.event", return_value="install"),
        patch("charm.install_snap") as mock_install,
    ):
        ctx.run(ctx.on.install(), State(config=BINARY))
    installed = {call.args[0] for call in mock_install.call_args_list}
    assert "opentelemetry-collector" in installed
    assert "node-exporter" not in installed


def test_binary_mode_syncs_the_service_with_the_configured_url_and_port(
    ctx, mock_binary_operations
):
    # GIVEN a binary-url and a node_exporter port override
    state = State(config={**BINARY, "ports": "node_exporter=9200"})
    # WHEN the reconciler runs
    ctx.run(ctx.on.config_changed(), state)
    # THEN the service is synced with that URL and port
    mock_binary_operations["sync_service"].assert_called_once()
    args, kwargs = mock_binary_operations["sync_service"].call_args
    assert args[0] == URL
    assert args[1] == 9200


def test_binary_mode_does_not_configure_the_snap(ctx, mock_binary_operations):
    # GIVEN binary mode
    with patch("charm.OpenTelemetryCollectorCharm._configure_node_exporter") as mock_cfg:
        # WHEN the reconciler runs
        ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the snap-flavor configuration path is never invoked
    mock_cfg.assert_not_called()


def test_binary_mode_writes_the_info_metric_file(ctx, mock_binary_operations, tmp_path):
    # GIVEN binary mode (the textfile dir is redirected into tmp_path by conftest)
    ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN this unit's info metric file exists in the textfile directory
    assert (tmp_path / "textfile-collector.d" / "otelcol_0.prom").exists()


def test_switch_apt_to_binary_removes_the_deb(ctx, mock_apt_operations, mock_binary_operations):
    # GIVEN this unit previously registered the deb and the deb is installed
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    mock_apt_operations["is_installed"].return_value = True
    with patch("apt_management.remove_package") as mock_remove_deb:
        # WHEN a binary-url is configured
        ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the deb is removed and its registration dropped
    mock_remove_deb.assert_called_once_with(NODE_EXPORTER_APT_PACKAGE)
    assert SingletonSnapManager.get_units(NODE_EXPORTER_APT_PACKAGE) == set()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"otelcol_0"}
    # AND the service is restarted anyway, because the deb just freed the port
    assert mock_binary_operations["sync_service"].call_args.kwargs["force_restart"] is True


def test_switch_snap_to_binary_removes_the_snap(
    ctx, mock_binary_operations, mock_snap_operations
):
    # GIVEN this unit previously registered the node-exporter snap, which is present
    SingletonSnapManager("otelcol/0").register("node-exporter", 2)
    mock_snap_operations.present = True
    with patch("charm.OpenTelemetryCollectorCharm._remove_snap") as mock_remove:
        # WHEN a binary-url is configured
        ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the snap is removed and its registration dropped
    mock_remove.assert_called_once_with("node-exporter")
    assert SingletonSnapManager.get_units("node-exporter") == set()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"otelcol_0"}


def test_switch_to_binary_keeps_a_deb_used_by_another_unit(
    ctx, mock_apt_operations, mock_binary_operations
):
    # GIVEN another co-located unit still uses the deb
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    SingletonSnapManager("other/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    mock_apt_operations["is_installed"].return_value = True
    with patch("apt_management.remove_package") as mock_remove_deb:
        # WHEN this unit switches to a binary-url
        ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the deb is NOT removed
    mock_remove_deb.assert_not_called()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_APT_PACKAGE) == {"other_0"}


def test_switch_binary_to_apt_removes_the_binary(ctx, mock_apt_operations, mock_binary_operations):
    # GIVEN this unit previously used a custom binary, which is installed
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    mock_binary_operations["is_installed"].return_value = True
    with patch("apt_management.install_package"):
        # WHEN binary-url is cleared, falling back to the default apt flavor
        ctx.run(ctx.on.config_changed(), State(config={"binary-url": ""}))
    # THEN the binary and its service are removed and the registration dropped
    mock_binary_operations["remove"].assert_called_once()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == set()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_APT_PACKAGE) == {"otelcol_0"}
    # AND the deb service is force-restarted, because the binary just freed the port
    assert mock_apt_operations["sync_config"].call_args.kwargs["force_restart"] is True


def test_switch_binary_to_apt_keeps_a_binary_used_by_another_unit(
    ctx, mock_apt_operations, mock_binary_operations
):
    # GIVEN another co-located unit still uses the custom binary
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    SingletonSnapManager("other/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    mock_binary_operations["is_installed"].return_value = True
    with patch("apt_management.install_package"):
        # WHEN this unit clears binary-url
        ctx.run(ctx.on.config_changed(), State(config={"binary-url": ""}))
    # THEN the binary is NOT removed
    mock_binary_operations["remove"].assert_not_called()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"other_0"}


def test_blocked_when_another_unit_uses_a_different_flavor(ctx, mock_binary_operations):
    # GIVEN a co-located unit of another app registered the deb
    SingletonSnapManager("other/0").register(NODE_EXPORTER_APT_PACKAGE, 0)
    # WHEN this unit reconciles with a binary-url
    state_out = ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN it blocks with an actionable message
    assert state_out.unit_status.name == "blocked"
    assert "binary-url" in state_out.unit_status.message


def test_no_conflict_when_all_units_use_the_binary(ctx, mock_binary_operations):
    # GIVEN another unit also using the custom binary
    SingletonSnapManager("other/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the unit is not blocked over a flavor conflict
    assert "different node-exporter flavor" not in getattr(state_out.unit_status, "message", "")


def test_download_failure_blocks_instead_of_crashing(ctx, mock_binary_operations):
    # GIVEN the binary cannot be fetched
    mock_binary_operations["sync_service"].side_effect = binary_management.BinaryDownloadError(
        "404"
    )
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the hook completes with a blocked status instead of raising
    assert state_out.unit_status.name == "blocked"
    assert "Failed to download" in state_out.unit_status.message


def test_service_failure_blocks_instead_of_crashing(ctx, mock_binary_operations):
    # GIVEN the service will not start
    mock_binary_operations["sync_service"].side_effect = binary_management.BinaryServiceError(
        "boom"
    )
    # WHEN the reconciler runs
    state_out = ctx.run(ctx.on.config_changed(), State(config=BINARY))
    # THEN the hook completes with a blocked status instead of raising
    assert state_out.unit_status.name == "blocked"
    assert "failed to start" in state_out.unit_status.message


def test_remove_hook_removes_the_binary(ctx, mock_binary_operations):
    # GIVEN this unit uses a custom binary, which is installed
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    mock_binary_operations["is_installed"].return_value = True
    with patch("charm.event", return_value="remove"):
        # WHEN the remove hook runs
        ctx.run(ctx.on.remove(), State(config=BINARY))
    # THEN the binary and its service are removed and the registration dropped
    mock_binary_operations["remove"].assert_called_once()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == set()


def test_remove_hook_keeps_a_binary_used_by_another_unit(ctx, mock_binary_operations):
    # GIVEN another co-located unit still uses the custom binary
    SingletonSnapManager("otelcol/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    SingletonSnapManager("other/0").register(NODE_EXPORTER_BINARY_PACKAGE, 0)
    mock_binary_operations["is_installed"].return_value = True
    with patch("charm.event", return_value="remove"):
        ctx.run(ctx.on.remove(), State(config=BINARY))
    # THEN the binary is NOT removed
    mock_binary_operations["remove"].assert_not_called()
    assert SingletonSnapManager.get_units(NODE_EXPORTER_BINARY_PACKAGE) == {"other_0"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tox -e unit -- tests/unit/test_binary_flavor_switching.py`
Expected: FAIL — nothing registers `node-exporter-binary` and `sync_node_exporter_service` is never called.

- [ ] **Step 3: Add the flavor-to-package map**

In `src/charm.py`, below `VALID_LOG_LEVELS`, add:

```python
# Reference-counting name per node-exporter flavor, for SingletonSnapManager.
# The snap and the deb use their real package names; the custom binary has no
# package, so it is tracked under a pseudo-package name.
NODE_EXPORTER_FLAVOR_PACKAGES: Dict[str, str] = {
    "snap": "node-exporter",
    "apt": NODE_EXPORTER_APT_PACKAGE,
    "binary": NODE_EXPORTER_BINARY_PACKAGE,
}
```

(If Task 4 Step 6 deferred it, add `NODE_EXPORTER_BINARY_PACKAGE` to the
`from constants import (...)` block now.)

- [ ] **Step 4: Make the existing flavor-aware methods three-way**

In `src/charm.py`, replace these five members:

```python
    def _managed_snaps(self) -> List[str]:
        """Snaps this unit manages given the current config.

        node-exporter is only snap-managed when the resolved flavor is 'snap';
        the deb and the custom binary are handled by _sync_node_exporter_package.
        """
        snaps = ["opentelemetry-collector"]
        if self._node_exporter_flavor == "snap":
            snaps.append("node-exporter")
        return snaps

    def _node_exporter_flavor_conflict(self) -> bool:
        """True when a co-located unit registered node-exporter under another flavor.

        Every flavor would fight over the node-exporter port, so this is surfaced
        as BlockedStatus. A same-application flavor switch trips this only
        transiently: once every unit has reconciled, the old flavors'
        registrations are gone.
        """
        manager = SingletonSnapManager(self.unit.name)
        return any(
            manager.is_used_by_other_units(package)
            for flavor, package in NODE_EXPORTER_FLAVOR_PACKAGES.items()
            if flavor != self._node_exporter_flavor
        )
```

```python
    def _remove_node_exporter(self):
        """Coordinate removal of node-exporter, whichever flavor(s) are present."""
        manager = SingletonSnapManager(self.unit.name)
        for package in NODE_EXPORTER_FLAVOR_PACKAGES.values():
            manager.unregister_all_for_unit(package)
        if not manager.is_used_by_other_units("node-exporter"):
            self._remove_snap("node-exporter")
        if not manager.is_used_by_other_units(
            NODE_EXPORTER_APT_PACKAGE
        ) and apt_management.is_installed(NODE_EXPORTER_APT_PACKAGE):
            self.unit.status = MaintenanceStatus(f"Uninstalling {NODE_EXPORTER_APT_PACKAGE} deb")
            try:
                apt_management.remove_package(NODE_EXPORTER_APT_PACKAGE)
            except apt_management.AptError as e:
                # Log error but don't fail the remove hook
                logger.error(f"Failed to uninstall {NODE_EXPORTER_APT_PACKAGE} deb: {e}")
        if not manager.is_used_by_other_units(
            NODE_EXPORTER_BINARY_PACKAGE
        ) and binary_management.is_installed():
            self.unit.status = MaintenanceStatus("Removing the custom node-exporter binary")
            # binary_management.remove is best-effort and never raises.
            binary_management.remove()

        # Clean up this unit's info-metric file from every flavor directory
        for directory in (
            NODE_EXPORTER_TEXTFILE_DIRECTORY,
            NODE_EXPORTER_APT_TEXTFILE_DIRECTORY,
            NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY,
        ):
            self._remove_node_exporter_info_metric_file(directory)
```

```python
    def _install_snaps(self) -> None:
        manager = SingletonSnapManager(self.unit.name)

        if self._node_exporter_flavor != "snap":
            # Neither the deb nor the custom binary has a pinned revision; register
            # with a constant 0 so the lockfile-based reference counting works the
            # same way as it does for snaps.
            manager.register(NODE_EXPORTER_FLAVOR_PACKAGES[self._node_exporter_flavor], 0)

        for snap_name in self._managed_snaps():
            snap_revision = SnapMap.get_revision(snap_name)
            manager.register(snap_name, snap_revision)
            revisions = manager.get_revisions(snap_name)
            if snap_revision >= (max(revisions) if revisions else 0):
                # Install the snap
                self.unit.status = MaintenanceStatus(f"Installing {snap_name} snap")
                install_snap(snap_name)
                # Start the snap
                self.unit.status = MaintenanceStatus(f"Starting {snap_name} snap")
                try:
                    self.snap(snap_name).start(enable=True)
                except snap.SnapError as e:
                    raise SnapServiceError(f"Failed to start {snap_name}") from e
```

```python
    @property
    def _node_exporter_textfile_dir(self) -> str:
        """Directory scraped by node-exporter's textfile collector for the active flavor.

        For the apt flavor this is the directory the Debian packaging points the
        textfile collector at by default; for the snap and the custom binary the
        charm chooses it. The charm only ever writes its info-metric file there.
        """
        flavor = self._node_exporter_flavor
        if flavor == "binary":
            return NODE_EXPORTER_BINARY_TEXTFILE_DIRECTORY
        if flavor == "apt":
            return NODE_EXPORTER_APT_TEXTFILE_DIRECTORY
        return NODE_EXPORTER_TEXTFILE_DIRECTORY
```

And update the conflict message in `_reconcile` to name both options:

```python
        if self._node_exporter_flavor_conflict():
            self.unit.status = BlockedStatus(
                "Another unit on this machine uses a different node-exporter flavor; "
                "align the package-type and binary-url config on all collector apps "
                "sharing this machine"
            )
            return
```

- [ ] **Step 5: Split the sync into a three-way dispatch**

In `src/charm.py`, replace the whole of `_sync_node_exporter_package` with the
following six methods. The apt and snap bodies are the existing ones with their
inlined "drop the other flavor" blocks lifted into the `_drop_*` helpers, plus a
call to drop the binary flavor.

```python
    def _sync_node_exporter_package(self, port: int) -> Optional[str]:
        """Converge node-exporter onto the configured flavor.

        Uninstalls the other flavors when this unit is their last registered user
        (see SingletonSnapManager) and installs the desired one if missing. All
        flavors are configured with the same collector set and listen port: the
        snap via `snap set`, the deb via its ARGS defaults file, the custom binary
        via its charm-rendered systemd unit.

        Returns:
            An error message for BlockedStatus if the desired flavor could not be
            installed or started, None otherwise. Failures must not raise: an
            unhandled exception here fails the hook and wedges the unit in error
            state.
        """
        flavor = self._node_exporter_flavor
        if flavor == "binary":
            return self._sync_node_exporter_binary(port)
        if flavor == "apt":
            return self._sync_node_exporter_apt(port)
        return self._sync_node_exporter_snap(port)

    def _drop_node_exporter_snap(self) -> bool:
        """Unregister the snap flavor and uninstall it if this unit was its last user.

        Returns:
            True if the snap was actually uninstalled, so the caller knows the
            node-exporter port was just freed and its own service needs a restart.
        """
        manager = SingletonSnapManager(self.unit.name)
        manager.unregister_all_for_unit("node-exporter")
        if (
            manager.is_used_by_other_units("node-exporter")
            or not self.snap("node-exporter").present
        ):
            return False
        self._remove_snap("node-exporter")
        return True

    def _drop_node_exporter_deb(self) -> bool:
        """Unregister the apt flavor and remove it if this unit was its last user.

        Returns:
            True if the deb was actually removed.
        """
        manager = SingletonSnapManager(self.unit.name)
        manager.unregister_all_for_unit(NODE_EXPORTER_APT_PACKAGE)
        if manager.is_used_by_other_units(
            NODE_EXPORTER_APT_PACKAGE
        ) or not apt_management.is_installed(NODE_EXPORTER_APT_PACKAGE):
            return False
        self.unit.status = MaintenanceStatus(f"Uninstalling {NODE_EXPORTER_APT_PACKAGE} deb")
        apt_management.remove_package(NODE_EXPORTER_APT_PACKAGE)
        return True

    def _drop_node_exporter_binary(self) -> bool:
        """Unregister the binary flavor and remove it if this unit was its last user.

        Returns:
            True if the binary and its systemd service were actually removed.
        """
        manager = SingletonSnapManager(self.unit.name)
        manager.unregister_all_for_unit(NODE_EXPORTER_BINARY_PACKAGE)
        if manager.is_used_by_other_units(
            NODE_EXPORTER_BINARY_PACKAGE
        ) or not binary_management.is_installed():
            return False
        self.unit.status = MaintenanceStatus("Removing the custom node-exporter binary")
        # binary_management.remove is best-effort and never raises.
        binary_management.remove()
        return True

    def _sync_node_exporter_apt(self, port: int) -> Optional[str]:
        """Install and configure the prometheus-node-exporter deb."""
        # NOTE: both drops must run, so evaluate them before the `or`.
        snap_removed = self._drop_node_exporter_snap()
        other_removed = self._drop_node_exporter_binary() or snap_removed
        if not apt_management.is_installed(NODE_EXPORTER_APT_PACKAGE):
            self.unit.status = MaintenanceStatus(f"Installing {NODE_EXPORTER_APT_PACKAGE} deb")
            try:
                apt_management.install_package(NODE_EXPORTER_APT_PACKAGE)
            except apt_management.AptInstallError as e:
                logger.error(f"Failed to install {NODE_EXPORTER_APT_PACKAGE}: {e}")
                return f"Failed to install {NODE_EXPORTER_APT_PACKAGE} from apt; see debug-log"
        SingletonSnapManager(self.unit.name).register(NODE_EXPORTER_APT_PACKAGE, 0)
        self._ensure_directory(self._node_exporter_textfile_dir)
        self._node_exporter_info_metric_file_path.write_text(self._info_metric)
        # Writes the ARGS defaults file and restarts the service when the config
        # changed or the service is down (e.g. it failed to bind :9100 while another
        # flavor still held the port mid-switch across several units).
        try:
            apt_management.sync_node_exporter_config(port, force_restart=other_removed)
        except apt_management.AptServiceError as e:
            logger.error(f"node-exporter (apt) service failed to restart: {e}")
            return f"{NODE_EXPORTER_APT_PACKAGE} service failed to restart; see debug-log"
        return None

    def _sync_node_exporter_snap(self, port: int) -> Optional[str]:
        """Install and configure the node-exporter snap."""
        deb_removed = self._drop_node_exporter_deb()
        other_removed = self._drop_node_exporter_binary() or deb_removed
        if not self.snap("node-exporter").present:
            self.unit.status = MaintenanceStatus("Installing node-exporter snap")
            install_snap("node-exporter")
            try:
                self.snap("node-exporter").start(enable=True)
            except snap.SnapError as e:
                raise SnapServiceError("Failed to start node-exporter") from e
        SingletonSnapManager(self.unit.name).register(
            "node-exporter", SnapMap.get_revision("node-exporter")
        )
        self._ensure_directory(self._node_exporter_textfile_dir)
        self._configure_node_exporter(port)
        if other_removed:
            self._restart_snap(self.snap("node-exporter"))
        return None

    def _sync_node_exporter_binary(self, port: int) -> Optional[str]:
        """Install and configure the custom node-exporter build from binary-url."""
        snap_removed = self._drop_node_exporter_snap()
        other_removed = self._drop_node_exporter_deb() or snap_removed
        SingletonSnapManager(self.unit.name).register(NODE_EXPORTER_BINARY_PACKAGE, 0)
        self._ensure_directory(self._node_exporter_textfile_dir)
        self._node_exporter_info_metric_file_path.write_text(self._info_metric)
        if not binary_management.is_installed():
            self.unit.status = MaintenanceStatus("Downloading the custom node-exporter binary")
        try:
            binary_management.sync_node_exporter_service(
                self._node_exporter_binary_url, port, force_restart=other_removed
            )
        except binary_management.BinaryDownloadError as e:
            logger.error(f"Failed to download the node-exporter binary: {e}")
            return "Failed to download the node-exporter binary from binary-url; see debug-log"
        except binary_management.BinaryError as e:
            # Covers BinaryServiceError and the useradd failure path.
            logger.error(f"node-exporter (binary) service failed to start: {e}")
            return "node-exporter service failed to start; see debug-log"
        return None
```

- [ ] **Step 6: Run the new test to verify it passes**

Run: `tox -e unit -- tests/unit/test_binary_flavor_switching.py`
Expected: PASS (16 tests)

- [ ] **Step 7: Run the full unit suite**

Run: `tox -e unit`
Expected: PASS — in particular every test in `tests/unit/test_package_type_switching.py`, unmodified. If `test_blocked_when_other_unit_uses_other_flavor` fails on its `assert "package-type" in state_out.unit_status.message`, the message from Step 4 is wrong: it must contain the literal string `package-type`.

- [ ] **Step 8: Lint and type-check**

Run: `tox -e lint && tox -e static`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/charm.py tests/unit/test_binary_flavor_switching.py
git commit -m "feat: install node-exporter from binary-url when set

Adds the custom binary as a third node-exporter flavor alongside the snap
and the deb, sharing their reference counting, port-conflict blocking and
remove-hook cleanup.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Integration test and user-facing docs

Prove the whole thing works against a real machine: the charm downloads a real
executable, systemd runs it as the dedicated user, it serves metrics on the
node-exporter port including the charm's info metric, and clearing `binary-url`
puts the deb back.

The test serves the binary from the unit's own machine rather than the public
internet, so it does not depend on GitHub availability or on a hardcoded
upstream version. The payload is `/usr/bin/prometheus-node-exporter`, copied out
of the deb the charm installed by default — a genuine node_exporter executable
of exactly the version that base ships.

**Files:**
- Create: `tests/integration/test_binary_url.py`
- Modify: `README.md` (add a "node-exporter" section after the "Snap" section)

**Interfaces:**
- Consumes: the charm as built, plus `tests/integration/helpers.py` (`RETRY`, `PATH_EXCLUDE`, `ENABLE_BASIC_DEBUG_EXPORTERS`).
- Produces: nothing; this is the last task.

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_binary_url.py`:

```python
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
    output = juju.ssh("otelcol/0", command=f"curl -sf -o /dev/null -w '%{{http_code}}' {BINARY_URL}")
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
```

- [ ] **Step 2: Build the charm and run the integration test**

Run:
```bash
charmcraft pack
tox -e integration -- tests/integration/test_binary_url.py
```
Expected: PASS. If `test_switch_to_the_custom_binary` times out, check
`juju debug-log --include otelcol/0` and `journalctl -u node-exporter` on the
machine — the most likely cause is the binary failing to start because a
collector flag is unknown to that version, which `binary_version` should have
gated.

- [ ] **Step 3: Run the existing package-type integration test to check for regressions**

Run: `tox -e integration -- tests/integration/test_package_type.py`
Expected: PASS — snap/apt switching is unchanged by this work.

- [ ] **Step 4: Document the three flavors in the README**

In `README.md`, replace the `## Snap` section heading's following blank line with
a new section inserted after it (i.e. add this between `## Snap` and
`## Contributing`):

````markdown
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
````

- [ ] **Step 5: Run the full check suite one last time**

Run: `tox -e lint && tox -e static && tox -e unit`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_binary_url.py README.md
git commit -m "test: integration coverage for the binary-url node-exporter flavor

Serves a real node_exporter from the unit's own machine, switches the
charm onto it, and switches back, asserting the service, its user, its
flags and the deb cleanup at each step.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification checklist

Before calling this done, all of the following must have been run and observed
to pass — not assumed:

- [ ] `tox -e lint`
- [ ] `tox -e static`
- [ ] `tox -e unit` (the pre-existing `tests/unit/test_apt_management.py` and `tests/unit/test_package_type_switching.py` must pass **unmodified**)
- [ ] `tox -e integration -- tests/integration/test_binary_url.py`
- [ ] `tox -e integration -- tests/integration/test_package_type.py`
- [ ] `git diff main --stat` shows no changes to `pyproject.toml` dependencies
