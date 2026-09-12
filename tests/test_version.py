import re
import subprocess
import sys
from pathlib import Path

from app import __version__
from app.ui import login_page_html, registration_page_html


SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def test_application_version_is_semver():
    assert __version__ == "6.8.0"
    assert SEMVER_RE.fullmatch(__version__)


def test_version_is_visible_in_portal_ui():
    assert f"v{__version__}" in login_page_html()
    assert f"v{__version__}" in registration_page_html()


def test_version_cli_show():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "version.py"), "show"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == __version__
