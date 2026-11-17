"""The screenshot helper must survive a full /tmp: isolated temp dirs on the work volume, reliable cleanup, and a
stale-directory sweep for browsers killed with SIGKILL. These tests use a stub browser for the failure paths and the
real headless Chromium (when the image ships one) for desktop/mobile captures."""
import importlib.machinery
import importlib.util
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "agent" / "vellur-screenshot"

_loader = importlib.machinery.SourceFileLoader("vellur_screenshot", str(TOOL))
spec = importlib.util.spec_from_loader(_loader.name, _loader)
vs = importlib.util.module_from_spec(spec)
_loader.exec_module(vs)

BROWSERS = sorted(Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")).glob(
    "chromium_headless_shell-*/chrome-*/chrome-headless-shell")) + sorted(
    Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")).glob(
        "chromium_headless_shell-*/chrome-linux/headless_shell"))
REAL_BROWSER = str(BROWSERS[-1]) if BROWSERS else None


def cli(args, env, timeout=180):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          env=dict(os.environ, **env), timeout=timeout)


def png_size(path):
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", data[16:24])


def leftovers(base):
    if not Path(base).is_dir():
        return []
    return sorted(p.name for p in Path(base).iterdir() if p.name.startswith(vs.TMP_PREFIX))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    shots = tmp_path / "t1" / "shots"
    shots.mkdir(parents=True)
    monkeypatch.delenv("VELLUR_SCREENSHOT_TMPDIR", raising=False)
    monkeypatch.delenv("AGENT_WORK", raising=False)
    return shots


def test_temp_base_stays_next_to_shots_and_ignores_unusable_tmp(workspace, monkeypatch, tmp_path):
    full_tmp = tmp_path / "full-tmp"
    full_tmp.write_text("")                      # a path that cannot hold directories: the /tmp is full case
    monkeypatch.setenv("TMPDIR", str(full_tmp))
    base = vs._tmp_base(str(workspace))
    assert base == str(workspace.parent / "tmp")
    assert vs._usable(base)


def test_temp_base_falls_back_when_task_dir_is_blocked(workspace, monkeypatch, tmp_path):
    (workspace.parent / "tmp").write_text("blocked")   # cannot create the per-task directory
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("AGENT_WORK", str(work))
    assert vs._tmp_base(str(workspace)) == str(work / ".tmp" / "screenshots")


def test_temp_base_honours_explicit_override(workspace, monkeypatch, tmp_path):
    override = tmp_path / "shots-tmp"
    monkeypatch.setenv("VELLUR_SCREENSHOT_TMPDIR", str(override))
    assert vs._tmp_base(str(workspace)) == str(override)


def test_sweep_removes_only_our_stale_directories(tmp_path):
    base = tmp_path / "tmp"
    base.mkdir()
    old = time.time() - vs.DEAD_SECONDS - 60
    dead = base / f"{vs.TMP_PREFIX}dead"
    dead.mkdir()
    (dead / ".pid").write_text("999999")
    os.utime(dead, (old, old))
    lost = base / f"{vs.TMP_PREFIX}lost"
    lost.mkdir()
    old_stale = time.time() - vs.STALE_SECONDS - 60
    os.utime(lost, (old_stale, old_stale))
    live = base / f"{vs.TMP_PREFIX}live"
    live.mkdir()
    (live / ".pid").write_text(str(os.getpid()))
    os.utime(live, (old, old))
    unrelated = base / "pytest-of-agent"
    unrelated.mkdir()
    (base / "notes.txt").write_text("keep")
    vs._sweep(str(base))
    assert not dead.exists() and not lost.exists()
    assert live.exists() and unrelated.exists() and (base / "notes.txt").exists()


def test_stub_browser_failure_cleans_isolated_dir(workspace, tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><title>t</title>")
    stub = tmp_path / "stub"
    stub.write_text("#!/bin/sh\nexit 3\n")
    stub.chmod(0o755)
    r = cli([str(page), "shot", "1440x900"],
            {"VELLUR_SHOTS": str(workspace), "VELLUR_BROWSER": str(stub), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 1
    assert "could not take the screenshot" in r.stderr
    assert leftovers(workspace.parent / "tmp") == []
    assert not (workspace / "shot.png").exists()


def test_sigterm_cleans_isolated_dir_and_stops_browser(workspace, tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><title>t</title>")
    pidfile = tmp_path / "browser.pid"
    stub = tmp_path / "stub"
    stub.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nexec sleep 30\n")
    stub.chmod(0o755)
    env = dict(os.environ, VELLUR_SHOTS=str(workspace), VELLUR_BROWSER=str(stub), PATH="/usr/bin:/bin")
    p = subprocess.Popen([sys.executable, str(TOOL), str(page), "shot"],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    base = workspace.parent / "tmp"
    try:
        deadline = time.time() + 10
        while time.time() < deadline and (not leftovers(base) or not pidfile.exists()):
            time.sleep(0.05)
        assert leftovers(base), "the tool did not create an isolated temp directory"
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=15)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == -signal.SIGTERM
    assert leftovers(base) == []


@pytest.mark.skipif(not REAL_BROWSER, reason="no headless Chromium in this image")
def test_real_desktop_and_mobile_capture_with_full_tmp(workspace, tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path / "full-tmp"))     # unusable inherited temp: must not matter
    (tmp_path / "full-tmp").write_text("")
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><meta charset=utf-8><body style='margin:0;background:#0b0f0a;color:#c6f24e;"
                    "font:600 48px sans-serif;display:grid;place-items:center;height:100vh'>NOVA SMOKE</body>")
    shots = workspace
    env = {"VELLUR_SHOTS": str(shots), "VELLUR_BROWSER": REAL_BROWSER}
    for size, dims in (("1440x900", (1440, 900)), ("390x844", (390, 844))):
        r = cli([str(page), f"nova-{size}", size], env)
        assert r.returncode == 0, r.stderr
        assert png_size(shots / f"nova-{size}.png") == dims
    assert leftovers(workspace.parent / "tmp") == []


def test_runner_sets_a_per_task_tmpdir(tmp_path, monkeypatch):
    spec2 = importlib.util.spec_from_file_location("vellur_runner_tmp", ROOT / "agent" / "runner.py")
    runner = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(runner)
    t = {"dir": str(tmp_path / "t7")}
    Path(t["dir"]).mkdir()
    assert runner.task_tmp(t) == t["dir"] + "/tmp"
    assert (tmp_path / "t7" / "tmp").is_dir()
