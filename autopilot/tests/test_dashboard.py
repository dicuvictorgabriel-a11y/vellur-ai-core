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


def test_3d_studio_hooks_are_present_and_keep_the_web_preview_sandboxed():
    html = open(PAGE).read()
    assert 'id="pv3D"' in html and 'id="nav3D"' in html
    assert "vellur-3d-selection" in html and "SANDBOX_3D" in html and "SANDBOX_WEB" in html
    assert 'sandbox="allow-scripts allow-forms allow-popups allow-modals allow-pointer-lock"' in html
    assert "allow-same-origin" not in html.split('id="pvFrame"', 1)[1].split("</iframe>", 1)[0]


def test_live_preview_shows_a_loading_state_instead_of_a_blank_pane():
    html = open(PAGE).read()
    assert '<div id="pvEmpty"><b>Loading preview…</b>' in html
    assert 'id="pvLoading"' in html and 'id="pvLoadText"' in html and 'id="pvLoadHint"' in html
    assert 'pvLoading(true, "Checking preview…"' in html
    assert 'pvLoading(true, info.mode === "command" ? "Starting preview…"' in html
    assert 'pvWait(seq)' in html and 'Preview is taking too long' in html
    assert '$("pvFrame").addEventListener("load"' in html
    assert '#cpToggle span{display:none}' in html and '#pvToggle span,#cpToggle span{display:none}' not in html


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

def test_3d_navigation_syncs_on_task_changes_even_when_preview_is_closed():
    html = open(PAGE).read()
    new_task = html[html.index("  function newTask() {"):html.index("  async function openTask(id) {")]
    open_task = html[html.index("  async function openTask(id) {"):html.index("  function renderLegacy(t) {")]
    preview = html[html.index("  async function loadPreview(force) {"):html.index("  function pvRefreshSoon")]
    assert "sync3DLink();" in new_task
    assert "sync3DLink();" in open_task
    assert preview.index("sync3DLink();") < preview.index("if (!studioOpen()) return;")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_3d_navigation_link_keeps_task_and_model_across_closed_preview():
    html = open(PAGE).read()
    helper = html[html.index("  function sync3DLink()"):html.index("  const SANDBOX_WEB")]
    preview = html[html.index("  async function loadPreview(force) {"):html.index("  function pvRefreshSoon")]
    setup = '''
const assert = require("assert");
const els = { nav3D: { href: "" } };
const $ = id => els[id] ||= {};
let TASK = null, CUR = null;
let PV = { task: null, model: "" };
const studioOpen = () => false;
'''
    checks = '''
(async () => {
  TASK = 7; CUR = {};
  await loadPreview();
  assert.equal($("nav3D").href, "/autopilot/3d/?task=7");
  PV.task = 7; PV.model = "robot.glb";
  await loadPreview();
  assert.equal($("nav3D").href, "/autopilot/3d/?task=7&model=robot.glb");
  TASK = 8;
  await loadPreview();
  assert.equal($("nav3D").href, "/autopilot/3d/?task=8");
  TASK = null; PV.task = null; PV.model = "";
  await loadPreview();
  assert.equal($("nav3D").href, "/autopilot/3d/");
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
    result = subprocess.run(["node", "-e", setup + helper + preview + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_3d_navigation_restores_saved_task_before_async_load():
    html = open(PAGE).read()
    helper = html[html.index("  function sync3DLink()"):html.index("  const SANDBOX_WEB")]
    start = html.index('  try { const saved = Number(sessionStorage.getItem("vellurTask"));')
    boot = html[start:html.index("\n  (async () => {", start)]
    setup = '''
const assert = require("assert");
const els = { nav3D: { href: "/autopilot/3d/" } };
const $ = id => els[id] ||= {};
let TASK = null;
let PV = { task: null, model: "" };
const sessionStorage = { getItem: k => k === "vellurTask" ? "42" : null };
'''
    checks = '''
assert.equal($("nav3D").href, "/autopilot/3d/?task=42");
assert.equal(TASK, 42);
'''
    result = subprocess.run(["node", "-e", setup + helper + boot + checks], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_routing_editor_image_format_and_daily_costs_are_wired():
    html = open(PAGE).read()
    # routing editor: draft saved in one PUT; image rows only offer image models and text rows never do
    assert 'id="rEditor"' in html and 'send("llm/routes", "PUT", RDRAFT)' in html
    assert "image ? x.image_generation : !x.image_generation" in html
    # image format: only shown in image mode, sent with the message
    assert 'id="imgSize"' in html and 'image_size: $("imgSize").value' in html
    for size in ("1024x1024", "832x1248", "768x1344", "1248x832", "1344x768"):
        assert f'value="{size}"' in html
    # daily spend chart with a table view for screen readers
    assert 'id="dayChart"' in html and 'id="dayTable"' in html and "renderDays(c)" in html
