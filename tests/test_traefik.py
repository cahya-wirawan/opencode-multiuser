from app import traefik


def test_workspace_open_url(monkeypatch):
    monkeypatch.setattr(traefik.settings, "control_plane_url", "https://code.example.test/")

    assert (
        traefik.workspace_open_url("12345678-abcd-1234-abcd-123456789012")
        == "https://code.example.test/open/12345678-abcd-1234-abcd-123456789012"
    )
