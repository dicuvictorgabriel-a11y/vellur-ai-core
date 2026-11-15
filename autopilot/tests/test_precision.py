import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest

from app.api import build_admin

ROOT = Path(__file__).resolve().parents[1]


def test_geometry_and_svg_export():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    result = subprocess.run(["node", "--test", str(ROOT / "tests/precision_geometry.test.js")],
                            capture_output=True, text=True, check=True)
    line = next(line for line in result.stdout.splitlines() if "SVG_FOR_PARSE=" in line)
    svg = ET.fromstring(line.split("SVG_FOR_PARSE=", 1)[1])
    assert svg.tag == "{http://www.w3.org/2000/svg}svg"
    path = svg.find("{http://www.w3.org/2000/svg}path")
    assert path is not None and path.attrib["stroke-width"] == "4"
    assert " C " in path.attrib["d"]


def test_studio_document_editing_and_project_files():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = subprocess.run(["node", "--test", str(ROOT / "tests/precision_studio.test.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


async def test_studio_requires_login_and_serves_only_known_assets(rig):
    app = build_admin(rig())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ap") as client:
        for url in ("/autopilot/studio/", "/autopilot/studio/geometry.js"):
            assert (await client.get(url)).status_code == 401
            assert (await client.get(url, headers={"X-Vellur-Admin": "s" * 32})).status_code == 200
        assert (await client.get("/autopilot/studio/dashboard.html", headers={"X-Vellur-Admin": "s" * 32})).status_code == 404
