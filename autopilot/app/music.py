"""Music: the artist's real catalog — releases, tracks and a small artist profile.

Everything here is artist-entered and persisted in SQLite; nothing is invented. A release status is exactly what
the artist set (planned / released), and the optional cover is only shown when it links to a real uploaded or
generated image. Registered from api.py behind the same admin / admin_write guards as every other endpoint.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .lifecycle import Controller

STATUSES = ("planned", "released")


def err(code: str, message: str, status: Optional[int] = None) -> JSONResponse:
    if status is None:
        status = 404 if code == "not_found" else 400
    return JSONResponse(status_code=status, content={"error": code, "message": message})


def clean(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


class ProfileBody(BaseModel):
    artist_name: str = ""
    genre: str = ""
    bio: str = ""
    links: str = ""


class ReleaseBody(BaseModel):
    title: str
    artist: str = ""
    status: str = "planned"
    target_date: str = ""
    released_at: str = ""
    notes: str = ""
    cover_image_id: str = ""


class PatchBody(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    status: Optional[str] = None
    target_date: Optional[str] = None
    released_at: Optional[str] = None
    notes: Optional[str] = None
    cover_image_id: Optional[str] = None


class TrackBody(BaseModel):
    title: str
    duration: str = ""
    notes: str = ""


def register(app: FastAPI, ctrl: Controller, admin, admin_write) -> None:
    db = ctrl.db

    def valid_cover(image_id: str) -> bool:
        if not image_id:
            return True
        if not db.one("SELECT id FROM generated_images WHERE id=?", (image_id,)):
            return bool(db.one("SELECT id FROM attachments WHERE id=? AND kind='image'", (image_id,)))
        return True

    def tracks_for(release_id: int) -> list[dict]:
        return db.all("SELECT id,title,duration,notes,position,created FROM music_tracks "
                      "WHERE release_id=? ORDER BY position, id", (release_id,))

    def release_out(row: dict) -> dict:
        return {**row, "tracks": tracks_for(row["id"])}

    def check_body(body: dict) -> Optional[JSONResponse]:
        title = clean(body.get("title") or "", 200)
        if not 1 <= len(title) <= 200:
            return err("invalid", "A release needs a title (1-200 characters).")
        if body.get("status") is not None and body["status"] not in STATUSES:
            return err("invalid", "Status is planned or released.")
        if not valid_cover(body.get("cover_image_id") or ""):
            return err("invalid", "The artwork link must point to a real image in NOVA.")
        return None

    @app.get("/autopilot/api/music", dependencies=[Depends(admin)])
    async def music_overview():
        profile = db.one("SELECT artist_name,genre,bio,links,updated FROM music_profile WHERE id=1")
        releases = [release_out(r) for r in db.all("SELECT * FROM music_releases ORDER BY updated DESC, id DESC")]
        return {"profile": profile or {"artist_name": "", "genre": "", "bio": "", "links": "", "updated": None},
                "releases": releases}

    @app.put("/autopilot/api/music/profile", dependencies=[Depends(admin_write)])
    async def music_profile_put(body: ProfileBody):
        now = time.time()
        db.x("INSERT INTO music_profile(id,artist_name,genre,bio,links,updated) VALUES(1,?,?,?,?,?) "
             "ON CONFLICT(id) DO UPDATE SET artist_name=excluded.artist_name, genre=excluded.genre, "
             "bio=excluded.bio, links=excluded.links, updated=excluded.updated",
             (clean(body.artist_name, 120), clean(body.genre, 120), (body.bio or "").strip()[:4000],
              (body.links or "").strip()[:4000], now))
        db.event("info", "music", "artist profile saved")
        return {"profile": db.one("SELECT artist_name,genre,bio,links,updated FROM music_profile WHERE id=1")}

    @app.post("/autopilot/api/music/releases", dependencies=[Depends(admin_write)])
    async def music_create(body: ReleaseBody):
        bad = check_body({"title": body.title, "status": body.status, "cover_image_id": body.cover_image_id})
        if bad:
            return bad
        now = time.time()
        cur = db.x("INSERT INTO music_releases(title,artist,status,target_date,released_at,notes,cover_image_id,"
                   "created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                   (clean(body.title, 200), clean(body.artist, 120), body.status, clean(body.target_date, 40),
                    clean(body.released_at, 40), (body.notes or "").strip()[:4000], body.cover_image_id, now, now))
        db.event("info", "music", f"release {cur.lastrowid} created: {clean(body.title, 80)}")
        return release_out(db.one("SELECT * FROM music_releases WHERE id=?", (cur.lastrowid,)))

    @app.get("/autopilot/api/music/releases/{rid}", dependencies=[Depends(admin)])
    async def music_get(rid: int):
        row = db.one("SELECT * FROM music_releases WHERE id=?", (rid,))
        return release_out(row) if row else err("not_found", "Unknown release.")

    @app.patch("/autopilot/api/music/releases/{rid}", dependencies=[Depends(admin_write)])
    async def music_patch(rid: int, body: PatchBody):
        row = db.one("SELECT * FROM music_releases WHERE id=?", (rid,))
        if not row:
            return err("not_found", "Unknown release.")
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        if "title" in changes:
            changes["title"] = clean(changes["title"], 200)
            if not changes["title"]:
                return err("invalid", "A release needs a title.")
        if "status" in changes and changes["status"] not in STATUSES:
            return err("invalid", "Status is planned or released.")
        for k, limit in (("artist", 120), ("target_date", 40), ("released_at", 40)):
            if k in changes:
                changes[k] = clean(changes[k], limit)
        if "notes" in changes:
            changes["notes"] = (changes["notes"] or "").strip()[:4000]
        if "cover_image_id" in changes:
            if not valid_cover(changes["cover_image_id"]):
                return err("invalid", "The artwork link must point to a real image in NOVA.")
        if changes:
            changes["updated"] = time.time()
            sets = ",".join(f"{k}=?" for k in changes)
            db.x(f"UPDATE music_releases SET {sets} WHERE id=?", (*changes.values(), rid))
            db.event("info", "music", f"release {rid} updated")
        return release_out(db.one("SELECT * FROM music_releases WHERE id=?", (rid,)))

    @app.delete("/autopilot/api/music/releases/{rid}", dependencies=[Depends(admin_write)])
    async def music_delete(rid: int):
        row = db.one("SELECT id,title FROM music_releases WHERE id=?", (rid,))
        if not row:
            return err("not_found", "Unknown release.")
        db.x("DELETE FROM music_tracks WHERE release_id=?", (rid,))
        db.x("DELETE FROM music_releases WHERE id=?", (rid,))
        db.event("info", "music", f"release {rid} deleted: {row['title'][:80]}")
        return {"ok": True}

    @app.post("/autopilot/api/music/releases/{rid}/tracks", dependencies=[Depends(admin_write)])
    async def track_create(rid: int, body: TrackBody):
        title = clean(body.title, 200)
        if not 1 <= len(title) <= 200:
            return err("invalid", "A track needs a title (1-200 characters).")
        if not db.one("SELECT id FROM music_releases WHERE id=?", (rid,)):
            return err("not_found", "Unknown release.")
        pos = (db.one("SELECT COALESCE(MAX(position),0) AS p FROM music_tracks WHERE release_id=?",
                      (rid,))["p"] or 0) + 1
        cur = db.x("INSERT INTO music_tracks(release_id,title,duration,notes,position,created) VALUES(?,?,?,?,?,?)",
                   (rid, title, clean(body.duration, 20), (body.notes or "").strip()[:1000], pos, time.time()))
        db.x("UPDATE music_releases SET updated=? WHERE id=?", (time.time(), rid))
        return db.one("SELECT id,title,duration,notes,position,created FROM music_tracks WHERE id=?", (cur.lastrowid,))

    @app.delete("/autopilot/api/music/tracks/{tid}", dependencies=[Depends(admin_write)])
    async def track_delete(tid: int):
        row = db.one("SELECT id,release_id FROM music_tracks WHERE id=?", (tid,))
        if not row:
            return err("not_found", "Unknown track.")
        db.x("DELETE FROM music_tracks WHERE id=?", (tid,))
        db.x("UPDATE music_releases SET updated=? WHERE id=?", (time.time(), row["release_id"]))
        return {"ok": True}
