import importlib.util
import json
import struct
import sys
from pathlib import Path

import httpx
import pytest

from app.api import build_admin

ROOT = Path(__file__).resolve().parents[1]
ADMIN = {"X-Vellur-Admin": "s" * 32}
spec = importlib.util.spec_from_file_location("vellur_runner_3d", ROOT.parent / "agent" / "runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def glb(size=20):
    return b"glTF" + struct.pack("<I", 2) + struct.pack("<I", size) + b"0" * (size - 12)


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "repo"
    write(repo / "models" / "scene.glb", glb())
    write(repo / "models" / "scene.bin", b"BIN!")
    write(repo / "models" / "scene.gltf", json.dumps({
        "asset": {"version": "2.0"}, "buffers": [{"uri": "scene.bin", "byteLength": 4}],
    }).encode())
    write(repo / "models" / "remote.gltf", json.dumps({
        "asset": {"version": "2.0"}, "buffers": [{"uri": "https://example.com/x.bin"}],
    }).encode())
    write(repo / "models" / "missing.gltf", json.dumps({
        "asset": {"version": "2.0"}, "buffers": [{"uri": "nope.bin"}],
    }).encode())
    write(repo / "models" / "bad.gltf", json.dumps({"asset": []}).encode())
    write(repo / "models" / "mesh.obj", b"o cube\nv 0 0 0\n")
    write(repo / "models" / "mesh.stl", b"solid cube\nendsolid cube\n")
    write(repo / "scene.blend", b"BLENDER-v300")
    write(tmp_path / "derived" / "scene-abc123.glb", glb())
    write(tmp_path / "derived" / "scene-abc123.glb.json", json.dumps({"source": "scene.blend"}).encode())
    write(tmp_path / "secret.txt", b"outside")
    write(repo / "secret.html", b"<script>alert(1)</script>")
    return {"dir": str(tmp_path)}


def by_path(models):
    return {m["path"]: m for m in models}


def test_model_discovery_validates_and_never_leaves_the_workspace(workspace, monkeypatch):
    monkeypatch.setattr(runner, "blender_bin", lambda: "")
    models = by_path(runner.model_files(workspace, refresh=True))
    assert models["models/scene.glb"]["previewable"] is True
    assert models["models/scene.gltf"]["previewable"] is True
    assert models["models/mesh.obj"]["previewable"] is True
    assert models["models/mesh.stl"]["previewable"] is True
    assert models["models/remote.gltf"]["previewable"] is False
    assert "external URL" in models["models/remote.gltf"]["reason"]
    assert models["models/missing.gltf"]["previewable"] is False
    assert "missing file" in models["models/missing.gltf"]["reason"]
    assert models["models/bad.gltf"]["previewable"] is False
    assert "asset metadata" in models["models/bad.gltf"]["reason"]
    assert models["scene.blend"]["previewable"] is False and models["scene.blend"]["needsExport"] is True
    assert models["@derived/scene-abc123.glb"]["previewable"] is True
    assert models["@derived/scene-abc123.glb"]["sourcePath"] == "scene.blend"
    assert models["@derived/scene-abc123.glb"]["name"] == "scene.blend"
    assert all(not m["path"].startswith("..") for m in models.values())
    assert "secret.txt" not in models


def test_model_plan_and_info(workspace, monkeypatch):
    monkeypatch.setattr(runner, "blender_bin", lambda: "")
    plan = runner.preview_plan(workspace)
    assert plan["mode"] == "model" and plan["models"]
    info = runner.preview_info(1, workspace)
    assert info["mode"] == "model" and info["model"]
    assert info["model"] in {m["path"] for m in info["models"] if m["previewable"]}
    assert info["blender"] is False and info["models"]


class Handler:
    def __init__(self, path):
        self.path = path
        self.client_address = ("127.0.0.1", 1)
        self.headers = {}

    def send(self, code, obj=None, raw=None, ctype=None):
        return {"code": code, "json": obj, "raw": raw, "type": ctype}


def test_auto_shots_skips_model_previews(workspace, monkeypatch):
    monkeypatch.setattr(runner, "blender_bin", lambda: "")
    notices = []
    monkeypatch.setattr(runner, "screenshot_notice", lambda t, message: notices.append(message))
    runner.auto_shots(1, workspace)
    assert notices == ["3D model preview has no web page; open it in VELLUR 3D Studio"]


def test_serve_asset_confines_paths_and_uses_model_mime(workspace, monkeypatch):
    monkeypatch.setattr(runner, "blender_bin", lambda: "")
    ok = runner.serve_asset(Handler("/tasks/1/asset/models/scene.glb"), workspace, "models/scene.glb")
    assert ok["code"] == 200 and ok["raw"] == glb() and ok["type"] == "model/gltf-binary"
    derived = runner.serve_asset(Handler("/tasks/1/asset/@derived/scene-abc123.glb"), workspace, "@derived/scene-abc123.glb")
    assert derived["code"] == 200 and derived["type"] == "model/gltf-binary"
    assert runner.serve_asset(Handler(""), workspace, "../secret.txt")["code"] == 404
    assert runner.serve_asset(Handler(""), workspace, "models/nope.glb")["code"] == 404
    assert runner.serve_asset(Handler(""), workspace, "secret.html")["code"] == 404
    assert runner.serve_asset(Handler(""), workspace, "")["code"] == 404


def test_blend_export_requires_blender_and_is_optional(workspace, monkeypatch):
    monkeypatch.setattr(runner, "blender_bin", lambda: "")
    with pytest.raises(RuntimeError, match="Blender is not installed"):
        runner.export_blend(workspace, "scene.blend")
    monkeypatch.setattr(runner, "blender_bin", lambda: "/bin/false")
    with pytest.raises(ValueError, match="unknown Blender file"):
        runner.export_blend(workspace, "../scene.blend")


def test_blend_export_runs_a_real_binary_and_returns_a_derived_entry(workspace, monkeypatch, tmp_path):
    fake = tmp_path / "fake-blender"
    fake.write_text(f"""#!{sys.executable}
import struct, sys
out = sys.argv[-1]
open(out, "wb").write(b"glTF" + struct.pack("<I", 2) + struct.pack("<I", 20) + b"0" * 8)
""")
    fake.chmod(0o755)
    monkeypatch.setattr(runner, "blender_bin", lambda: str(fake))
    entry = runner.export_blend(workspace, "scene.blend")
    assert entry["path"].startswith("@derived/") and entry["previewable"] is True
    assert entry["sourcePath"] == "scene.blend" and entry["name"] == "scene.blend"
    assert (Path(workspace["dir"]) / "derived" / (entry["path"].split("/", 1)[1] + ".json")).is_file()


async def test_3d_studio_requires_login_and_serves_only_local_files(rig):
    app = build_admin(rig())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ap") as client:
        for url in ("/autopilot/3d/", "/autopilot/3d/studio3d.js", "/autopilot/3d/vendor/three.module.min.js"):
            assert (await client.get(url)).status_code == 401
            r = await client.get(url, headers=ADMIN)
            assert r.status_code == 200
        page = await client.get("/autopilot/3d/", headers=ADMIN)
        csp = page.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'self'" in csp and "object-src 'none'" in csp
        assert page.headers["cache-control"] == "no-store"
        assert (await client.get("/autopilot/3d/%2e%2e/dashboard.html", headers=ADMIN)).status_code == 404
        assert (await client.get("/autopilot/3d/does-not-exist.js", headers=ADMIN)).status_code == 404


def test_3d_studio_opens_local_models_without_uploading():
    import os
    from app.api import STUDIO3D_HEADERS
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "3d")
    js = open(os.path.join(root, "studio3d.js")).read()
    html = open(os.path.join(root, "studio3d.html")).read()
    assert 'id="localFile"' in html and 'accept=".glb,.gltf,.obj,.stl"' in html and 'id="emptyOpen"' in html
    assert "URL.createObjectURL(file)" in js and "entry.local ? entry.url" in js
    # loaders fetch the object URL: connect-src must allow blob:, and nothing else was widened
    csp = STUDIO3D_HEADERS["Content-Security-Policy"]
    assert "connect-src 'self' blob:;" in csp and "script-src 'self';" in csp and "default-src 'self';" in csp
