import importlib.util
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location("vellur_runner", Path(__file__).resolve().parents[2] / "agent" / "runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def task(tmp_path, monkeypatch):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    t = {"dir": str(tmp_path), "meta": {"model": "vellur-coder", "prompt": "test", "proxy_url": "http://proxy",
                                      "proxy_token": "test"}, "state": "running", "run": 1, "session": "",
         "started": 1, "ended": None, "exit": None, "changed": 0, "error": "", "proc": None}
    monkeypatch.setattr(runner, "tasks", {1: t})
    monkeypatch.setattr(runner, "git", lambda *a, **k: b"")
    monkeypatch.setattr(runner, "checkpoint", lambda *a: None)
    return t


def run(task, monkeypatch, exit_code=0):
    class Process:
        stdout = io.BytesIO(b"")
        returncode = exit_code

        def poll(self):
            return self.returncode

        def wait(self):
            return self.returncode

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: Process())
    runner.run_task(1)


def events(task):
    path = Path(task["dir"]) / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("value", [None, False, "true", 1, [], {}])
def test_default_and_non_boolean_never_call_screenshots(task, monkeypatch, value):
    if value is not None:
        task["meta"]["auto_screenshots"] = value
    calls = []
    monkeypatch.setattr(runner, "auto_shots", lambda *a: calls.append(a))
    run(task, monkeypatch)
    assert task["state"] == "done"
    assert calls == []
    assert events(task) == []


def test_enabled_screenshots_use_preview_and_both_sizes(task, monkeypatch):
    task["meta"]["auto_screenshots"] = True
    monkeypatch.setattr(runner, "preview_plan", lambda t: {"mode": "command", "key": "site"})
    previews, calls = [], []
    monkeypatch.setattr(runner, "ensure_preview", lambda *a, **k: previews.append((a, k)) or "")
    monkeypatch.setattr(runner.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or SimpleNamespace(returncode=0))
    run(task, monkeypatch)
    assert task["state"] == "done"
    assert len(previews) == 1
    assert [cmd[-1] for cmd, _ in calls] == ["desktop", "mobile"]
    assert all(cmd[1].endswith("/tasks/1/preview/") for cmd, _ in calls)
    assert events(task) == []


@pytest.mark.parametrize("mode", ["none", "command"])
def test_missing_preview_is_nonfatal_notice(task, monkeypatch, mode):
    task["meta"]["auto_screenshots"] = True
    monkeypatch.setattr(runner, "preview_plan", lambda t: {"mode": mode, "why": "not found"})
    monkeypatch.setattr(runner, "ensure_preview", lambda *a, **k: "server failed")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("screenshot must not run"))
    run(task, monkeypatch)
    assert task["state"] == "done"
    notice = events(task)[0]
    assert notice["category"] == "screenshot_notice" and notice["run"] == 1
    assert "preview unavailable" in notice["text"]
    assert notice["text"] in (Path(task["dir"]) / "log").read_text()


@pytest.mark.parametrize("failure,expected", [
    (FileNotFoundError("vellur-screenshot"), "vellur-screenshot"),
    (subprocess.TimeoutExpired("vellur-screenshot", 120), "TimeoutExpired"),
    (SimpleNamespace(returncode=1, stderr=b"no browser is installed", stdout=b""), "no browser is installed"),
    (SimpleNamespace(returncode=2, stderr=b"capture error", stdout=b""), "capture error"),
])
def test_capture_failure_is_nonfatal_notice(task, monkeypatch, failure, expected):
    task["meta"]["auto_screenshots"] = True
    monkeypatch.setattr(runner, "preview_plan", lambda t: {"mode": "static", "root": "."})

    def capture(*a, **k):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(runner.subprocess, "run", capture)
    run(task, monkeypatch)
    assert task["state"] == "done" and task["error"] == ""
    notices = events(task)
    assert len(notices) == 2
    assert all(e["category"] == "screenshot_notice" and expected in e["text"] for e in notices)
    assert all(e["text"] in (Path(task["dir"]) / "log").read_text() for e in notices)


@pytest.mark.parametrize("exit_code,state", [(0, "done"), (7, "failed")])
def test_unexpected_screenshot_error_preserves_run_result(task, monkeypatch, exit_code, state):
    task["meta"]["auto_screenshots"] = True
    monkeypatch.setattr(runner, "auto_shots", lambda *a: (_ for _ in ()).throw(RuntimeError("unexpected")))
    run(task, monkeypatch, exit_code=exit_code)
    assert task["state"] == state and task["exit"] == exit_code
    assert "unexpected" in events(task)[0]["text"]


@pytest.mark.parametrize("value,expected", [(None, False), (False, False), ("true", False), (True, True)])
def test_followup_resets_opt_in_per_run(task, monkeypatch, value, expected):
    task.update(state="done", session="previous-session")
    task["meta"]["auto_screenshots"] = True
    body = {"prompt": "next"}
    if value is not None:
        body["auto_screenshots"] = value
    handler = object.__new__(runner.H)
    handler.path = "/tasks/1/message"
    handler.headers = {"x-runner-secret": "test"}
    handler.client_address = ("127.0.0.1", 1234)
    handler.body = lambda cap: json.dumps(body).encode()
    handler.send = lambda code, obj: (code, obj)
    monkeypatch.setattr(runner, "SECRET", "test")
    monkeypatch.setattr(runner.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    code, result = handler.route("POST")
    assert code == 200 and result["run"] == 2
    assert task["meta"]["auto_screenshots"] is expected
