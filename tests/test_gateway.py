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


def test_workspace_stop_reason_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runtime.settings, "data_root", str(tmp_path))
    runtime.write_workspace_stop_reason("user-1", "project-1", "idle")
    assert runtime.read_workspace_stop_reason("user-1", "project-1") == "idle"
    runtime.clear_workspace_stop_reason("user-1", "project-1")
    assert runtime.read_workspace_stop_reason("user-1", "project-1") is None


def test_workspace_activity_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runtime.settings, "data_root", str(tmp_path))
    runtime.touch_workspace_activity("user-1", "project-1")
    activity = runtime.workspace_activity_path("user-1", "project-1")
    assert activity.is_file()
    assert runtime.workspace_last_activity("user-1", "project-1") is not None


def test_wait_workspace_ready(monkeypatch):
    class FakeResponse:
        status = 200
        def read(self):
            return b'{"healthy":true}'

    class FakeConnection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return FakeResponse()
        def close(self):
            pass

    monkeypatch.setattr(runtime, "container_running", lambda _: True)
    monkeypatch.setattr(runtime.http.client, "HTTPConnection", FakeConnection)
    runtime.wait_workspace_ready("oc-test", 41001, "secret")


def test_portal_widget_injection():
    from app.portal import inject_portal_widget
    html = '<!doctype html><html><body><main>OpenCode</main></body></html>'
    out = inject_portal_widget(html, 'project-one')
    assert '<main>OpenCode</main>' in out
    assert 'id="oc-mu-portal"' in out
    assert '/_portal/widget.js' in out
    assert '/_portal/widget.css' in out
    assert 'href="/dashboard"' in out
    assert '/_portal/dashboard' not in out
    assert out.index('id="oc-mu-portal"') < out.lower().rindex('</body>')


def test_portal_widget_injection_is_idempotent():
    from app.portal import inject_portal_widget
    first = inject_portal_widget('<html><body>x</body></html>', 'project-one')
    second = inject_portal_widget(first, 'project-one')
    assert second.count('id="oc-mu-portal"') == 1


def test_forward_headers_forces_identity_encoding(monkeypatch):
    monkeypatch.setattr(gateway.settings, "session_cookie_name", "oc_session")
    monkeypatch.setattr(gateway.settings, "workspace_cookie_name", "oc_workspace")
    headers = gateway._forward_headers([("Accept-Encoding", "gzip, br")], "runtime-secret")
    assert headers["accept-encoding"] == "identity"


def test_workspace_model_uses_partial_active_index():
    from app.models import Workspace
    indexes = {idx.name: idx for idx in Workspace.__table__.indexes}
    idx = indexes["uq_active_workspace"]
    assert idx.unique is True
    assert [col.name for col in idx.columns] == ["user_id", "project_slug"]
    assert "STOPPED" not in str(idx.dialect_options["postgresql"]["where"])
    assert "RUNNING" in str(idx.dialect_options["postgresql"]["where"])


def test_modern_login_ui_is_local_and_accessible():
    from app.ui import login_page_html
    html = login_page_html()
    assert 'OpenCode Workspace Portal' in html
    assert '/_ui/app.css' in html
    assert 'cdn.tailwindcss.com' not in html
    assert 'aria-label="Show password"' in html
    assert 'Signing in…' in html


def test_modern_dashboard_has_operational_feedback():
    from app.ui import dashboard_page_html
    html = dashboard_page_html('alice', '', 10, 8, 30)
    assert 'Your workspaces' in html
    assert 'Available slots' in html
    assert 'Starting container…' in html
    assert 'Waiting for OpenCode service…' in html
    assert 'Stopping workspace…' in html
    assert 'Logging out…' in html
    assert 'toast-region' in html
    assert 'stop-dialog' in html


def test_login_ui_supports_oidc_and_local_fallback():
    from app.ui import login_page_html
    html = login_page_html(
        oidc_enabled=True,
        oidc_display_name="Example SSO",
        local_auth_enabled=True,
        registration_enabled=True,
    )
    assert '/auth/oidc/login' in html
    assert 'Continue with Example SSO' in html
    assert 'or use a local account' in html
    assert 'href="/register"' in html
    assert 'id="login"' in html


def test_login_ui_can_be_oidc_only():
    from app.ui import login_page_html
    html = login_page_html(
        oidc_enabled=True,
        oidc_display_name="Enterprise Identity",
        local_auth_enabled=False,
        registration_enabled=False,
    )
    assert 'Continue with Enterprise Identity' in html
    assert 'id="login"' not in html
    assert 'href="/register"' not in html


def test_registration_ui_describes_first_user_admin():
    from app.ui import registration_page_html
    html = registration_page_html(first_user=True)
    assert 'Bootstrap administrator' in html
    assert 'Admin role' in html
    assert '/auth/register' in html
    assert 'Creating account…' in html


def test_first_user_gets_admin_role():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import User
    from app.security import role_for_new_user

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        assert role_for_new_user(db) == 'admin'
        db.add(User(username='first', password_hash='x', role='admin', auth_provider='local'))
        db.commit()
        assert role_for_new_user(db) == 'developer'


def test_oidc_provisioning_is_stable_and_uses_subject(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app import oidc

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(oidc.settings, 'oidc_issuer', 'https://issuer.example')
    monkeypatch.setattr(oidc.settings, 'oidc_auto_provision', True)
    monkeypatch.setattr(oidc.settings, 'oidc_default_role', 'developer')
    with Session() as db:
        claims = {'sub': 'abc-123', 'preferred_username': 'alice', 'email': 'alice@example.com', 'name': 'Alice Example'}
        user1 = oidc.provision_oidc_user(db, claims)
        user2 = oidc.provision_oidc_user(db, claims)
        assert user1.id == user2.id
        assert user1.role == 'admin'
        assert user1.auth_provider == 'oidc'
        assert user1.password_hash is None
        assert user1.email == 'alice@example.com'
