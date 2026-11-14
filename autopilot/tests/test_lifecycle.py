import asyncio

import pytest

from app import lifecycle as lc
from app.lifecycle import ControlError
from app.providers.mock import MockProvider


async def until(pred, timeout=3.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def state(ctrl):
    s = ctrl.current()
    return s and s["state"]


async def ready(ctrl, trigger="manual"):
    await ctrl.start(trigger)
    await until(lambda: state(ctrl) == "ready")


async def test_start_reaches_ready_through_all_phases(rig):
    ctrl = rig()
    await ready(ctrl)
    kinds = [e["message"].split(":")[0] for e in reversed(ctrl.db.events(50)) if e["kind"] == "state"]
    assert kinds == ["provisioning", "starting", "loading_model", "ready"]
    s = ctrl.current()
    assert s["instance_id"] and s["billing_started_at"] and s["dph"] == 0.30 and ctrl.phase() == "ready"


async def test_duplicate_start_creates_one_instance(rig):
    ctrl = rig()
    await asyncio.gather(*(ctrl.start("manual") for _ in range(10)))
    await until(lambda: state(ctrl) == "ready")
    assert [c for c in ctrl.mock.calls if c[0] == "create"] == [("create",)]
    assert len(ctrl.db.all("SELECT id FROM sessions")) == 1


async def test_concurrent_requests_share_one_start(rig):
    ctrl = rig()
    ctrl.settings.update({"auto_start_paid": True})
    urls = await asyncio.gather(*(ctrl.ensure_ready() for _ in range(8)))
    assert set(urls) == {"http://mock-backend"}
    assert sum(1 for c in ctrl.mock.calls if c[0] == "create") == 1
    assert ctrl.queued == 0


async def test_dry_run_never_creates(rig):
    ctrl = rig(live=False)
    await ctrl.start("manual")
    await until(lambda: state(ctrl) == "stopped")
    s = ctrl.current()
    assert s["reason"].startswith("dry_run") and s["dry_run"] == 1
    assert not any(c[0] == "create" for c in ctrl.mock.calls)


async def test_request_cannot_start_paid_gpu_when_auto_start_off(rig):
    ctrl = rig()
    with pytest.raises(ControlError) as e:
        await ctrl.ensure_ready()
    assert e.value.code == "auto_start_disabled"
    assert not ctrl.mock.calls


async def test_disabled_autopilot_rejects_requests(rig):
    ctrl = rig()
    ctrl.settings.update({"enabled": False, "auto_start_paid": True})
    with pytest.raises(ControlError) as e:
        await ctrl.ensure_ready()
    assert e.value.code == "disabled"


async def test_missing_credentials(rig):
    ctrl = rig(provider=MockProvider(fail={"config": True}))
    with pytest.raises(ControlError) as e:
        await ctrl.start("manual")
    assert e.value.code == "config" and "missing credentials" in e.value.message


@pytest.mark.parametrize("fail,code", [({"find_offer": "capacity"}, "capacity"), ({"find_offer": "timeout"}, "timeout"),
                                       ({"create": "auth"}, "auth"), ({"find_offer": "provider"}, "provider")])
async def test_provider_failures_are_reported(rig, fail, code):
    ctrl = rig(provider=MockProvider(fail=fail))
    ctrl.settings.update({"auto_start_paid": True})
    with pytest.raises(ControlError) as e:
        await ctrl.ensure_ready()
    assert e.value.code == code
    assert state(ctrl) == "failed" and ctrl.current()["reason"].startswith(code)
    assert any(ev["kind"] == "gpu_failed" for ev in ctrl.db.events(20))


async def test_price_above_limit_is_rejected_before_renting(rig):
    ctrl = rig(provider=MockProvider(dph=0.9))
    await ctrl.start("manual")
    await until(lambda: state(ctrl) == "failed")
    assert ctrl.current()["reason"].startswith("price")
    assert not any(c[0] == "create" for c in ctrl.mock.calls)


async def test_stop_is_confirmed_by_provider_and_idempotent(rig):
    ctrl = rig()
    await ready(ctrl)
    await ctrl.stop("manual")
    await ctrl.stop("manual")
    await until(lambda: state(ctrl) == "stopped")
    s = ctrl.current()
    assert s["termination_verified"] == 1 and s["ended_at"]
    assert ctrl.mock.instances[s["instance_id"]]["state"] == "terminated"
    await ctrl.stop("manual")
    assert sum(1 for c in ctrl.mock.calls if c[0] == "stop") == 1


async def test_unconfirmed_termination_escalates(rig, monkeypatch):
    monkeypatch.setattr(lc, "STOP_VERIFY_ATTEMPTS", 3)
    ctrl = rig(provider=MockProvider(fail={"stop_ignored": True}))
    await ready(ctrl)
    await ctrl.stop("manual")
    await until(lambda: state(ctrl) == "failed")
    assert ctrl.current()["reason"].startswith("termination_unverified")
    assert any(e["kind"] == "termination_unverified" for e in ctrl.db.events(20))
    with pytest.raises(ControlError) as e:      # no new GPU while one may still be billing
        await ctrl.start("manual")
    assert e.value.code == "termination_unverified"


async def test_failed_start_after_creation_cleans_up(rig):
    ctrl = rig(model_load_timeout_seconds=0.3)
    ctrl.backend.up = False                       # model never answers
    await ctrl.start("manual")
    await until(lambda: state(ctrl) == "failed")
    assert ctrl.current()["reason"].startswith("timeout")
    iid = ctrl.current()["instance_id"]
    await until(lambda: ctrl.mock.instances[iid]["state"] == "terminated")
    await until(lambda: ctrl.current()["termination_verified"] == 1)


async def test_idle_timeout_stops_only_after_real_inactivity(rig):
    ctrl = rig()
    await ready(ctrl)
    ctrl.fake_clock.t += 19 * 60
    await ctrl.tick()
    assert state(ctrl) == "ready"
    ctrl.fake_clock.t += 2 * 60
    await ctrl.tick()
    await until(lambda: state(ctrl) == "stopped")


async def test_long_running_request_keeps_gpu(rig):
    ctrl = rig()
    await ready(ctrl)
    async with ctrl.activity():
        ctrl.fake_clock.t += 60 * 60          # a one-hour generation
        await ctrl.tick()
        assert state(ctrl) == "ready" and ctrl.phase() == "processing"
    ctrl.fake_clock.t += 5 * 60
    await ctrl.tick()
    assert state(ctrl) == "ready"             # idle timer restarted when the request ended


async def test_protected_task_keeps_gpu(rig):
    ctrl = rig()
    await ready(ctrl)
    ctrl.protect(60)
    ctrl.fake_clock.t += 30 * 60
    await ctrl.tick()
    assert state(ctrl) == "ready"
    ctrl.fake_clock.t += 31 * 60
    await ctrl.tick()
    await until(lambda: state(ctrl) == "stopped")


async def test_monitoring_failure_never_stops_automated_gpu(rig):
    ctrl = rig()
    await ready(ctrl)
    ctrl.backend.up = False
    for _ in range(20):
        ctrl.fake_clock.t += 30
        ctrl.last_activity = ctrl.fake_clock.t     # user still active
        await ctrl.tick()
    assert state(ctrl) == "ready"
    assert any(e["kind"] == "health" for e in ctrl.db.events(50))


async def test_session_limit_waits_for_active_work(rig):
    ctrl = rig()
    await ready(ctrl)
    ctrl.settings.update({"max_session_hours": 1.0})
    async with ctrl.activity():
        ctrl.fake_clock.t += 3601
        await ctrl.tick()
        assert state(ctrl) == "ready"
        with pytest.raises(ControlError) as e:
            await ctrl.ensure_ready()
        assert e.value.code == "session_limit"
    await ctrl.tick()
    await until(lambda: state(ctrl) == "stopped")


async def test_budget_exhaustion_blocks_and_stops(rig):
    ctrl = rig()
    await ready(ctrl)
    ctrl.budget.record_provider_charge("mock", "x", 50.0, ctrl.fake_clock.t)
    with pytest.raises(ControlError) as e:
        await ctrl.ensure_ready()
    assert e.value.code == "budget"
    await ctrl.tick()
    await until(lambda: state(ctrl) == "stopped")
    assert any(e["kind"] == "budget_threshold" for e in ctrl.db.events(50))
    with pytest.raises(ControlError) as e:
        await ctrl.start("manual")
    assert e.value.code == "budget"


async def test_billing_reconciliation(rig):
    ctrl = rig()
    await ready(ctrl)
    await ctrl.stop("manual")
    await until(lambda: state(ctrl) == "stopped")
    ctrl.mock.charges.append({"ref": "inv-1", "amount": 0.42, "ts": ctrl.fake_clock.t})
    from app.providers.base import BillingReport
    orig = ctrl.mock.billing

    async def covered(month, ms):
        r = await orig(month, ms)
        return BillingReport(r.month, r.charges, balance_usd=7.5, covered_until=ctrl.fake_clock.t + 1)
    ctrl.mock.billing = covered
    out = await ctrl.sync_billing()
    assert out["mock"] == {"new_charges": 1, "balance_usd": 7.5}
    assert (await ctrl.sync_billing())["mock"]["new_charges"] == 0
    b = ctrl.budget.status(ctrl.settings.get(), None, ctrl.fake_clock.t)
    assert b["confirmed_usd"] == 0.42 and b["estimated_unconfirmed_usd"] == 0
    assert ctrl.current()["reconciled"] == 1


async def test_recovery_after_restart_reattaches(rig, tmp_path):
    ctrl = rig()
    await ready(ctrl)
    iid = ctrl.current()["instance_id"]
    await ctrl.close()
    # new process: same DB, same provider (the GPU kept running meanwhile)
    ctrl2 = rig(provider=ctrl.mock)
    assert ctrl2.current()["instance_id"] == iid
    await ctrl2.recover()
    await until(lambda: state(ctrl2) == "ready" and ctrl2.backend_url)
    assert sum(1 for c in ctrl.mock.calls if c[0] == "create") == 1


async def test_recovery_finishes_interrupted_stop(rig):
    ctrl = rig()
    await ready(ctrl)
    sid = ctrl.current()["id"]
    ctrl.db.x("UPDATE sessions SET state='stopping' WHERE id=?", (sid,))
    await ctrl.close()
    ctrl2 = rig(provider=ctrl.mock)
    await ctrl2.recover()
    await until(lambda: state(ctrl2) == "stopped")
    assert ctrl2.current()["termination_verified"] == 1


async def test_orphan_instance_is_stopped_in_live_mode(rig):
    ctrl = rig()
    ctrl.mock.instances["999"] = {"state": "running", "polls": 9, "label": "vellur-autopilot-old"}
    await ctrl.tick()
    assert ctrl.mock.instances["999"]["state"] == "terminated"


async def test_orphan_only_reported_in_dry_run(rig):
    ctrl = rig(live=False)
    ctrl.mock.instances["999"] = {"state": "running", "polls": 9, "label": "vellur-autopilot-old"}
    await ctrl.tick()
    await ctrl.tick()
    assert ctrl.mock.instances["999"]["state"] == "running"
    assert sum(1 for e in ctrl.db.events(20) if e["kind"] == "orphan") == 1


async def test_assisted_provider_attach_and_workspace_gone(rig):
    ctrl = rig(manual=True)
    await ctrl.start("request")                    # assisted mode needs no paid auto-start permission
    await until(lambda: state(ctrl) == "ready")
    assert ctrl.fake_tunnel.opened == [("ssh.example", 2222)]
    ctrl.backend.up = False
    for _ in range(lc.HEALTH_FAILS_MANUAL_END):
        await ctrl.tick()
    assert state(ctrl) == "stopped" and ctrl.current()["reason"].startswith("backend_gone")
    assert ctrl.manual.stop_calls == 0


async def test_assisted_idle_alerts_instead_of_stopping(rig):
    ctrl = rig(manual=True)
    await ready(ctrl)
    ctrl.fake_clock.t += 25 * 60
    await ctrl.tick()
    await ctrl.tick()
    assert state(ctrl) == "ready"
    assert sum(1 for e in ctrl.db.events(30) if e["kind"] == "manual_idle") == 1
    await ctrl.stop("manual")
    await until(lambda: state(ctrl) == "stopped")
    assert "unverified" in ctrl.current()["reason"]
    assert ctrl.status()["termination"].startswith("unverified")


async def test_request_waits_while_starting_then_times_out_cleanly(rig):
    ctrl = rig(provider=MockProvider(boot_polls=10**9), request_hold_seconds=0.3)
    ctrl.settings.update({"auto_start_paid": True})
    with pytest.raises(ControlError) as e:
        await ctrl.ensure_ready()
    assert e.value.code == "starting" and "Starting your AI environment" in e.value.message
    assert ctrl.queued == 0
