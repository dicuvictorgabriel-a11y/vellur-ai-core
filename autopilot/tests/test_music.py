"""Artist Desk: real music records — persistence, validation and truthful artwork links.

A release exists only when the artist creates it; the UI must survive a reload with the same data. The cover
link is accepted only when the image really exists (generated-images store, or an image attachment), so the
interface can never display an artwork that is not there.
"""
import httpx
import pytest

from app.api import build_admin
from app.router import Router

M = "/autopilot/api/music"
ADMIN = {"X-Vellur-Admin": "s" * 32}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}


@pytest.fixture
def music(rig):
    def build(**kw):
        ctrl = rig(**kw)
        admin = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=Router(ctrl))),
                                  base_url="https://ai.example")
        return ctrl, admin
    return build


async def test_music_requires_auth_and_starts_empty(music):
    _, admin = music()
    assert (await admin.get(M)).status_code in (401, 403)
    assert (await admin.post(M + "/releases", headers=ADMIN, json={"title": "x"})).status_code == 403
    r = await admin.get(M, headers=ADMIN)
    assert r.status_code == 200
    d = r.json()
    assert d["releases"] == []
    assert d["profile"]["artist_name"] == "" and d["profile"]["updated"] is None


async def test_release_create_persist_and_reload(music):
    ctrl, admin = music()
    r = await admin.post(M + "/releases", headers=WRITE, json={"title": "Ring City", "target_date": "2026-12-04",
                                                               "notes": "First single."})
    assert r.status_code == 200, r.text
    rel = r.json()
    assert rel["title"] == "Ring City" and rel["status"] == "planned" and rel["tracks"] == []
    r = await admin.patch(M + f"/releases/{rel['id']}", headers=WRITE, json={"status": "released"})
    assert r.status_code == 200 and r.json()["status"] == "released"
    r = await admin.post(M + f"/releases/{rel['id']}/tracks", headers=WRITE, json={"title": "Neon Hours",
                                                                                    "duration": "3:21"})
    assert r.status_code == 200 and r.json()["position"] == 1
    # a fresh client sees the same data (real persistence, no session state)
    d = (await admin.get(M, headers=ADMIN)).json()
    assert len(d["releases"]) == 1 and d["releases"][0]["status"] == "released"
    assert d["releases"][0]["tracks"][0]["title"] == "Neon Hours"
    assert d["releases"][0]["tracks"][0]["duration"] == "3:21"


async def test_release_validation_and_delete(music):
    ctrl, admin = music()
    assert (await admin.post(M + "/releases", headers=WRITE, json={"title": "   "})).status_code == 400
    assert (await admin.post(M + "/releases", headers=WRITE, json={"title": "ok", "status": "draft"})).status_code == 400
    assert (await admin.patch(M + "/releases/999", headers=WRITE, json={"notes": "x"})).status_code == 404
    rel = (await admin.post(M + "/releases", headers=WRITE, json={"title": "Short set"})).json()
    tr = (await admin.post(M + f"/releases/{rel['id']}/tracks", headers=WRITE, json={"title": "One"})).json()
    assert (await admin.post(M + f"/releases/{rel['id']}/tracks", headers=WRITE, json={"title": " "})).status_code == 400
    assert (await admin.delete(M + f"/tracks/{tr['id']}", headers=WRITE)).json() == {"ok": True}
    assert (await admin.delete(M + f"/releases/{rel['id']}", headers=WRITE)).json() == {"ok": True}
    d = (await admin.get(M, headers=ADMIN)).json()
    assert d["releases"] == []


async def test_cover_only_links_to_real_images(music):
    ctrl, admin = music()
    assert (await admin.post(M + "/releases", headers=WRITE,
                             json={"title": "No such cover", "cover_image_id": "img_missing"})).status_code == 400
    now = __import__("time").time()
    ctrl.db.x("INSERT INTO generated_images(id,created,prompt,provider,model,target,mime,size,sha256) "
              "VALUES('img_real',?, 'p', 'mock', 'm', 't', 'image/png', 10, 'x')", (now,))
    rel = (await admin.post(M + "/releases", headers=WRITE,
                            json={"title": "With cover", "cover_image_id": "img_real"})).json()
    assert rel["cover_image_id"] == "img_real"
    assert (await admin.patch(M + f"/releases/{rel['id']}", headers=WRITE,
                              json={"cover_image_id": "img_other"})).status_code == 400


async def test_profile_saved_and_returned(music):
    ctrl, admin = music()
    r = await admin.put(M + "/profile", headers=WRITE, json={"artist_name": "Ring City", "genre": "indie"})
    assert r.status_code == 200
    assert r.json()["profile"]["artist_name"] == "Ring City" and r.json()["profile"]["genre"] == "indie"
    d = (await admin.get(M, headers=ADMIN)).json()
    assert d["profile"]["artist_name"] == "Ring City"
    r = await admin.put(M + "/profile", headers=WRITE, json={"artist_name": "", "genre": "", "bio": "", "links": ""})
    assert (await admin.get(M, headers=ADMIN)).json()["profile"]["artist_name"] == ""
