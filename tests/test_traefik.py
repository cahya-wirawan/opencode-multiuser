from pathlib import Path
from app import traefik


def test_workspace_host():
    assert traefik.workspace_host("12345678-abcd-1234-abcd-123456789012").startswith("w-12345678abcd1234.")
