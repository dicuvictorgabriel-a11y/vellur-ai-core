import json
import sys
import httpx

from app.api import build_admin
from app.efficiency import detect_rtk_binary, ponytail_compact_messages, rtk_compact_text

ADMIN = {"X-Vellur-Admin": "s" * 32}
MUTATE = {**ADMIN, "X-Vellur-Request": "1"}


def test_rtk_binary_detector_rejects_reachingforthejack_rtk(tmp_path, monkeypatch):
    fake_wrong = tmp_path / "rtk"
    fake_wrong.write_text(f"#!{sys.executable}\nprint('reachingforthejack/rtk v0.2 type kit')\n")
    fake_wrong.chmod(0o755)
    monkeypatch.setenv("VELLUR_RTK_BIN", str(fake_wrong))
    info = detect_rtk_binary()
    assert info["repo"] == "TokenFleet-AI/rtk"
    assert info["excluded_repo"] == "reachingforthejack/rtk"
    assert info["binary_installed"] is False
    assert info["mode"] == "embedded_filter"

    fake_good = tmp_path / "rtk-good"
    fake_good.write_text(f"#!{sys.executable}\nprint('rtk 0.9.1 (TokenFleet-AI/rtk)')\n")
    fake_good.chmod(0o755)
    monkeypatch.setenv("VELLUR_RTK_BIN", str(fake_good))
    info_ok = detect_rtk_binary()
    assert info_ok["binary_installed"] is True
    assert info_ok["repo"] == "TokenFleet-AI/rtk"


def test_rtk_and_ponytail_compaction_preserve_prefix_and_recent_context():
    noisy = (
        "\x1b[32mCollecting packages...\x1b[0m\n"
        "  45% |██████████          | 12.4 MB\n"
        ".................................... [ 80%]\n"
        + ("identical noisy warning line\n" * 12)
        + ("diff --git a/x.py b/x.py\n+line\n" * 400)
        + "\nFAILED tests/test_demo.py::test_x - AssertionError\n"
    )
    compacted, saved = rtk_compact_text(noisy, max_chars=1200)
    assert saved > 2000
    assert "\x1b[" not in compacted
    assert "FAILED tests/test_demo.py::test_x" in compacted

    big_file = "1: def alpha():\n" + ("    pass\n" * 300)
    msgs = [
        {"role": "system", "content": "SYSTEM PREFIX MUST NOT CHANGE"},
        {"role": "user", "content": "Initial task instructions"},
        {"role": "assistant", "content": "Reading file first time"},
        {"role": "tool", "tool_call_id": "c1", "content": big_file},
        {"role": "assistant", "content": "Step 2"},
        {"role": "tool", "tool_call_id": "c2", "content": "old stale tool output\n" + ("x" * 2200)},
        {"role": "assistant", "content": "Re-reading file in recent tail"},
        {"role": "tool", "tool_call_id": "c3", "content": big_file},
        {"role": "user", "content": "Finish up"},
    ]
    trimmed, p_saved = ponytail_compact_messages(msgs, keep_recent=3, max_stale_chars=500)
    assert p_saved > 3000
    # System prefix and recent tail are preserved byte-for-byte
    assert trimmed[0]["content"] == "SYSTEM PREFIX MUST NOT CHANGE"
    assert trimmed[1]["content"] == "Initial task instructions"
    assert trimmed[-2]["content"] == big_file
    assert "[ponytail: duplicate tool output" in trimmed[3]["content"]


async def test_efficiency_api_reversible_toggles_and_router_telemetry(rig):
    seen_upstream = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_upstream.append(json.loads(req.content.decode()))
        return httpx.Response(200, json={
            "id": "chatcmpl-eff",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 180, "completion_tokens": 12, "prompt_tokens_details": {"cached_tokens": 90}},
        })

    from app.router import Router
    ctrl = rig()
    router = Router(ctrl, transport=httpx.MockTransport(handler))
    router.update_provider("openrouter", {"api_key": "sk-or-test-key-1234567890", "enabled": True})
    ctrl.settings.update({"routes": {"vellur-coder": ["openrouter:qwen/qwen3-coder:free"]}})

    app = build_admin(ctrl, router=router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ap") as client:
        st0 = (await client.get("/autopilot/api/efficiency", headers=ADMIN)).json()
        assert st0["ponytail"]["repo"] == "DietrichGebert/ponytail"
        assert st0["rtk"]["repo"] == "TokenFleet-AI/rtk"
        assert st0["rtk"]["excluded_repo"] == "reachingforthejack/rtk"
        assert st0["ponytail"]["enabled"] is True and st0["rtk"]["enabled"] is True

        # Reversible off -> verify messages pass through untouched
        off = (await client.patch("/autopilot/api/efficiency", headers=MUTATE, json={"ponytail_enabled": False, "rtk_enabled": False})).json()
        assert off["active"] is False

        noisy_tool = "\x1b[31m" + ("verbose log line\n" * 350)
        payload = {
            "model": "vellur-coder",
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "go"},
                {"role": "tool", "tool_call_id": "t1", "content": noisy_tool},
                {"role": "user", "content": "next"},
            ],
        }
        r0 = await router.handle("POST", "chat/completions", "", {}, json.dumps(payload).encode(), tag="test")
        if r0.stream:
            async for _ in r0.stream:
                pass
        assert len(seen_upstream[-1]["messages"][2]["content"]) == len(noisy_tool)

        # Turn back ON -> verify router compacts before sending upstream and records measured savings
        on = (await client.patch("/autopilot/api/efficiency", headers=MUTATE, json={"ponytail_enabled": True, "rtk_enabled": True})).json()
        assert on["active"] is True

        resp = await router.handle("POST", "chat/completions", "", {}, json.dumps(payload).encode(), tag="test")
        if resp.stream:
            async for _ in resp.stream:
                pass
        assert resp.status == 200
        assert int(resp.headers.get("X-Vellur-Tokens-Saved", "0")) > 100
        assert len(seen_upstream[-1]["messages"][2]["content"]) < len(noisy_tool)

        st1 = (await client.get("/autopilot/api/efficiency", headers=ADMIN)).json()
        assert st1["metrics"]["compaction_tokens_saved"] > 100
        assert st1["metrics"]["cached_tokens_saved"] == 180
        assert st1["metrics"]["measured_savings_pct"] > 0

        costs = (await client.get("/autopilot/api/costs", headers=ADMIN)).json()
        assert "token_efficiency" in costs
        assert costs["token_efficiency"]["metrics"]["total_tokens_saved"] >= 280
