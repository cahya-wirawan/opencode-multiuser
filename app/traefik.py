from .config import settings


def workspace_open_url(workspace_id: str) -> str:
    return f"{settings.control_plane_url.rstrip('/')}/open/{workspace_id}"


def cleanup_legacy_workspace_routes() -> None:
    """Remove v5 per-workspace Traefik route files after upgrading to v6."""
    directory = settings.expanded_traefik_dynamic_dir
    if not directory.exists():
        return
    for path in directory.glob("workspace-*.yml"):
        path.unlink(missing_ok=True)
