import re
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from .config import settings

_SAFE = re.compile(r"^[a-zA-Z0-9_.-]+$")


@dataclass
class RuntimeResult:
    container_name: str
    basic_password: str


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [settings.podman_bin, *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )


def ensure_network() -> None:
    result = _run(["network", "exists", settings.podman_network], check=False)
    if result.returncode != 0:
        _run(["network", "create", settings.podman_network])


def workspace_paths(user_id: str, project_slug: str) -> tuple[Path, Path, Path]:
    if not _SAFE.match(project_slug):
        raise ValueError("invalid project slug")
    root = settings.expanded_data_root / "users" / user_id / project_slug
    workspace = root / "workspace"
    data = root / "opencode-data"
    config = root / "opencode-config"
    for path in (workspace, data, config):
        path.mkdir(parents=True, exist_ok=True)
    return workspace, data, config


def start_workspace(user_id: str, project_slug: str, workspace_id: str, slot_id: int) -> RuntimeResult:
    ensure_network()
    workspace, data, config = workspace_paths(user_id, project_slug)
    short = workspace_id.replace("-", "")[:12]
    container_name = f"oc-{slot_id}-{short}"
    password = secrets.token_urlsafe(32)

    # Defensive cleanup in case a prior crash left the slot's name behind.
    _run(["rm", "-f", container_name], check=False)

    args = [
        "run", "-d",
        "--name", container_name,
        "--network", settings.podman_network,
        "--memory", settings.workspace_memory,
        "--cpus", str(settings.workspace_cpus),
        "--pids-limit", str(settings.workspace_pids_limit),
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--userns", "keep-id:uid=1000,gid=1000",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g",
        "--volume", f"{workspace}:/workspace:rw,Z",
        "--volume", f"{data}:/home/opencode/.local/share/opencode:rw,Z",
        "--volume", f"{config}:/home/opencode/.config/opencode:rw,Z",
        "--env", "HOME=/home/opencode",
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
    return RuntimeResult(container_name=container_name, basic_password=password)


def stop_workspace(container_name: str | None) -> None:
    if not container_name:
        return
    _run(["rm", "-f", container_name], check=False)


def container_running(container_name: str | None) -> bool:
    if not container_name:
        return False
    cp = _run(["inspect", "-f", "{{.State.Running}}", container_name], check=False)
    return cp.returncode == 0 and cp.stdout.strip() == "true"
