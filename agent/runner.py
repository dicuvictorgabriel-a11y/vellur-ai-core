#!/usr/bin/env python3
"""VELLUR agent runner — runs INSIDE the isolated agent container, never on the host.

It receives one coding task at a time from the Autopilot controller (shared secret, private Docker network), unpacks
the repository snapshot, runs a headless OpenCode session in it and reports the log and the resulting changes.
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
KEEP_TASKS = 5
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


def run_task(tid):
    t = tasks[tid]
    d, repo, home = t["dir"], os.path.join(t["dir"], "repo"), os.path.join(t["dir"], "home")
    log = open(os.path.join(d, "log"), "ab", buffering=0)
    try:
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--allow-empty", "-m", "baseline")
        cfg = os.path.join(home, "opencode.json")
        with open(cfg, "w") as f:
            json.dump(opencode_config(t["meta"]), f)
        env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
        env.update(GIT_ID)
        env.update({"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                    "XDG_DATA_HOME": os.path.join(home, ".local/share"), "XDG_CACHE_HOME": os.path.join(home, ".cache"),
                    "OPENCODE_CONFIG": cfg, "CI": "1", "TERM": "dumb", "NO_COLOR": "1"})
        cmd = [OPENCODE, "run", "--auto", "-m", f"vellur/{t['meta']['model']}", "--", t["meta"]["prompt"]]
        log.write(f"[runner] starting OpenCode with model {t['meta']['model']}\n".encode())
        p = subprocess.Popen(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
        t["proc"] = p
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
        log.close()


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
                              "exit": None, "changed": 0, "error": ""}
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
            threading.Thread(target=run_task, args=(tid,), daemon=True).start()
            return self.send(200, {"ok": True})
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
                                   "started": t["started"], "ended": t["ended"], "log": tail.decode(errors="replace")})
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
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("AGENT_PORT", "7070"))), H).serve_forever()
