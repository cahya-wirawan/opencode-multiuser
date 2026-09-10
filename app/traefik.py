import os
from pathlib import Path
from .config import settings


def route_path(workspace_id: str) -> Path:
    return settings.expanded_traefik_dynamic_dir / f"workspace-{workspace_id}.yml"


def workspace_host(workspace_id: str) -> str:
    short = workspace_id.replace("-", "")[:16]
    return f"w-{short}.{settings.base_domain}"


def write_route(workspace_id: str, container_name: str) -> str:
    directory = settings.expanded_traefik_dynamic_dir
    directory.mkdir(parents=True, exist_ok=True)
    host = workspace_host(workspace_id)
    router = f"ws-{workspace_id.replace('-', '')[:16]}"
    tls_lines = ""
    if settings.traefik_tls:
        tls_lines = f"\n      tls:\n        certResolver: {settings.traefik_cert_resolver}"
    content = f'''http:\n  routers:\n    {router}:\n      rule: \"Host(`{host}`)\"\n      service: {router}\n      entryPoints:\n        - websecure{tls_lines}\n  services:\n    {router}:\n      loadBalancer:\n        servers:\n          - url: \"http://{container_name}:4096\"\n'''
    target = route_path(workspace_id)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(content)
    os.replace(tmp, target)
    return f"{settings.workspace_scheme}://{host}"


def remove_route(workspace_id: str) -> None:
    route_path(workspace_id).unlink(missing_ok=True)
