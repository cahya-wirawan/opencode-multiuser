from __future__ import annotations

import re
import subprocess
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import settings

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_FILE = Path.home() / ".config" / "opencode-multiuser" / "control-plane.env"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OpenCodeUpdateState:
    status: str = "idle"
    requested_version: str | None = None
    previous_version: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    message: str = "No update is running."
    error: str | None = None
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=80))

    def payload(self) -> dict:
        data = asdict(self)
        data["log_tail"] = list(self.log_tail)
        data["running"] = self.status in {"queued", "building", "testing", "activating"}
        return data


_STATE = OpenCodeUpdateState()


def validate_version(version: str) -> str:
    value = version.strip()
    if not _SEMVER.fullmatch(value):
        raise ValueError("OpenCode version must be a semantic version such as 1.18.30")
    return value


def _append(message: str) -> None:
    with _LOCK:
        _STATE.log_tail.append(message.rstrip())
        _STATE.message = message.rstrip()


def _run(args: list[str], *, timeout: int = 900, stream: bool = False) -> subprocess.CompletedProcess[str]:
    if not stream:
        cp = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
        if cp.returncode != 0:
            raise RuntimeError(
                f"Command failed ({cp.returncode}): {' '.join(args)}\n"
                f"stdout:\n{cp.stdout[-4000:]}\nstderr:\n{cp.stderr[-4000:]}"
            )
        return cp

    proc = subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            output.append(line)
            _append(line.strip())
        rc = proc.wait(timeout=timeout)
    except Exception:
        proc.kill()
        raise
    text = "".join(output)
    if rc != 0:
        raise RuntimeError(f"Command failed ({rc}): {' '.join(args)}\n{text[-8000:]}")
    return subprocess.CompletedProcess(args, rc, stdout=text, stderr="")


def configured_version() -> str:
    return settings.opencode_version


def installed_version() -> str | None:
    try:
        cp = _run(
            [settings.podman_bin, "run", "--rm", settings.workspace_image, "--version"],
            timeout=60,
        )
    except Exception:
        return None
    value = cp.stdout.strip().splitlines()[-1].strip() if cp.stdout.strip() else ""
    # OpenCode currently prints the bare semantic version. Be tolerant of a prefix.
    match = re.search(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", value)
    return match.group(0) if match else (value or None)


def latest_version() -> str:
    response = httpx.get(settings.opencode_registry_url, timeout=10.0, follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    version = payload.get("version")
    if not isinstance(version, str):
        raise RuntimeError("OpenCode registry response did not contain a version")
    return validate_version(version)


def _persist_version(version: str) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = _CONFIG_FILE.read_text(encoding="utf-8").splitlines() if _CONFIG_FILE.exists() else []
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.startswith("OPENCODE_VERSION="):
            output.append(f"OPENCODE_VERSION={version}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"OPENCODE_VERSION={version}")
    _CONFIG_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")
    _CONFIG_FILE.chmod(0o600)
    settings.opencode_version = version


def _perform_update(version: str) -> None:
    safe_tag = version.replace("+", "_")
    candidate = f"localhost/opencode-workspace:{safe_tag}"
    previous = configured_version()
    try:
        with _LOCK:
            _STATE.status = "building"
            _STATE.message = f"Building OpenCode {version} candidate image…"
        _append(f"Building candidate image {candidate}")
        _run(
            [
                settings.podman_bin,
                "build",
                "--build-arg",
                f"OPENCODE_VERSION={version}",
                "-t",
                candidate,
                "-f",
                str(_PROJECT_ROOT / "Containerfile.workspace"),
                str(_PROJECT_ROOT),
            ],
            timeout=settings.opencode_update_timeout_seconds,
            stream=True,
        )

        with _LOCK:
            _STATE.status = "testing"
            _STATE.message = "Smoke-testing the candidate image…"
        cp = _run([settings.podman_bin, "run", "--rm", candidate, "--version"], timeout=60)
        reported = cp.stdout.strip().splitlines()[-1].strip() if cp.stdout.strip() else ""
        match = re.search(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", reported)
        actual = match.group(0) if match else reported
        if actual != version:
            raise RuntimeError(f"Candidate image reported OpenCode {actual!r}; expected {version!r}")
        _append(f"Smoke test passed: OpenCode {actual}")

        with _LOCK:
            _STATE.status = "activating"
            _STATE.message = "Activating the tested image…"
        _run([settings.podman_bin, "tag", candidate, settings.workspace_image], timeout=60)
        _persist_version(version)
        _append(f"Activated {settings.workspace_image} -> OpenCode {version}")
        _append("Existing running workspaces are unchanged; restart them to use the new image.")

        with _LOCK:
            _STATE.status = "success"
            _STATE.finished_at = _now()
            _STATE.message = f"OpenCode {version} is active for newly started workspaces."
            _STATE.error = None
    except Exception as exc:
        with _LOCK:
            _STATE.status = "error"
            _STATE.finished_at = _now()
            _STATE.message = "OpenCode update failed. The previous latest image was left unchanged."
            _STATE.error = f"{type(exc).__name__}: {exc}"
        _append(f"ERROR: {type(exc).__name__}: {exc}")
        # Keep the candidate tag for diagnosis/possible manual inspection.
    finally:
        with _LOCK:
            if _STATE.previous_version is None:
                _STATE.previous_version = previous


def start_update(version: str) -> dict:
    version = validate_version(version)
    with _LOCK:
        if _STATE.status in {"queued", "building", "testing", "activating"}:
            raise RuntimeError("An OpenCode update is already running")
        _STATE.status = "queued"
        _STATE.requested_version = version
        _STATE.previous_version = configured_version()
        _STATE.started_at = _now()
        _STATE.finished_at = None
        _STATE.message = f"Queued OpenCode {version} update."
        _STATE.error = None
        _STATE.log_tail.clear()
    thread = threading.Thread(target=_perform_update, args=(version,), name="opencode-image-update", daemon=True)
    thread.start()
    return update_status()


def update_status() -> dict:
    with _LOCK:
        return _STATE.payload()


def system_snapshot(*, check_latest: bool = False) -> dict:
    latest: str | None = None
    latest_error: str | None = None
    if check_latest:
        try:
            latest = latest_version()
        except Exception as exc:
            latest_error = f"{type(exc).__name__}: {exc}"
    return {
        "configured_version": configured_version(),
        "installed_version": installed_version(),
        "latest_version": latest,
        "latest_error": latest_error,
        "workspace_image": settings.workspace_image,
        "registry_url": settings.opencode_registry_url,
        "update": update_status(),
    }
