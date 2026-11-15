"""The REAL agent runner (agent/runner.py) with a fake OpenCode: workspace persistence, retention and restarts."""
import importlib.util
import json
import os
import shutil
import stat
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[2] / "agent" / "runner.py"
SECRET = "r" * 32
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

FAKE_OPENCODE = """#!/bin/sh
# fake OpenCode: one session event, edits a file, then waits until the release file exists (or 20 s)
echo '{"type":"step_start","sessionID":"ses_fake","part":{}}'
echo "work" >> generated.txt
i=0; while [ ! -f "@RELEASE@" ] && [ $i -lt 200 ]; do sleep 0.1; i=$((i+1)); done
echo '{"type":"text","sessionID":"ses_fake","part":{"text":"done. STATUS: COMPLETE"}}'
"""


def load_runner(work, opencode):
    os.environ.update(AGENT_RUNNER_SECRET=SECRET, AGENT_WORK=str(work), OPENCODE_BIN=str(opencode))
    spec = importlib.util.spec_from_file_location(f"runner_{time.time_ns()}", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.auto_shots = lambda *a, **k: None          # no browser in unit tests
    return mod


class Runner:
    def __init__(self, tmp):
        self.tmp = tmp
        self.work = tmp / "work"
        self.release = tmp / "release"
        oc = tmp / "opencode"
        oc.write_text(FAKE_OPENCODE.replace("@RELEASE@", str(self.release)))   # the runner passes no custom env
        oc.chmod(oc.stat().st_mode | stat.S_IEXEC)
        self.oc = oc
        self.start()

    def start(self):
        self.mod = load_runner(self.work, self.oc)
        os.makedirs(self.work, exist_ok=True)
        self.mod.load_state()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self.mod.H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def restart(self):
        """Like recreating the container: the process is gone, the volume (work folder) stays."""
        self.srv.shutdown()
        self.srv.server_close()
        self.start()

    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}{path}", data=data, method=method,
                                     headers={"x-runner-secret": SECRET})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def create(self, tid, start=True):
        meta = {"id": tid, "prompt": "build it", "model": "vellur-coder", "proxy_url": "http://x/v1", "proxy_token": "t"}
        assert self.call("POST", "/tasks", meta)[0] == 200
        assert self.call("PUT", f"/tasks/{tid}/source", raw=b"")[0] in (200, 500)  # empty snapshot is fine
        if start:
            assert self.call("POST", f"/tasks/{tid}/start")[0] == 200

    def wait(self, tid, state="done", timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            code, st = self.call("GET", f"/tasks/{tid}")
            if code == 200 and st["state"] == state:
                return st
            time.sleep(0.1)
        raise AssertionError(f"task {tid} did not reach {state}: {st}")


@pytest.fixture
def runner(tmp_path):
    r = Runner(tmp_path)
    yield r
    r.release.touch()
    r.srv.shutdown()


def test_no_workspace_is_deleted_automatically_even_beyond_the_old_limit(runner):
    runner.release.touch()
    for tid in range(1, 16):                       # more than the former KEEP_TASKS = 12
        runner.create(tid)
        runner.wait(tid)
    for tid in range(1, 16):
        assert runner.call("GET", f"/tasks/{tid}")[0] == 200
        assert (runner.work / f"t{tid}" / "repo" / "generated.txt").exists()


def test_an_active_task_cannot_be_deleted_and_create_never_wipes_a_folder(runner):
    runner.create(7)                               # running: waits for the release file
    runner.wait(7, "running")
    assert runner.call("DELETE", "/tasks/7")[0] == 409
    assert (runner.work / "t7" / "repo").exists()
    runner.release.touch()
    runner.wait(7)
    # an existing folder for a "new" task id is moved aside, never deleted
    (runner.work / "t9" / "repo").mkdir(parents=True)
    (runner.work / "t9" / "repo" / "precious.txt").write_text("keep me")
    runner.create(9, start=False)
    kept = list(runner.work.glob("t9.preserved-*"))
    assert len(kept) == 1 and (kept[0] / "repo" / "precious.txt").read_text() == "keep me"


def test_concurrent_deletes_and_starts_do_not_race(runner):
    runner.release.touch()
    runner.create(1)
    runner.wait(1)
    deletes, follow_ups = [], []

    def delete():
        deletes.append(runner.call("DELETE", "/tasks/1")[0])

    def follow_up():
        follow_ups.append(runner.call("POST", "/tasks/1/message", {"prompt": "more", "proxy_token": "t"})[0])
    runner.release.unlink()                        # a started follow-up keeps running while the deletes arrive
    threads = [threading.Thread(target=f) for f in (delete, follow_up, delete, follow_up, delete)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    # Invariant: a run that was started always has its workspace; a delete never removes a running task.
    if 200 in follow_ups:
        assert (runner.work / "t1" / "repo").exists(), (deletes, follow_ups)
        assert follow_ups.count(200) == 1             # one run at a time
    runner.release.touch()
    if 200 not in follow_ups:
        assert deletes.count(200) >= 1 and not (runner.work / "t1").exists()


def test_restart_during_the_first_run_keeps_files_and_session(runner):
    runner.create(3)
    runner.wait(3, "running")
    time.sleep(0.5)                                 # the session event has been read
    runner.restart()                                # container recreated mid-run
    code, st = runner.call("GET", "/tasks/3")
    assert code == 200 and st["state"] == "failed" and "restarted" in st["error"]
    assert st["session"] == "ses_fake"              # saved as soon as it was known: a follow-up continues it
    assert (runner.work / "t3" / "repo" / "generated.txt").exists()
    runner.release.touch()
    assert runner.call("POST", "/tasks/3/message", {"prompt": "continue", "proxy_token": "t"})[0] == 200
    runner.wait(3)


def test_a_folder_without_state_is_still_reachable_after_restart(runner):
    runner.release.touch()
    runner.create(4)
    runner.wait(4)
    (runner.work / "t4" / "state.json").unlink()
    runner.restart()
    code, st = runner.call("GET", "/tasks/4")
    assert code == 200 and "state file was missing" in st["error"]
    # without a session a follow-up is refused with a clear code, and allowed as a fresh session on request
    assert runner.call("POST", "/tasks/4/message", {"prompt": "x", "proxy_token": "t"}) == (410, {"error": "this task has no agent session to continue", "code": "no_session"})
    full = {"prompt": "x", "proxy_token": "t", "proxy_url": "http://x/v1", "model": "vellur-coder", "new_session": True}
    assert runner.call("POST", "/tasks/4/message", full)[0] == 200      # the controller always sends model and URL
    runner.wait(4)


def test_missing_workspace_and_unknown_route_are_distinguished(runner):
    code, body = runner.call("GET", "/tasks/999")
    assert code == 404 and body["code"] == "workspace_missing"
    runner.release.touch()
    runner.create(5)
    runner.wait(5)
    code, body = runner.call("GET", "/tasks/5/no-such-action")
    assert code == 404 and body["code"] == "unknown_route"
    assert runner.call("GET", "/healthz")[1]["version"] >= 3


def test_saved_changes_can_be_reapplied_to_a_fresh_workspace(runner):
    runner.release.touch()
    runner.create(6)
    runner.wait(6)
    diff = runner.call("GET", "/tasks/6/diff")[1]["diff"]
    assert "generated.txt" in diff
    runner.create(8, start=False)
    code, res = runner.call("PUT", "/tasks/8/patch", raw=diff.encode())
    assert code == 200 and res["ok"] is True
    assert (runner.work / "t8" / "repo" / "generated.txt").read_text() == "work\n"
    ws = {w["name"]: w for w in runner.call("GET", "/workspaces")[1]["workspaces"]}
    assert {"t6", "t8"} <= set(ws)
