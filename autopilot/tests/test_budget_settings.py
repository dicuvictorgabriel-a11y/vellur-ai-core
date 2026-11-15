import pytest

from app.budget import Budget, month_start
from app.db import DB
from app.settings import Settings, SettingsError
from conftest import make_env


@pytest.fixture
def parts(tmp_path):
    env = make_env(tmp_path)
    db = DB(str(tmp_path / "b.db"))
    return db, Settings(db, env), Budget(db)


def add_session(db, provider, start, end, dph=0.3, extra=0.0, reconciled=0, dry=0):
    db.x("INSERT INTO sessions(provider,profile,trigger,state,created_at,billing_started_at,ended_at,dph,extra_cost,"
         "reconciled,dry_run) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
         (provider, "coding", "manual", "stopped", start, start, end, dph, extra, reconciled, dry))


def test_estimates_and_confirmed_are_separate(parts):
    db, settings, b = parts
    t0 = month_start(1_800_000_000.0) + 3600
    add_session(db, "vastai", t0, t0 + 2 * 3600, dph=0.4, extra=0.1)          # 0.8 + 0.1
    add_session(db, "vastai", t0, t0 + 3600, dph=0.4, dry=1)                 # dry-run: never counted
    b.record_provider_charge("vastai", "inv-1", 1.25, t0 + 10)
    st = b.status(settings.get(), None, t0 + 3 * 3600)
    assert st["confirmed_usd"] == 1.25
    assert st["estimated_unconfirmed_usd"] == 0.9
    assert st["total_usd"] == 2.15
    assert st["remaining_usd"] == round(50 - 2.15, 2)


def test_provider_charge_is_idempotent(parts):
    _, _, b = parts
    assert b.record_provider_charge("vastai", "inv-9", 2.0, 1_800_000_000.0)
    assert not b.record_provider_charge("vastai", "inv-9", 2.0, 1_800_000_000.0)


def test_hyperai_included_hours_then_hourly(parts):
    db, settings, b = parts
    t0 = month_start(1_800_000_000.0) + 60
    add_session(db, "hyperai", t0, t0 + 28 * 3600)            # within the 30 included hours
    add_session(db, "hyperai", t0 + 100, t0 + 100 + 4 * 3600)  # 2 included + 2 billable
    st = b.status(settings.get(), None, t0 + 40 * 3600)
    assert st["estimated_unconfirmed_usd"] == round(2 * 0.35, 2)
    assert st["hyperai_hours_left_estimate"] == 0


def test_check_start_limits(parts):
    db, settings, b = parts
    s = settings.get()
    assert b.check_start(s, "vastai", 0.45, 0.2, 1_800_000_000.0) == (True, "ok")
    assert b.check_start(s, "vastai", 0.60, 0.0, 1_800_000_000.0) == (False, "price_above_limit")
    b.record_provider_charge("vastai", "big", 49.9, 1_800_000_000.0)
    assert b.check_start(s, "vastai", 0.40, 0.0, 1_800_000_000.0)[1] == "budget_insufficient"
    b.record_provider_charge("vastai", "big2", 1.0, 1_800_000_000.0)
    assert b.check_start(s, "vastai", 0.40, 0.0, 1_800_000_000.0)[1] == "budget_exhausted"


def test_thresholds_reported_once(parts):
    db, settings, b = parts
    b.record_provider_charge("vastai", "a", 38.0, 1_800_000_000.0)   # 76 %
    assert b.crossed_thresholds(settings.get(), 1_800_000_000.0) == [50, 75]
    assert b.crossed_thresholds(settings.get(), 1_800_000_000.0) == []
    b.record_provider_charge("vastai", "b", 12.0, 1_800_000_000.0)   # 100 %
    assert b.crossed_thresholds(settings.get(), 1_800_000_000.0) == [90, 100]


def test_settings_clamped_by_server_ceiling(parts):
    db, settings, _ = parts
    with pytest.raises(SettingsError):
        settings.update({"monthly_budget_usd": 500})
    with pytest.raises(SettingsError):
        settings.update({"max_hourly_usd": 2.0})
    db.settings_put({"monthly_budget_usd": 999})        # even a tampered DB value is clamped
    assert settings.get()["monthly_budget_usd"] == 50.0
    with pytest.raises(SettingsError):
        settings.update({"enabled": "yes"})
    with pytest.raises(SettingsError):
        settings.update({"idle_timeout_min": 1})
    with pytest.raises(SettingsError):
        settings.update({"preferred_provider": "aws"})
    with pytest.raises(SettingsError):
        settings.update({"nonexistent": 1})
    assert settings.update({"idle_timeout_min": 30})["idle_timeout_min"] == 30


def test_agent_preferences_persist_independently(parts):
    db, settings, _ = parts
    keys = ("agent_visual_inspection", "agent_auto_screenshots", "agent_auto_publish", "agent_auto_publish_main_repo")
    assert tuple(settings.get()[k] for k in keys) == (False, False, True, "")
    settings.update({"agent_visual_inspection": True})
    settings.update({"agent_auto_screenshots": True, "agent_auto_publish": False})
    settings.update({"agent_auto_publish_main_repo": "Owner/vellur-ai-core"})
    restored = Settings(db, settings.env).get()
    assert tuple(restored[k] for k in keys) == (True, True, False, "Owner/vellur-ai-core")
    settings.update({"agent_visual_inspection": False, "agent_auto_publish_main_repo": ""})
    restored = settings.get()
    assert tuple(restored[k] for k in keys) == (False, True, False, "")


@pytest.mark.parametrize("key", ["agent_visual_inspection", "agent_auto_screenshots", "agent_auto_publish"])
@pytest.mark.parametrize("value", [0, 1, "true", None, [], {}])
def test_agent_boolean_preferences_reject_non_booleans(parts, key, value):
    _, settings, _ = parts
    with pytest.raises(SettingsError):
        settings.update({key: value})


@pytest.mark.parametrize("value", [None, 1, True, [], " owner/repo", "owner/repo ", "owner/repo\n", "owner/..", "owner/repo/extra", "https://github.com/owner/repo", "owner/"])
def test_main_repo_preference_rejects_invalid_values(parts, value):
    _, settings, _ = parts
    with pytest.raises(SettingsError):
        settings.update({"agent_auto_publish_main_repo": value})
    assert settings.get()["agent_auto_publish_main_repo"] == ""
