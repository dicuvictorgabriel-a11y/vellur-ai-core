import json

import httpx
import pytest

from app.config import PROFILES
from app.config import DEFAULT_SETTINGS
from app.providers.base import ProviderError
from app.providers.vastai import VastProvider

S = dict(DEFAULT_SETTINGS)


def vast(handler, live=True, key="k"):
    return VastProvider(key, live=live, transport=httpx.MockTransport(handler))


async def test_offer_search_payload_and_choice():
    seen = {}

    def h(req):
        seen["method"], seen["url"], seen["body"] = req.method, str(req.url), json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"offers": [
            {"id": 1, "gpu_name": "RTX 5090", "gpu_ram": 32607, "dph_total": 0.31, "geolocation": "Texas, US",
             "inet_down_cost": 0.002, "reliability": 0.99},
            {"id": 2, "gpu_name": "RTX 5090", "gpu_ram": 32607, "dph_total": 0.38, "geolocation": "Netherlands, NL",
             "inet_down_cost": 0.001, "reliability": 0.995}]})
    o = await vast(h).find_offer(PROFILES["coding"], S)
    assert seen["method"] == "POST" and seen["url"] == "https://console.vast.ai/api/v0/bundles/"
    assert seen["auth"] == "Bearer k"
    b = seen["body"]
    assert b["gpu_ram"] == {"gte": 32000} and b["dph_total"] == {"lte": 0.5} and b["num_gpus"] == {"eq": 1}
    assert b["verified"] == {"eq": True} and b["rentable"] == {"eq": True} and b["type"] == "on-demand"
    assert o.offer_id == "2" and o.location.endswith("NL")      # geography preferred over a cheaper US offer
    assert o.extra_cost == pytest.approx(19 * 0.001)


async def test_no_offer_is_capacity_error():
    with pytest.raises(ProviderError) as e:
        await vast(lambda r: httpx.Response(200, json={"offers": []})).find_offer(PROFILES["coding"], S)
    assert e.value.code == "capacity"


async def test_create_attaches_ssh_key_and_returns_contract():
    calls = []

    def h(req):
        calls.append((req.method, req.url.path, json.loads(req.content or b"{}")))
        if req.url.path == "/api/v0/asks/2/":
            return httpx.Response(200, json={"success": True, "new_contract": 7835610})
        return httpx.Response(200, json={"success": True})
    from app.providers.base import Offer
    iid = await vast(h).create(Offer("2", "RTX 5090", 32, 0.38), PROFILES["coding"], S, "vellur-autopilot-5",
                               "ssh-ed25519 AAAA test", "vllm-key")
    assert iid == "7835610"
    m, path, body = calls[0]
    assert (m, path) == ("PUT", "/api/v0/asks/2/")
    assert body["image"] == "vllm/vllm-openai:v0.27.1" and body["runtype"] == "ssh_proxy" and body["client_id"] == "me"
    assert body["env"]["VLLM_API_KEY"] == "vllm-key" and "--host 127.0.0.1" in body["onstart"]
    assert calls[1][:2] == ("POST", "/api/v0/instances/7835610/ssh/") and calls[1][2] == {"ssh_key": "ssh-ed25519 AAAA test"}


async def test_status_mapping_and_terminated():
    def h(req):
        if req.url.path.endswith("/404/"):
            return httpx.Response(200, json={"instances": None})
        return httpx.Response(200, json={"instances": {"id": 5, "actual_status": "running", "intended_status": "running",
                                                       "ssh_host": "ssh5.vast.ai", "ssh_port": 12345, "dph_total": 0.38,
                                                       "label": "vellur-autopilot-5"}})
    p = vast(h)
    st = await p.status("5")
    assert (st.state, st.ssh_host, st.ssh_port) == ("running", "ssh5.vast.ai", 12345)
    assert (await p.status("404")).state == "terminated"


async def test_stop_destroys_by_default_and_can_stop():
    calls = []

    def h(req):
        calls.append((req.method, req.url.path, json.loads(req.content or b"{}")))
        return httpx.Response(200, json={"success": True})
    p = vast(h)
    await p.stop("5", S)
    await p.stop("5", {**S, "vast_idle_action": "stop"})
    assert calls == [("DELETE", "/api/v0/instances/5/", {}), ("PUT", "/api/v0/instances/5/", {"state": "stopped"})]


async def test_dry_run_refuses_mutations_but_allows_reads():
    p = vast(lambda r: httpx.Response(200, json={"offers": [{"id": 1, "dph_total": 0.2, "gpu_ram": 32000}]}), live=False)
    assert (await p.find_offer(PROFILES["coding"], S)).offer_id == "1"
    with pytest.raises(ProviderError) as e:
        await p.stop("5", S)
    assert e.value.code == "dry_run"


async def test_tests_can_never_call_real_vast():
    p = VastProvider("real-key", live=True)            # no mock transport
    with pytest.raises(ProviderError) as e:
        await p.stop("5", S)
    assert e.value.code == "dry_run"


@pytest.mark.parametrize("resp,code", [(httpx.Response(401), "auth"), (httpx.Response(429), "provider"),
                                       (httpx.Response(500, text="boom"), "provider")])
async def test_http_errors(resp, code):
    with pytest.raises(ProviderError) as e:
        await vast(lambda r: resp).find_offer(PROFILES["coding"], S)
    assert e.value.code == code


async def test_timeout_and_missing_key():
    def slow(req):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(ProviderError) as e:
        await vast(slow).find_offer(PROFILES["coding"], S)
    assert e.value.code == "timeout"
    p = vast(lambda r: httpx.Response(200), key="")
    assert p.configured()[0] is False
    with pytest.raises(ProviderError) as e:
        await p.find_offer(PROFILES["coding"], S)
    assert e.value.code == "config"


async def test_billing_parses_charges_and_balance():
    def h(req):
        if req.url.path == "/api/v0/users/current":
            return httpx.Response(200, json={"credit": 12.5, "id": 1})
        q = dict(httpx.QueryParams(req.url.query))
        assert q["owner"] == "me" and q["inc_charges"] == "true"
        return httpx.Response(200, json={"invoices": [
            {"id": "c1", "type": "charge", "amount": -1.2, "timestamp": 1_800_000_100, "description": "instance 5"},
            {"id": "p1", "type": "payment", "amount": 20, "timestamp": 1_800_000_000}], "current": {"charges": 0.4}})
    rep = await vast(h).billing("2027-01", 1_799_999_000.0)
    assert rep.balance_usd == 12.5 and rep.charges == [{"ref": "c1", "amount": 1.2, "ts": 1_800_000_100.0, "note": "instance 5"}]


async def test_list_managed_paginates_and_filters_label():
    pages = [{"instances": [{"id": 1, "label": "vellur-autopilot-1", "actual_status": "running"},
                            {"id": 2, "label": "someone-else", "actual_status": "running"}], "next_token": "t2"},
             {"instances": [{"id": 3, "label": "vellur-autopilot-3", "actual_status": "exited"}]}]

    def h(req):
        assert req.url.path == "/api/v1/instances/"
        return httpx.Response(200, json=pages.pop(0))
    out = await vast(h).list_managed("vellur-autopilot")
    assert [(i.instance_id, i.state) for i in out] == [("1", "running"), ("3", "stopped")]
