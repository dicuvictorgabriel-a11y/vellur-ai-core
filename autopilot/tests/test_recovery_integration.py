"""Failure injection with REAL processes: the actual agent runner (agent/runner.py) runs as its own process with a fake
OpenCode (tests/fake_opencode.py); the controller is rebuilt from the same SQLite file to simulate its restart.

Scenario 1: the controller dies during a tool operation while the runner keeps working → the new controller re-attaches,
            the agent finishes, nothing runs twice and the model token still works.
Scenario 2: the controller AND the runner are killed (SIGKILL) during a tool operation → the new controller resumes the
            same OpenCode session automatically (no "continue" from the owner); step 1 is not repeated.
Everything is local (127.0.0.1); no model, GitHub or network is used.
"""
import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from app.agent import AgentManager
from app.router import Router

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "..", "..", "agent", "runner.py")
SECRET = "r" * 32
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required by the runner")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RunnerProcess:
    def __init__(self, work, port):
        self.work, self.port, self.p = work, port, None
        os.chmod(os.path.join(HERE, "fake_opencode.py"), 0o755)

    def start(self):
        env = dict(os.environ, AGENT_WORK=self.work, AGENT_RUNNER_SECRET=SECRET, AGENT_PORT=str(self.port),
                   OPENCODE_BIN=os.path.join(HERE, "fake_opencode.py"))
        self.p = subprocess.Popen([sys.executable, RUNNER], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        deadline = time.time() + 15
        while time.time() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.05)
        raise AssertionError("runner did not start")

    def kill(self):
        """Like a container restart: the runner and every process it started die at once (SIGKILL, no cleanup)."""
        for pid in subprocess.run(["pgrep", "-f", "fake_opencode.py"], capture_output=True, text=True).stdout.split():
            with __import__("contextlib").suppress(ProcessLookupError):
                os.kill(int(pid), signal.SIGKILL)
        os.killpg(self.p.pid, signal.SIGKILL)
        self.p.wait()


def controller(rig, port, db_path):
    ctrl = rig(agent_runner_url=f"http://127.0.0.1:{port}", agent_runner_secret=SECRET, db_path=db_path)
    router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    mgr = AgentManager(ctrl, router, runner_transport=httpx.AsyncHTTPTransport(), poll_seconds=0.1)
    return ctrl, router, mgr


async def until(fn, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if fn():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached")


def crash(mgr):
    """The controller process dies: its jobs stop, nothing in memory survives (a new Router/AgentManager is built)."""
    mgr._closing = True               # what happens on SIGTERM; a SIGKILL skips even this (the DB state is the same)
    for j in list(mgr._jobs.values()):
        j.cancel()


def task_dir(work, tid):
    return os.path.join(work, f"t{tid}")


async def start_task(mgr, work):
    t = await mgr.create("Write step1.txt and step2.txt", "vellur-coder")
    tid = t["id"]
    repo = os.path.join(task_dir(work, tid), "repo")
    # wait until step 1 is written and the long `npm test` tool is running (the interruption happens mid-tool)
    await until(lambda: any(e["kind"] == "tool" and e["data"].get("status") == "running" for e in mgr.events(tid)))
    return tid, repo


async def test_controller_restart_reattaches_to_the_running_agent(rig, tmp_path):
    work, port, db_path = str(tmp_path / "work"), free_port(), str(tmp_path / "autopilot.db")
    runner = RunnerProcess(work, port)
    runner.start()
    try:
        ctrl, router, mgr = controller(rig, port, db_path)
        tid, repo = await start_task(mgr, work)
        token_tag = "task:%d" % tid
        crash(mgr)
        await asyncio.sleep(0.3)
        assert ctrl.db.one("SELECT state FROM tasks WHERE id=?", (tid,))["state"] == "running"   # nobody marked it failed

        ctrl2, router2, mgr2 = controller(rig, port, db_path)
        # the agent's model token survived the restart (only its hash is stored)
        tokens = [r["key"] for r in ctrl2.db.all("SELECT key FROM kv WHERE key LIKE 'proxy_token_%'")]
        assert len(tokens) == 1
        await mgr2.recover()
        await until(lambda: mgr2.task(tid)["state"] == "running")
        open(os.path.join(repo, "release"), "w").close()          # let the long tool finish
        await until(lambda: mgr2.task(tid)["state"] in ("done", "failed", "publishing") and tid not in mgr2._jobs)

        t = mgr2.task(tid)
        assert t["state"] in ("done", "publishing") and t["outcome"] == "completed", (t["state"], t["error"])
        assert open(os.path.join(task_dir(work, tid), "invocations")).read().split() == ["first"]   # never restarted
        assert open(os.path.join(repo, "step1.txt")).read() == "step one\n"
        texts = [e["data"].get("text", "") for e in mgr2.events(tid)]
        assert any("Reconnected: the agent kept working" in x for x in texts)
        kinds = [e["kind"] for e in mgr2.events(tid)]
        assert kinds.count("report") == 1
        writes = [e for e in mgr2.events(tid) if e["kind"] == "tool" and e["data"].get("tool") == "write"]
        assert len(writes) == 2                                    # step1 + step2, each imported exactly once
        assert not ctrl2.db.all("SELECT key FROM kv WHERE key LIKE 'proxy_token_%'")   # revoked once the run ended
        assert token_tag
    finally:
        runner.kill()


async def test_controller_and_runner_killed_mid_tool_resume_automatically(rig, tmp_path):
    work, port, db_path = str(tmp_path / "work"), free_port(), str(tmp_path / "autopilot.db")
    runner = RunnerProcess(work, port)
    runner.start()
    try:
        ctrl, router, mgr = controller(rig, port, db_path)
        tid, repo = await start_task(mgr, work)
        ctrl.db.x("INSERT INTO usage(ts,month,role,target,provider,model,ok,cost_usd,cost_source,tag) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (1, "2026-11", "vellur-coder", "p:m", "p", "m", 1, 0.0123, "provider", f"task:{tid}"))
        crash(mgr)
        runner.kill()                                              # the agent process dies in the middle of `npm test`
        open(os.path.join(repo, "release"), "w").close()           # a resumed run must not wait
        runner.start()                                             # the runner container comes back

        ctrl2, router2, mgr2 = controller(rig, port, db_path)
        await mgr2.recover()
        await until(lambda: mgr2.task(tid)["state"] in ("done", "failed", "publishing") and tid not in mgr2._jobs, 45)

        t = mgr2.task(tid)
        assert t["state"] in ("done", "publishing") and t["outcome"] == "completed", (t["state"], t["error"])
        assert t["runs"] == 2                                      # one automatic resume, no owner message
        assert open(os.path.join(task_dir(work, tid), "invocations")).read().split() == ["first", "resume"]
        assert open(os.path.join(repo, "step1.txt")).read() == "step one\n"      # not repeated
        assert open(os.path.join(repo, "step2.txt")).read() == "step two\n"
        texts = [e["data"].get("text", "") for e in mgr2.events(tid)]
        assert any("Execution was interrupted" in x for x in texts)
        assert any("Workspace restored. Continuing from the last verified step" in x for x in texts)
        assert not [e for e in mgr2.events(tid) if e["kind"] == "user" and e["run"] == 2]   # the owner typed nothing
        assert ctrl2.db.kv_get(f"task_recoveries_{tid}") == 1
        assert round(router2.tag_cost(f"task:{tid}"), 4) == 0.0123                # cost recorded once, kept
    finally:
        runner.kill()


async def test_recovery_stops_after_the_limit_and_says_why(rig, tmp_path):
    work, port, db_path = str(tmp_path / "work"), free_port(), str(tmp_path / "autopilot.db")
    runner = RunnerProcess(work, port)
    runner.start()
    try:
        ctrl, router, mgr = controller(rig, port, db_path)
        tid, repo = await start_task(mgr, work)
        crash(mgr)
        runner.kill()
        runner.start()
        ctrl.db.kv_set(f"task_recoveries_{tid}", AgentManager.MAX_RECOVERIES)
        ctrl2, router2, mgr2 = controller(rig, port, db_path)
        await mgr2.recover()
        await until(lambda: mgr2.task(tid)["state"] == "failed" and tid not in mgr2._jobs)
        msg = [e["data"].get("message", "") for e in mgr2.events(tid) if e["kind"] == "error"][-1]
        assert "could not resume this task automatically" in msg and "Resume" in msg
        assert open(os.path.join(task_dir(work, tid), "invocations")).read().split() == ["first"]
    finally:
        runner.kill()
