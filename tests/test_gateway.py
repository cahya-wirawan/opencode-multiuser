from pathlib import Path

from app import gateway, runtime, traefik


def test_workspace_open_url(monkeypatch):
    monkeypatch.setattr(traefik.settings, "control_plane_url", "http://code.example.com:8443")
    assert traefik.workspace_open_url("abc-123") == "http://code.example.com:8443/open/abc-123"


def test_workspace_host_port(monkeypatch):
    monkeypatch.setattr(runtime.settings, "workspace_host_port_base", 41000)
    assert runtime.workspace_host_port(1) == 41001
    assert runtime.workspace_host_port(10) == 41010


def test_gateway_strips_routing_and_session_headers(monkeypatch):
    monkeypatch.setattr(gateway.settings, "session_cookie_name", "oc_session")
    monkeypatch.setattr(gateway.settings, "workspace_cookie_name", "oc_workspace")
    headers = gateway._forward_headers(
        [
            ("Host", "code.example.com"),
            ("Authorization", "Bearer user-token"),
            ("X-OpenCode-Workspace", "oc-1-abc"),
            ("Cookie", "oc_session=secret; oc_workspace=abc; theme=dark"),
            ("Accept", "application/json"),
        ],
        "runtime-secret",
    )
    assert headers["authorization"].startswith("Basic ")
    assert headers["cookie"] == "theme=dark"
    assert "Host" not in headers
    assert "X-OpenCode-Workspace" not in headers
    assert headers["Accept"] == "application/json"


def test_cleanup_legacy_workspace_routes(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(traefik.settings, "traefik_dynamic_dir", str(tmp_path))
    (tmp_path / "control-plane.yml").write_text("keep")
    (tmp_path / "workspace-old.yml").write_text("remove")
    traefik.cleanup_legacy_workspace_routes()
    assert (tmp_path / "control-plane.yml").exists()
    assert not (tmp_path / "workspace-old.yml").exists()
