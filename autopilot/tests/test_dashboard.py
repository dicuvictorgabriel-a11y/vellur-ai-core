import os
import shutil
import subprocess

import pytest

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "dashboard.html")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_dashboard_script_parses(tmp_path):
    html = open(PAGE).read()
    js = html[html.index("<script>") + 8: html.index("</script>")]
    f = tmp_path / "dash.js"
    f.write_text(js)
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_dashboard_has_no_basic_auth_or_secrets():
    html = open(PAGE).read()
    assert "/autopilot/login" not in html and "X-Vellur-Admin" not in html and "localStorage" not in html
