"""A stream that ends before its usage chunk (Stop button, cancelled agent run, dropped connection) is still billed by
OpenRouter. VELLUR recorded it at $0; it now asks OpenRouter for the real cost of that generation id."""
import asyncio
import json

import httpx
import pytest

from app.router import Router


def sse(chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode()


class Upstream:
    def __init__(self, with_usage=False, gen_cost=0.0137, gen_status=200):
        self.with_usage, self.gen_cost, self.gen_status, self.gen_calls = with_usage, gen_cost, gen_status, []

    def handler(self, req: httpx.Request):
        if req.url.path.endswith("/generation"):
            self.gen_calls.append(req.url.params.get("id"))
            if self.gen_status != 200:
                return httpx.Response(self.gen_status, json={"error": {"message": "not ready"}})
            return httpx.Response(200, json={"data": {"id": req.url.params.get("id"), "total_cost": self.gen_cost,
                                                      "tokens_prompt": 900, "tokens_completion": 120}})
        chunks = [{"id": "gen-abc123XYZ", "choices": [{"delta": {"content": "Hel"}}]}]
        if self.with_usage:
            chunks.append({"id": "gen-abc123XYZ", "choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 3, "cost": 0.002}})
        return httpx.Response(200, content=sse(chunks), headers={"content-type": "text/event-stream"})


async def run(rig, up):
    ctrl = rig()
    router = Router(ctrl, transport=httpx.MockTransport(up.handler))
    router.RECONCILE_DELAYS = (0, 0)
    router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
    ctrl.settings.update({"routes": {"vellur-chat": ["openrouter:some/model"]}, "prices": {"openrouter:some/model": [1, 2]}})
    body = json.dumps({"model": "vellur-chat", "stream": True, "messages": [{"role": "user", "content": "hi"}]}).encode()
    resp = await router.handle("POST", "chat/completions", "", {}, body, "chat:1")
    async for _ in resp.stream:
        pass
    for _ in range(20):
        await asyncio.sleep(0)
    if router._reconciling:
        await asyncio.gather(*list(router._reconciling))
    return ctrl, ctrl.db.one("SELECT * FROM usage ORDER BY id DESC LIMIT 1")


async def test_stream_without_usage_gets_the_real_provider_cost(rig):
    up = Upstream(with_usage=False)
    ctrl, row = await run(rig, up)
    assert up.gen_calls == ["gen-abc123XYZ"]
    assert row["cost_usd"] == pytest.approx(0.0137) and row["cost_source"] == "provider"
    assert (row["in_tokens"], row["out_tokens"]) == (900, 120)


async def test_stream_with_reported_cost_is_not_queried_again(rig):
    up = Upstream(with_usage=True)
    ctrl, row = await run(rig, up)
    assert up.gen_calls == [] and row["cost_usd"] == pytest.approx(0.002) and row["cost_source"] == "provider"


async def test_unavailable_generation_stats_leave_the_row_unknown_not_invented(rig):
    up = Upstream(with_usage=False, gen_status=404)
    ctrl, row = await run(rig, up)
    assert len(up.gen_calls) == 2
    assert row["cost_usd"] == 0 and row["cost_source"] == "unknown"
