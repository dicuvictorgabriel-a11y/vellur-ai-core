#!/usr/bin/env python3
"""VELLUR agent runner — runs INSIDE the isolated agent container, never on the host.

It receives one coding task at a time from the Autopilot controller (shared secret, private Docker network), unpacks
the repository snapshot, runs a headless OpenCode session in it (`opencode run --format json`) and reports the live
event stream, screenshots and the resulting changes. Follow-up messages continue the same OpenCode session in the same
workspace (`--session`), so the agent keeps its context. Task state is saved next to the workspace and survives a
container restart.
The container holds no production secret, no GitHub token and no Docker socket: the only credential a task gets is a
short-lived inference token for the VELLUR model router. Standard library only.
"""
import base64
import io
import json
import os
import shutil
import signal
import subprocess
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORK = os.environ.get("AGENT_WORK", "/work")
SECRET = os.environ.get("AGENT_RUNNER_SECRET", "")
OPENCODE = os.environ.get("OPENCODE_BIN", "opencode")
KEEP_TASKS = 8
MAX_EVENT = 6000          # characters kept per string field of an event (tool output, file contents)
MAX_SHOT = 4 * 1024 * 1024
MAX_SOURCE = 400 * 1024 * 1024
MAX_DIFF = 2 * 1024 * 1024
MAX_FILE = 5 * 1024 * 1024
MAX_FILES_TOTAL = 30 * 1024 * 1024
MAX_FILES = 2000
PASS_ENV = ("PATH", "LANG", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
            "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE")
GIT_ID = {"GIT_AUTHOR_NAME": "VELLUR Agent", "GIT_AUTHOR_EMAIL": "agent@vellur.invalid",
          "GIT_COMMITTER_NAME": "VELLUR Agent", "GIT_COMMITTER_EMAIL": "agent@vellur.invalid"}

tasks = {}
lock = threading.Lock()


def tdir(tid):
    return os.path.join(WORK, f"t{int(tid)}")


def git(repo, *args, check=True, cap=None):
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env.update(GIT_ID)
    env.update({"HOME": os.path.dirname(repo), "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "safe.directory=*", *args], cwd=repo, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {r.stderr.decode(errors='replace')[:300]}")
    return r.stdout if cap is None else r.stdout[:cap]


def safe_extract(data, dest):
    """Extract a GitHub tarball (one top-level folder) without following anything outside dest."""
    root = os.path.realpath(dest)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for m in tar:
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            rel = os.path.normpath(parts[1])
            if rel.startswith("..") or os.path.isabs(rel):
                continue
            target = os.path.join(root, rel)
            if not os.path.realpath(os.path.dirname(target)).startswith(root):
                continue
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.isfile():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tar.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(target, 0o755 if m.mode & 0o100 else 0o644)
            elif m.issym():
                link = os.path.normpath(os.path.join(os.path.dirname(rel), m.linkname))
                if not link.startswith("..") and not os.path.isabs(m.linkname):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if not os.path.lexists(target):
                        os.symlink(m.linkname, target)


def opencode_config(meta):
    model = {"tool_call": True, "limit": {"context": 131072, "output": 16384}}
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"vellur": {"npm": "@ai-sdk/openai-compatible", "name": "VELLUR AI Core",
                                "options": {"baseURL": meta["proxy_url"], "apiKey": meta["proxy_token"]},
                                "models": {n: dict(model, name=n) for n in ("vellur-coder", "vellur-chat", "vellur-max")}}},
        "model": f"vellur/{meta['model']}", "small_model": f"vellur/{meta['model']}",
        "permission": {"edit": "allow", "bash": "allow", "webfetch": "allow"},
        "autoupdate": False, "share": "disabled",
    }


def clip(v):
    """Shrink one OpenCode event: long strings (file contents, command output) are cut so events stay small."""
    if isinstance(v, str):
        return v if len(v) <= MAX_EVENT else v[:MAX_EVENT] + f"\n… [{len(v) - MAX_EVENT} more characters]"
    if isinstance(v, dict):
        return {k: clip(x) for k, x in v.items() if k not in ("responseHeaders", "snapshot")}
    if isinstance(v, list):
        return [clip(x) for x in v[:200]]
    return v


def save_state(t):
    keep = {k: t[k] for k in ("state", "started", "ended", "exit", "changed", "error", "session", "run")}
    keep["meta"] = {k: v for k, v in t["meta"].items() if k != "proxy_token"}
    tmp = os.path.join(t["dir"], "state.json.tmp")
    with open(tmp, "w") as f:
        json.dump(keep, f)
    os.replace(tmp, os.path.join(t["dir"], "state.json"))


def load_state():
    """After a container restart: reload saved tasks so follow-ups can continue; interrupted runs are failed."""
    if not os.path.isdir(WORK):
        return
    for name in os.listdir(WORK):
        f = os.path.join(WORK, name, "state.json")
        if not (name.startswith("t") and name[1:].isdigit() and os.path.exists(f)):
            continue
        try:
            st = json.load(open(f))
        except (OSError, ValueError):
            continue
        t = dict(st, dir=os.path.join(WORK, name), proc=None)
        if t["state"] in ("created", "running"):
            t["state"], t["error"], t["ended"] = "failed", "the agent workspace restarted during this run", time.time()
        tasks[int(name[1:])] = t


def pump(t, stream, path):
    """Copy OpenCode's JSON event lines into events.jsonl, tagged with the run number and clipped."""
    with open(path, "ab", buffering=0) as out:
        for raw in iter(stream.readline, b""):
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict):
                    raise ValueError
            except ValueError:
                ev = {"type": "raw", "text": line}
            ev = clip(ev)
            ev["run"] = t["run"]
            if not t.get("session") and isinstance(ev.get("sessionID"), str):
                t["session"] = ev["sessionID"]
            out.write((json.dumps(ev) + "\n").encode())


def run_task(tid):
    t = tasks[tid]
    d, repo, home = t["dir"], os.path.join(t["dir"], "repo"), os.path.join(t["dir"], "home")
    log = open(os.path.join(d, "log"), "ab", buffering=0)
    try:
        if not os.path.isdir(os.path.join(repo, ".git")):
            git(repo, "init", "-q", "-b", "main")
            # build caches and installed dependencies are never part of the result (only untracked files are affected)
            with open(os.path.join(repo, ".git", "info", "exclude"), "a") as f:
                f.write("\n__pycache__/\n*.pyc\nnode_modules/\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\n.venv/\n"
                        ".next/\n.turbo/\n.cache/\n.DS_Store\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "--allow-empty", "-m", "baseline")
        cfg = os.path.join(home, "opencode.json")
        with open(cfg, "w") as f:
            json.dump(opencode_config(t["meta"]), f)
        shots = os.path.join(d, "shots")
        os.makedirs(shots, exist_ok=True)
        env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
        env.update(GIT_ID)
        env.update({"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                    "XDG_DATA_HOME": os.path.join(home, ".local/share"), "XDG_CACHE_HOME": os.path.join(home, ".cache"),
                    "OPENCODE_CONFIG": cfg, "CI": "1", "TERM": "dumb", "NO_COLOR": "1", "VELLUR_SHOTS": shots})
        cmd = [OPENCODE, "run", "--auto", "--format", "json", "-m", f"vellur/{t['meta']['model']}"]
        if t.get("session"):
            cmd += ["--session", t["session"]]
        cmd += ["--", t["meta"]["prompt"]]
        log.write(f"[runner] run {t['run']}: starting OpenCode with model {t['meta']['model']}\n".encode())
        p = subprocess.Popen(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                             start_new_session=True)
        t["proc"] = p
        reader = threading.Thread(target=pump, args=(t, p.stdout, os.path.join(d, "events.jsonl")), daemon=True)
        reader.start()
        limit = time.time() + 60 * float(t["meta"].get("max_minutes", 30)) + 60
        while p.poll() is None:
            if time.time() > limit or t.get("cancel"):
                reason = "cancelled" if t.get("cancel") else "time limit"
                log.write(f"\n[runner] stopping: {reason}\n".encode())
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                    time.sleep(3)
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                t["state"] = "cancelled" if t.get("cancel") else "timeout"
                break
            time.sleep(0.5)
        p.wait()
        reader.join(timeout=10)
        t["exit"] = p.returncode
        git(repo, "add", "-A", check=False)
        names = git(repo, "diff", "--cached", "--no-renames", "--name-status", "-z", "HEAD", check=False).decode(errors="replace")
        t["changed"] = len([x for x in names.split("\0") if x]) // 2
        if t["state"] == "running":
            t["state"] = "done" if p.returncode == 0 else "failed"
    except Exception as e:  # noqa: BLE001 - report everything to the controller
        log.write(f"\n[runner] error: {e}\n".encode())
        t["state"] = "failed"
        t["error"] = str(e)[:300]
    finally:
        t["ended"] = time.time()
        t["proc"] = None
        log.close()
        save_state(t)


def activity(t):
    """What runs right now inside this task (OpenCode reports a command only when it finishes): the busiest child
    processes of the OpenCode session with their CPU time, read from /proc. [] when nothing runs."""
    p = t.get("proc")
    if not p or p.poll() is not None:
        return []
    tick = os.sysconf("SC_CLK_TCK")
    try:
        uptime = float(open("/proc/uptime").read().split()[0])
    except OSError:
        return []
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == p.pid:
            continue
        try:
            raw = open(f"/proc/{pid}/stat").read()
            fields = raw[raw.rindex(")") + 2:].split()
            if int(fields[3]) != p.pid:          # session id: only processes started by this task's OpenCode
                continue
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
        except (OSError, ValueError, IndexError):
            continue
        name = os.path.basename(cmd.split(" ", 1)[0]) if cmd else ""
        if not cmd or name in ("sh", "bash", "dash", "timeout", "env"):
            continue
        cpu = (int(fields[11]) + int(fields[12])) / tick
        out.append({"cmd": cmd[:200], "cpu_s": round(cpu, 1), "elapsed_s": round(uptime - int(fields[19]) / tick)})
    return sorted(out, key=lambda x: -x["cpu_s"])[:3]


def file_summary(repo):
    """[{path, status, added, removed}] of the changes against the baseline (binary files: added/removed = None)."""
    git(repo, "add", "-A", check=False)
    st = git(repo, "diff", "--cached", "--no-renames", "--name-status", "-z", "HEAD", check=False).decode(errors="replace")
    parts = [x for x in st.split("\0") if x]
    status = dict(zip(parts[1::2], parts[0::2]))
    nums = {}
    for line in git(repo, "diff", "--cached", "--no-renames", "--numstat", "HEAD", check=False).decode(errors="replace").splitlines():
        a, r, path = (line.split("\t", 2) + ["", ""])[:3]
        nums[path] = (None if a == "-" else int(a or 0), None if r == "-" else int(r or 0))
    return [{"path": p, "status": status[p][:1], "added": nums.get(p, (None, None))[0],
             "removed": nums.get(p, (None, None))[1]} for p in sorted(status)][:500]


def changed_files(repo):
    raw = git(repo, "diff", "--cached", "--no-renames", "--name-status", "-z", "HEAD", check=False).decode(errors="replace")
    parts = [x for x in raw.split("\0") if x]
    files, total = [], 0
    if len(parts) // 2 > MAX_FILES:
        raise RuntimeError(f"too many changed files ({len(parts) // 2}); is a dependency folder missing from .gitignore?")
    for status, path in zip(parts[0::2], parts[1::2]):
        full = os.path.join(repo, path)
        if status.startswith("D"):
            files.append({"path": path, "deleted": True})
            continue
        if os.path.islink(full):
            files.append({"path": path, "mode": "120000", "content_b64": base64.b64encode(os.readlink(full).encode()).decode()})
            continue
        size = os.path.getsize(full)
        total += size
        if size > MAX_FILE or total > MAX_FILES_TOTAL:
            raise RuntimeError(f"changed file too large to publish: {path}")
        with open(full, "rb") as f:
            files.append({"path": path, "mode": "100755" if os.access(full, os.X_OK) else "100644",
                          "content_b64": base64.b64encode(f.read()).decode()})
    return files


def cleanup():
    done = sorted((t["ended"] or 0, tid) for tid, t in tasks.items() if t["state"] not in ("created", "running"))
    for _, tid in done[:-KEEP_TASKS]:
        shutil.rmtree(tasks[tid]["dir"], ignore_errors=True)
        tasks.pop(tid, None)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send(self, code, obj=None, raw=None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(obj if obj is not None else {}).encode()
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self, cap):
        n = int(self.headers.get("content-length") or 0)
        if n > cap:
            raise ValueError("body too large")
        return self.rfile.read(n)

    def route(self, method):
        if not SECRET or self.headers.get("x-runner-secret") != SECRET:
            return self.send(401, {"error": "unauthorized"})
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if parts == ["healthz"]:
            return self.send(200, {"ok": True, "busy": any(t["state"] in ("created", "running") for t in tasks.values())})
        if not parts or parts[0] != "tasks":
            return self.send(404, {"error": "not found"})
        if method == "POST" and len(parts) == 1:
            meta = json.loads(self.body(1_000_000))
            tid = int(meta["id"])
            with lock:
                if any(t["state"] in ("created", "running") for t in tasks.values()):
                    return self.send(409, {"error": "busy"})
                d = tdir(tid)
                shutil.rmtree(d, ignore_errors=True)
                os.makedirs(os.path.join(d, "repo"))
                os.makedirs(os.path.join(d, "home"))
                tasks[tid] = {"state": "created", "dir": d, "meta": meta, "proc": None, "started": None, "ended": None,
                              "exit": None, "changed": 0, "error": "", "session": "", "run": 1}
            return self.send(200, {"ok": True})
        tid = int(parts[1])
        t = tasks.get(tid)
        if t is None:
            return self.send(404, {"error": "unknown task"})
        repo = os.path.join(t["dir"], "repo")
        action = parts[2] if len(parts) > 2 else ""
        if method == "PUT" and action == "source":
            if t["state"] != "created":
                return self.send(409, {"error": "already started"})
            safe_extract(self.body(MAX_SOURCE), repo)
            return self.send(200, {"ok": True})
        if method == "POST" and action == "start":
            if t["state"] != "created":
                return self.send(409, {"error": "already started"})
            t["state"], t["started"] = "running", time.time()
            save_state(t)
            threading.Thread(target=run_task, args=(tid,), daemon=True).start()
            return self.send(200, {"ok": True})
        if method == "POST" and action == "message":
            # Follow-up in the same workspace and the same OpenCode session (the agent keeps its context).
            body = json.loads(self.body(1_000_000))
            with lock:
                if any(x["state"] in ("created", "running") for x in tasks.values()):
                    return self.send(409, {"error": "busy"})
                if not t.get("session"):
                    return self.send(410, {"error": "this task has no agent session to continue"})
                t["meta"].update({k: body[k] for k in ("prompt", "proxy_token", "proxy_url", "model", "max_minutes") if k in body})
                t.update(state="running", started=time.time(), ended=None, exit=None, error="", cancel=False,
                         run=int(t.get("run") or 1) + 1)
            threading.Thread(target=run_task, args=(tid,), daemon=True).start()
            return self.send(200, {"ok": True, "run": t["run"]})
        if method == "GET" and action == "events":
            q = dict(x.split("=", 1) for x in (self.path.split("?", 1) + [""])[1].split("&") if "=" in x)
            after, out = int(q.get("after", "0") or 0), []
            try:
                with open(os.path.join(t["dir"], "events.jsonl"), "rb") as f:
                    for i, line in enumerate(f):
                        if i >= after and len(out) < 400:
                            try:
                                out.append(json.loads(line))
                            except ValueError:
                                out.append({"type": "raw", "text": "(unreadable event)"})
            except FileNotFoundError:
                pass
            return self.send(200, {"after": after, "events": out, "state": t["state"]})
        if method == "GET" and action == "shots":
            sd = os.path.join(t["dir"], "shots")
            names = sorted(n for n in (os.listdir(sd) if os.path.isdir(sd) else []) if n.endswith(".png"))
            if len(parts) == 3:
                return self.send(200, {"shots": names[:40]})
            name = parts[3]
            if name not in names or os.path.getsize(os.path.join(sd, name)) > MAX_SHOT:
                return self.send(404, {"error": "no such screenshot"})
            with open(os.path.join(sd, name), "rb") as f:
                return self.send(200, raw=f.read(), ctype="image/png")
        if method == "GET" and action == "summary":
            return self.send(200, {"files": file_summary(repo)})
        if method == "POST" and action == "cancel":
            t["cancel"] = True
            if t["state"] == "created":
                t["state"], t["ended"] = "cancelled", time.time()
            return self.send(200, {"ok": True})
        if method == "GET" and action == "":
            tail = b""
            try:
                with open(os.path.join(t["dir"], "log"), "rb") as f:
                    f.seek(0, 2)
                    f.seek(max(0, f.tell() - 40000))
                    tail = f.read()
            except FileNotFoundError:
                pass
            return self.send(200, {"state": t["state"], "exit": t["exit"], "changed": t["changed"], "error": t["error"],
                                   "started": t["started"], "ended": t["ended"], "session": t.get("session", ""),
                                   "run": t.get("run", 1), "activity": activity(t), "log": tail.decode(errors="replace")})
        if method == "GET" and action == "diff":
            if t["state"] in ("created", "running"):
                git(repo, "add", "-A", check=False)
            stat = git(repo, "diff", "--cached", "--stat", "HEAD", check=False, cap=20000)
            diff = git(repo, "diff", "--cached", "HEAD", check=False, cap=MAX_DIFF)
            return self.send(200, {"stat": stat.decode(errors="replace"), "diff": diff.decode(errors="replace")})
        if method == "GET" and action == "files":
            return self.send(200, {"files": changed_files(repo)})
        if method == "DELETE" and action == "":
            if t["state"] == "running":
                return self.send(409, {"error": "running"})
            shutil.rmtree(t["dir"], ignore_errors=True)
            tasks.pop(tid, None)
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "not found"})

    def handle_one(self, method):
        try:
            self.route(method)
            cleanup()
        except Exception as e:  # noqa: BLE001
            try:
                self.send(500, {"error": str(e)[:300]})
            except Exception:  # noqa: BLE001
                pass

    def do_GET(self):
        self.handle_one("GET")

    def do_POST(self):
        self.handle_one("POST")

    def do_PUT(self):
        self.handle_one("PUT")

    def do_DELETE(self):
        self.handle_one("DELETE")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if len(SECRET) < 24:
        raise SystemExit("AGENT_RUNNER_SECRET missing or too short")
    os.makedirs(WORK, exist_ok=True)
    load_state()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("AGENT_PORT", "7070"))), H).serve_forever()
