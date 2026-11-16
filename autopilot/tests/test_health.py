"""Restart diagnostics: a clean stop leaves a record, a kill does not; the boot record says which, with memory."""
import httpx

from app.agent import AgentManager
from app.api import build_admin
from app.health import Health, memory
from app.router import Router


def test_boot_records_clean_and_unclean_stops(rig):
    ctrl = rig()
    t = [1000.0]
    h = Health(ctrl.db, lambda: {"installed": "a" * 40}, clock=lambda: t[0])
    assert h.boot()["kind"] == "first"
    t[0] += 30
    h.beat()
    h.shutdown("signal")                       # docker stop / release install
    t[0] += 5
    rec = Health(ctrl.db, lambda: {"installed": "b" * 40}, clock=lambda: t[0]).boot()
    assert rec["kind"] == "clean" and rec.get("release_changed") and "aaaaaaa → bbbbbbb" in rec["summary"]
    t[0] += 60
    h.beat()                                   # ... then killed: no shutdown record
    t[0] += 20
    rec = Health(ctrl.db, lambda: {"installed": "b" * 40}, clock=lambda: t[0]).boot()
    assert rec["kind"] == "unclean" and "UNCLEAN" in rec["summary"] and "20 s earlier" in rec["summary"]
    assert [b["kind"] for b in h.report()["boots"]] == ["unclean", "clean", "first"]
    assert ctrl.db.one("SELECT level FROM events WHERE kind='boot' ORDER BY id DESC LIMIT 1")["level"] == "warn"
    assert isinstance(memory(), dict)


async def test_restart_diagnostics_endpoint(rig):
    ctrl = rig()
    router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router, agent=AgentManager(ctrl, router))),
                          base_url="https://ai.example")
    assert (await c.get("/autopilot/api/diagnostics/restarts")).status_code == 401
    r = await c.get("/autopilot/api/diagnostics/restarts", headers={"X-Vellur-Admin": "s" * 32})
    assert r.status_code == 200 and "boots" in r.json() and "interrupted_tasks" in r.json()


def test_recovery_knowledge_ships_once_and_keeps_the_cause_unconfirmed(rig):
    ctrl = rig()
    router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    AgentManager(ctrl, router)
    m = AgentManager(ctrl, router)            # a second start does not duplicate them
    rows = m.knowledge.search("restart recovery", any_term=True)
    sources = sorted(r["source"] for r in rows if r["source"].startswith("release:recovery"))
    assert len(sources) == 3
    incident = ctrl.db.one("SELECT status FROM knowledge WHERE source='release:recovery-2026-11-16:incident'")
    assert incident["status"] == "proposed"
