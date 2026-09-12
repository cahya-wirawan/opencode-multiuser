import base64
import http.client
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from .config import settings

_SAFE = re.compile(r"^[a-zA-Z0-9_.-]+$")


@dataclass
class RuntimeResult:
    container_name: str
    basic_password: str
    host_port: int


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        [settings.podman_bin, *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"Podman command failed with exit code {cp.returncode}\n"
            f"stdout:\n{cp.stdout}\n"
            f"stderr:\n{cp.stderr}"
        )
    return cp


def ensure_network() -> None:
    result = _run(["network", "exists", settings.podman_network], check=False)
    if result.returncode != 0:
        _run(["network", "create", settings.podman_network])


def workspace_root(user_id: str, project_slug: str) -> Path:
    if not _SAFE.match(project_slug):
        raise ValueError("invalid project slug")
    return settings.expanded_data_root / "users" / user_id / project_slug


def workspace_paths(user_id: str, project_slug: str) -> tuple[Path, Path, Path, Path]:
    root = workspace_root(user_id, project_slug)
    workspace = root / "workspace"
    data = root / "opencode-data"
    state = root / "opencode-state"
    config = root / "opencode-config"
    for path in (workspace, data, state, config):
        path.mkdir(parents=True, exist_ok=True)
    return workspace, data, state, config



def workspace_activity_path(user_id: str, project_slug: str) -> Path:
    return workspace_root(user_id, project_slug) / "last-activity"


def touch_workspace_activity(user_id: str, project_slug: str) -> None:
    path = workspace_activity_path(user_id, project_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def workspace_last_activity(user_id: str, project_slug: str) -> datetime | None:
    path = workspace_activity_path(user_id, project_slug)
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def workspace_secret_path(user_id: str, project_slug: str) -> Path:
    return workspace_root(user_id, project_slug) / "runtime-secret"


def workspace_stop_reason_path(user_id: str, project_slug: str) -> Path:
    return workspace_root(user_id, project_slug) / "stop-reason"


def write_workspace_stop_reason(user_id: str, project_slug: str, reason: str) -> None:
    path = workspace_stop_reason_path(user_id, project_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reason.strip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def read_workspace_stop_reason(user_id: str, project_slug: str) -> str | None:
    path = workspace_stop_reason_path(user_id, project_slug)
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def clear_workspace_stop_reason(user_id: str, project_slug: str) -> None:
    workspace_stop_reason_path(user_id, project_slug).unlink(missing_ok=True)


def write_workspace_secret(user_id: str, project_slug: str, password: str) -> None:
    path = workspace_secret_path(user_id, project_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(password, encoding="utf-8")
    path.chmod(0o600)


def read_workspace_secret(user_id: str, project_slug: str) -> str:
    path = workspace_secret_path(user_id, project_slug)
    if not path.is_file():
        raise RuntimeError(
            "Workspace runtime secret is missing. Stop and restart this workspace "
            "so it is recreated by the gateway-enabled runtime."
        )
    return path.read_text(encoding="utf-8").strip()


def workspace_host_port(slot_id: int) -> int:
    return settings.workspace_host_port_base + slot_id



def wait_workspace_ready(container_name: str, host_port: int, password: str) -> None:
    deadline = time.monotonic() + settings.workspace_ready_timeout_seconds
    credentials = base64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    last_error = "backend has not accepted a connection yet"

    while time.monotonic() < deadline:
        if not container_running(container_name):
            logs = _run(["logs", "--tail", "80", container_name], check=False)
            detail = (logs.stderr or logs.stdout or "no container logs available").strip()
            raise RuntimeError(f"OpenCode container exited before becoming ready: {detail}")

        connection = None
        try:
            connection = http.client.HTTPConnection("127.0.0.1", host_port, timeout=2)
            connection.request(
                "GET",
                "/global/health",
                headers={"Authorization": f"Basic {credentials}"},
            )
            response = connection.getresponse()
            response.read()
            if 200 <= response.status < 300:
                return
            last_error = f"health endpoint returned HTTP {response.status}"
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        finally:
            if connection is not None:
                connection.close()

        time.sleep(settings.workspace_ready_poll_interval_seconds)

    raise RuntimeError(
        f"OpenCode did not become ready within {settings.workspace_ready_timeout_seconds}s "
        f"on 127.0.0.1:{host_port} ({last_error})"
    )


def start_workspace(user_id: str, project_slug: str, workspace_id: str, slot_id: int) -> RuntimeResult:
    ensure_network()
    workspace, data, state, config = workspace_paths(user_id, project_slug)
    short = workspace_id.replace("-", "")[:12]
    container_name = f"oc-{slot_id}-{short}"
    password = secrets.token_urlsafe(32)
    host_port = workspace_host_port(slot_id)

    # Defensive cleanup in case a prior crash left the slot's name behind.
    _run(["rm", "-f", container_name], check=False)
    clear_workspace_stop_reason(user_id, project_slug)
    write_workspace_secret(user_id, project_slug, password)

    args = [
        "run", "-d",
        "--name", container_name,
        "--network", settings.podman_network,
        # The gateway is a native user service, so expose OpenCode only on
        # loopback. Nothing outside the host can connect directly to this port.
        "--publish", f"127.0.0.1:{host_port}:4096",
        "--memory", settings.workspace_memory,
        "--cpus", str(settings.workspace_cpus),
        "--pids-limit", str(settings.workspace_pids_limit),
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--userns", "keep-id:uid=10001,gid=10001",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
        "--tmpfs", "/home/opencode/.cache:rw,nosuid,nodev,size=512m,mode=1777",
        "--volume", f"{workspace}:/workspace:rw,Z",
        "--volume", f"{data}:/home/opencode/.local/share/opencode:rw,Z",
        "--volume", f"{state}:/home/opencode/.local/state:rw,Z",
        "--volume", f"{config}:/home/opencode/.config/opencode:rw,Z",
        "--env", "HOME=/home/opencode",
        "--env", "XDG_DATA_HOME=/home/opencode/.local/share",
        "--env", "XDG_STATE_HOME=/home/opencode/.local/state",
        "--env", "XDG_CACHE_HOME=/home/opencode/.cache",
        "--env", "XDG_CONFIG_HOME=/home/opencode/.config",
        "--env", "OPENCODE_SERVER_USERNAME=opencode",
        "--env", f"OPENCODE_SERVER_PASSWORD={password}",
    ]
    if settings.opencode_cors:
        args.extend(["--env", f"OPENCODE_CORS={settings.opencode_cors}"])
    args.extend([
        settings.workspace_image,
        "serve", "--hostname", "0.0.0.0", "--port", "4096"
    ])
    _run(args)
    try:
        wait_workspace_ready(container_name, host_port, password)
    except Exception:
        _run(["rm", "-f", container_name], check=False)
        raise
    touch_workspace_activity(user_id, project_slug)
    return RuntimeResult(container_name=container_name, basic_password=password, host_port=host_port)


def stop_workspace(container_name: str | None) -> None:
    if not container_name:
        return
    _run(["rm", "-f", container_name], check=False)


def container_running(container_name: str | None) -> bool:
    if not container_name:
        return False
    cp = _run(["inspect", "-f", "{{.State.Running}}", container_name], check=False)
    return cp.returncode == 0 and cp.stdout.strip() == "true"


def container_has_workspace_port(container_name: str | None, slot_id: int | None) -> bool:
    """Return True when the runtime exposes slot's expected loopback port."""
    if not container_name or slot_id is None:
        return False
    cp = _run(["port", container_name, "4096/tcp"], check=False)
    if cp.returncode != 0:
        return False
    expected = f"127.0.0.1:{workspace_host_port(slot_id)}"
    return any(line.strip() == expected for line in cp.stdout.splitlines())
