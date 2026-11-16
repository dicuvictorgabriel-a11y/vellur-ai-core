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


async def test_3d_studio_two_prompt_futuristic_room_persistence_glb_and_snapshot(rig):
    app = build_admin(rig())
    mutate = {**ADMIN, "X-Vellur-Request": "1"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ap") as client:
        assert (await client.get("/autopilot/3d/scene_studio.js", headers=ADMIN)).status_code == 200

        prompt1 = (
            "Create a minimalist futuristic room with frosted glass panels, "
            "polished concrete flooring, a chrome sphere, and soft studio lighting."
        )
        # Missing CSRF header must be rejected
        assert (await client.post("/autopilot/api/studio3d/scenes", headers=ADMIN, json={"prompt": prompt1})).status_code == 403
        r1 = await client.post("/autopilot/api/studio3d/scenes", headers=mutate, json={"prompt": prompt1, "use_ai": False})
        assert r1.status_code == 200
        rec1 = r1.json()
        sid = rec1["id"]
        assert rec1["version"] == 1
        sc1 = rec1["scene"]
        roles = {o["role"] for o in sc1["objects"]}
        assert {"floor", "glass", "hero"} <= roles
        glass1 = [o for o in sc1["objects"] if o["role"] == "glass"]
        sphere1 = next(o for o in sc1["objects"] if o["primitive"] == "sphere")
        floor1 = next(o for o in sc1["objects"] if o["role"] == "floor")
        assert len(glass1) >= 2
        assert floor1["material"]["preset"] == "polished_concrete"
        assert sphere1["material"]["preset"] == "chrome"
        initial_trans = glass1[0]["material"]["transmission"]
        initial_opac = glass1[0]["material"]["opacity"]
        initial_sphere_x = sphere1["position"][0]
        initial_cam_pos = list(sc1["camera"]["position"])

        prompt2 = "Make the glass more transparent, move the sphere to the left, and adjust the camera."
        r2 = await client.post(
            f"/autopilot/api/studio3d/scenes/{sid}/prompt",
            headers=mutate,
            json={"prompt": prompt2, "use_ai": False},
        )
        assert r2.status_code == 200
        rec2 = r2.json()
        assert rec2["version"] == 2
        assert len(rec2["history"]) == 2

        # Reopen persistent scene from API to verify full database round-trip
        reopened = (await client.get(f"/autopilot/api/studio3d/scenes/{sid}", headers=ADMIN)).json()
        sc2 = reopened["scene"]
        glass2 = [o for o in sc2["objects"] if o["role"] == "glass"]
        sphere2 = next(o for o in sc2["objects"] if o["primitive"] == "sphere")
        assert glass2[0]["material"]["transmission"] > initial_trans
        assert glass2[0]["material"]["opacity"] < initial_opac
        assert sphere2["position"][0] < initial_sphere_x
        assert sc2["camera"]["position"] != initial_cam_pos

        # Direct inspector save round-trip
        sphere2["material"]["roughness"] = 0.14
        r_put = await client.put(
            f"/autopilot/api/studio3d/scenes/{sid}",
            headers=mutate,
            json={"scene": sc2, "note": "Inspector roughness tweak"},
        )
        assert r_put.status_code == 200
        assert r_put.json()["version"] == 3

        # Binary GLB 2.0 export validation
        glb_res = await client.get(f"/autopilot/api/studio3d/scenes/{sid}/export.glb", headers=ADMIN)
        assert glb_res.status_code == 200
        assert glb_res.headers["content-type"].startswith("model/gltf-binary")
        glb = glb_res.content
        magic, version, total_len = struct.unpack_from("<4sII", glb, 0)
        assert magic == b"glTF" and version == 2 and total_len == len(glb)
        json_len, json_type = struct.unpack_from("<II", glb, 12)
        assert json_type == 0x4E4F534A
        gltf_json = json.loads(glb[20 : 20 + json_len].decode("utf-8").strip())
        assert "KHR_materials_transmission" in gltf_json.get("extensionsUsed", [])
        assert len(gltf_json["meshes"]) == len(sc2["objects"])
        bin_offset = 20 + json_len
        bin_len, bin_type = struct.unpack_from("<II", glb, bin_offset)
        assert bin_type == 0x004E4942 and bin_len > 100

        # Server-side isometric PNG snapshot + retrieval
        snap_res = await client.post(
            f"/autopilot/api/studio3d/scenes/{sid}/snapshots",
            headers=mutate,
            json={"note": "Server isometric preview"},
        )
        assert snap_res.status_code == 200
        snap = snap_res.json()
        assert snap["width"] >= 320 and snap["height"] >= 200
        png_res = await client.get(snap["url"], headers=ADMIN)
        assert png_res.status_code == 200
        assert png_res.headers["content-type"] == "image/png"
        assert png_res.content[:8] == b"\x89PNG\r\n\x1a\n"



async def test_precision_studio_is_gone_and_3d_studio_stays(rig):
    """Owner decision 2026-11-16: Precision Studio removed. Its routes and files must not come back."""
    import os
    import httpx
    from app.api import build_admin
    static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")
    for name in ("studio.html", "studio.js", "studio.css", "geometry.js"):
        assert not os.path.exists(os.path.join(static, name)), name
    html = open(os.path.join(static, "dashboard.html")).read()
    assert "/autopilot/studio/" not in html and "Precision Studio" not in html
    ctrl = rig()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl)), base_url="https://ai.example") as c:
        h = {"X-Vellur-Admin": "s" * 32}
        for url in ("/autopilot/studio/", "/autopilot/studio/studio.js", "/autopilot/studio/geometry.js"):
            assert (await c.get(url, headers=h)).status_code == 404, url
        assert (await c.get("/autopilot/3d/", headers=h)).status_code == 200
