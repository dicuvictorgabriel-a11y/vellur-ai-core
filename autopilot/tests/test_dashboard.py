import os
import shutil
import subprocess

import pytest

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "dashboard.html")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_dashboard_script_parses(tmp_path):
    html = open(PAGE).read()
    js = html[html.index("<script>") + 8: html.index("</script>")]
    f = tmp_path / "dash.js"
    f.write_text(js)
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_dashboard_has_no_basic_auth_or_secrets():
    html = open(PAGE).read()
    assert "/autopilot/login" not in html and "X-Vellur-Admin" not in html and "localStorage" not in html


def test_agent_tools_markup_and_payloads():
    html = open(PAGE).read()
    assert '<details id="agTools"><summary title="Agent tools" aria-label="Agent tools">' in html
    assert '<summary>Tools</summary>' not in html
    assert 'id="agToolsNote" role="status" aria-live="polite"' in html
    assert 'id="agInspect" disabled' in html and 'id="agAutoShots" disabled' in html
    assert html.count('visual_inspection: $("agInspect").checked, auto_screenshots: $("agAutoShots").checked') == 2
    assert '$("agInspect").checked = false' not in html
    assert '$("agAutoShots").checked = false' not in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_agent_preferences_ui_behavior():
    html = open(PAGE).read()
    js = html[html.index('  const PREF_CONTROLS'):html.index('  const FINAL_STATES')]
    setup = '''
const assert = require("assert");
const els = {};
const $ = id => els[id] ||= { checked: false, disabled: true, textContent: "", addEventListener() {}, contains() { return false; }, querySelector() { return { focus() {} }; } };
const document = { addEventListener() {} };
let AGENT = { self_repo: "owner/vellur" }, fail = false, calls = [];
let server = { agent_visual_inspection: false, agent_auto_screenshots: false, agent_auto_publish: true, agent_auto_publish_main_repo: "" };
const confirm = () => true;
const send = async (path, method, body) => { calls.push([path, method, body]); if (fail) throw Error("offline"); Object.assign(server, body); return { ...server }; };
'''
    checks = '''
(async () => {
  syncAgentPrefs(server);
  assert.equal($("agInspect").checked, false);
  assert.equal($("agAutoShots").checked, false);
  assert.equal($("agAutoPublish").checked, true);
  $("agInspect").checked = true; await $("agInspect").onchange();
  assert.deepEqual(calls[0], ["settings", "PUT", { agent_visual_inspection: true }]);
  $("agAutoShots").checked = true; await $("agAutoShots").onchange();
  assert.equal($("agInspect").checked, true);
  $("agMainPublish").checked = true; await $("agMainPublish").onchange();
  assert.equal(server.agent_auto_publish_main_repo, "owner/vellur");
  fail = true; $("agAutoPublish").checked = false; await $("agAutoPublish").onchange();
  assert.equal($("agAutoPublish").checked, true);
  assert.match($("agToolsNote").textContent, /Not saved/);
  syncAgentPrefs({ ...server });
  assert.equal($("agInspect").checked, true);
  assert.equal($("agAutoShots").checked, true);
  assert.equal($("agMainPublish").checked, true);
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
    result = subprocess.run(["node", "-e", setup + js + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_publication_events_and_persisted_report_render():
    html = open(PAGE).read()
    esc = html[html.index('  const esc ='):html.index('  const md =')]
    js = html[html.index('  function publicationText'):html.index('  const money4 =')]
    setup = '''
const assert = require("assert");
const OUTCOME = { completed: ["Completed", "ok"] };
const money4 = v => "$" + Number(v || 0).toFixed(4);
const shotUrl = n => n;
let reports = [], inserted = [], EXPLORE = null;
const area = () => ({ hidden: true, innerHTML: "" });
const document = { createElement: () => ({ dataset: {}, innerHTML: "", className: "" }) };
const box = () => ({ querySelectorAll: () => reports });
const stick = () => true;
const div = (cls, html) => ({ className: cls, innerHTML: html });
const put = el => { inserted.push(el); return el; };
const fillActs = () => {};
const toolView = () => ({});
'''
    checks = '''
const p = { status: "published", commit: "abc123", branch: "main", command: "bash dev/test.sh", rebaseline: "ok" };
const a = area(), b = area();
reports = [{ dataset: { run: "1" }, querySelector: () => a }, { dataset: { run: "2" }, querySelector: () => b }];
renderEvent({ kind: "publication", run: 2, data: p });
assert.equal(a.hidden, true);
assert.equal(b.hidden, false);
assert.match(b.innerHTML, /abc123/);
assert.match(b.innerHTML, /main/);
assert.match(inserted[0].innerHTML, /Automatically published/);
assert.match(reportCard({ run: 2, outcome: "completed", publication: p }).innerHTML, /abc123/);
for (const status of ["failed", "skipped"]) {
  const el = renderEvent({ kind: "publication", run: 2, data: { status, reason: "<unsafe>", rebaseline: "failed: <bad>" } });
  assert.match(el.innerHTML, new RegExp(status));
  assert.match(el.innerHTML, /&lt;unsafe&gt;/);
  assert.match(el.innerHTML, /&lt;bad&gt;/);
}
assert.match(publicationText({ status: "published", commit: "<unsafe>" }), /&lt;unsafe&gt;/);
assert.match(reportCard({ run: 3 }).innerHTML, /class="publication" hidden/);
'''
    result = subprocess.run(["node", "-e", setup + esc + js + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'updateReportPublication(t.report.run, t.report.publication)' in html
    assert 'updateReportPublication(CUR.report.run, CUR.report.publication)' in html
