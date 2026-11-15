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
import contextlib
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

WORK = os.environ.get("AGENT_WORK", "/work")
SECRET = os.environ.get("AGENT_RUNNER_SECRET", "")
OPENCODE = os.environ.get("OPENCODE_BIN", "opencode")
KEEP_TASKS = 12
MAX_EVENT = 6000          # characters kept per string field of an event (tool output, file contents)
MAX_SHOT = 4 * 1024 * 1024
MAX_SOURCE = 400 * 1024 * 1024
MAX_ATTACHMENT = 100 * 1024 * 1024
MAX_DIFF = 2 * 1024 * 1024
MAX_FILE = 5 * 1024 * 1024
MAX_FILES_TOTAL = 30 * 1024 * 1024
MAX_FILES = 2000
PASS_ENV = ("PATH", "LANG", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
            "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "PIP_CERT", "PIP_INDEX_URL", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
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
    if meta.get("vision"):      # the owner said this model accepts images: the agent can open its screenshots
        model.update({"attachment": True, "modalities": {"input": ["text", "image"], "output": ["text"]}})
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"vellur": {"npm": "@ai-sdk/openai-compatible", "name": "VELLUR AI Core",
                                "options": {"baseURL": meta["proxy_url"], "apiKey": meta["proxy_token"]},
                                "models": {n: dict(model, name=n) for n in ("vellur-coder", "vellur-chat", "vellur-max")}}},
        "model": f"vellur/{meta['model']}", "small_model": f"vellur/{meta['model']}",
        "permission": {"edit": "allow", "bash": "allow", "webfetch": "allow"},
        "autoupdate": False, "share": "disabled",
    }


BASE = "vellur-base"      # tag: the state the workspace started from (or was last published as); changes are measured against it


def base(repo):
    if git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{BASE}", check=False).strip() == b"":
        git(repo, "tag", BASE, "HEAD", check=False)          # workspaces created before checkpoints existed
    return BASE


def checkpoint(repo, label, named=False):
    """Save the current files as a recoverable checkpoint (a commit inside the workspace). Returns its id.
    Automatic checkpoints are skipped when nothing changed; a checkpoint the owner names is always recorded."""
    base(repo)
    git(repo, "add", "-A", check=False)
    if named or git(repo, "status", "--porcelain", check=False).strip():
        git(repo, "commit", "-q", "--allow-empty", "-m", (label or "Checkpoint")[:120].replace("\n", " "))
    return git(repo, "rev-parse", "HEAD").decode().strip()


def checkpoints(repo):
    base(repo)
    out = []
    raw = git(repo, "log", "--format=%H%x09%ct%x09%s", f"{BASE}..HEAD", check=False).decode(errors="replace")
    for line in raw.splitlines():
        sha, ts, label = (line.split("\t", 2) + ["", ""])[:3]
        out.append({"id": sha, "ts": int(ts or 0), "label": label})
    b = git(repo, "log", "-1", "--format=%H%x09%ct", BASE, check=False).decode().strip().split("\t")
    out.append({"id": b[0], "ts": int(b[1]) if len(b) > 1 else 0, "label": "Original version (before the agent's changes)", "base": True})
    return out[:100]


def restore(repo, sha):
    """Bring the files back to a checkpoint. The current state is saved first, so a restore can itself be undone."""
    known = {c["id"]: c for c in checkpoints(repo)}
    if sha not in known:
        raise ValueError("unknown checkpoint")
    checkpoint(repo, "Before restoring an earlier version")
    git(repo, "restore", f"--source={sha}", "--staged", "--worktree", "--", ".")
    git(repo, "clean", "-fdq", check=False)
    return checkpoint(repo, "Restored: " + known[sha]["label"][:80])


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


def initialize_repo(repo):
    if not os.path.isdir(os.path.join(repo, ".git")):
        git(repo, "init", "-q", "-b", "main")
        with open(os.path.join(repo, ".git", "info", "exclude"), "a") as f:
            f.write("\n__pycache__/\n*.pyc\nnode_modules/\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\n.venv/\n"
                    ".next/\n.turbo/\n.cache/\n.DS_Store\n.vellur/attachments/\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--allow-empty", "-m", "baseline")
        git(repo, "tag", BASE)


def run_task(tid):
    t = tasks[tid]
    d, repo, home = t["dir"], os.path.join(t["dir"], "repo"), os.path.join(t["dir"], "home")
    log = open(os.path.join(d, "log"), "ab", buffering=0)
    try:
        initialize_repo(repo)
        cfg = os.path.join(home, "opencode.json")
        with open(cfg, "w") as f:
            json.dump(opencode_config(t["meta"]), f)
        shots = os.path.join(d, "shots")
        os.makedirs(shots, exist_ok=True)
        env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
        env.update(GIT_ID)
        env.update({"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                    "XDG_DATA_HOME": os.path.join(home, ".local/share"), "XDG_CACHE_HOME": os.path.join(home, ".cache"),
                    "OPENCODE_CONFIG": cfg, "CI": "1", "TERM": "dumb", "NO_COLOR": "1", "VELLUR_SHOTS": shots,
                    "VELLUR_PREVIEW_URL": f"http://127.0.0.1:{os.environ.get('AGENT_PORT', '7070')}/tasks/{tid}/preview/",
                    "VELLUR_IMAGE_URL": os.environ.get("VELLUR_IMAGE_URL",
                                                       "http://autopilot:8080/autopilot/api/agent/images"),
                    "VELLUR_RUNNER_SECRET": os.environ.get("AGENT_RUNNER_SECRET", ""),
                    "VELLUR_TASK_ID": str(tid)})
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
        with contextlib.suppress(Exception):
            checkpoint(repo, f"After run {t['run']}")          # every run ends with a recoverable checkpoint
        if t["state"] == "running" and t["meta"].get("auto_screenshots") is True:
            try:
                auto_shots(tid, t)
            except Exception as e:
                screenshot_notice(t, f"automatic screenshots unavailable: {type(e).__name__}: {e}")
        git(repo, "add", "-A", check=False)
        names = git(repo, "diff", "--cached", "--no-renames", "--name-status", "-z", base(repo), check=False).decode(errors="replace")
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
    st = git(repo, "diff", "--cached", "--no-renames", "--name-status", "-z", base(repo), check=False).decode(errors="replace")
    parts = [x for x in st.split("\0") if x]
    status = dict(zip(parts[1::2], parts[0::2]))
    nums = {}
    for line in git(repo, "diff", "--cached", "--no-renames", "--numstat", base(repo), check=False).decode(errors="replace").splitlines():
        a, r, path = (line.split("\t", 2) + ["", ""])[:3]
        nums[path] = (None if a == "-" else int(a or 0), None if r == "-" else int(r or 0))
    return [{"path": p, "status": status[p][:1], "added": nums.get(p, (None, None))[0],
             "removed": nums.get(p, (None, None))[1]} for p in sorted(status)][:500]


def changed_files(repo, tree=None):
    args = (base(repo), tree) if tree else ("--cached", base(repo))
    raw = git(repo, "diff", "--no-renames", "--name-status", "-z", *args).decode(errors="replace")
    parts = [x for x in raw.split("\0") if x]
    files, total = [], 0
    if len(parts) // 2 > MAX_FILES:
        raise RuntimeError(f"too many changed files ({len(parts) // 2}); is a dependency folder missing from .gitignore?")
    for status, path in zip(parts[0::2], parts[1::2]):
        full = os.path.join(repo, path)
        if status.startswith("D"):
            files.append({"path": path, "deleted": True})
            continue
        if tree:
            entry = git(repo, "ls-tree", "-z", tree, "--", path).split(b"\t", 1)[0].split()
            if len(entry) != 3 or entry[0] not in (b"100644", b"100755", b"120000"):
                raise RuntimeError(f"unsupported file mode: {path}")
            size = int(git(repo, "cat-file", "-s", entry[2].decode()).strip())
            total += size
            if size > MAX_FILE or total > MAX_FILES_TOTAL:
                raise RuntimeError(f"changed file too large to publish: {path}")
            content = git(repo, "cat-file", "blob", entry[2].decode())
            files.append({"path": path, "mode": entry[0].decode(), "content_b64": base64.b64encode(content).decode()})
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


def validation_snapshot(t):
    repo = os.path.join(t["dir"], "repo")
    git(repo, "add", "-A")
    tree = git(repo, "write-tree").decode().strip()
    files = changed_files(repo, tree)
    if not files:
        return {"status": "skipped", "reason": "no changes"}
    return {"status": "failed", "reason": "independent immutable validation is not configured", "tree": tree, "files": files}


# ── live preview: the owner looks at the project while the agent works ───────────────────────────
# Static sites are served straight from the workspace; apps that need a server declare it in
# `.vellur/preview.json` ({"command": "npm run dev -- --port $PORT --host 127.0.0.1", "dir": "."} or
# {"static": "dist"}). One preview server runs at a time; it is reached only through the controller.
STATIC_ROOTS = ("dist", "build", "out", "_site", "public", "site", "www", "docs", ".")
PREVIEW_PORT = int(os.environ.get("AGENT_PREVIEW_PORT", "4173"))
MAX_PREVIEW_BODY = 20 * 1024 * 1024
preview = {"tid": None, "proc": None, "key": None, "started": 0.0}
plock = threading.Lock()

# ── 3D assets: discovered in the workspace, served through the controller only ───────────────────
MODEL_FORMATS = {".glb": "glb", ".gltf": "gltf", ".obj": "obj", ".stl": "stl"}
MODEL_MIME = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json; charset=utf-8", ".obj": "text/plain; charset=utf-8",
              ".stl": "model/stl", ".bin": "application/octet-stream", ".png": "image/png", ".jpg": "image/jpeg",
              ".jpeg": "image/jpeg", ".webp": "image/webp", ".ktx2": "image/ktx2", ".blend": "application/octet-stream"}
ASSET_EXTS = frozenset(MODEL_MIME) - {".blend"}
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_GLTF_JSON = 16 * 1024 * 1024
MAX_MODELS = 120
MAX_SCAN_FILES = 20000
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             ".next", ".turbo", ".cache"}
DERIVED_PREFIX = "@derived/"
_model_cache = {}


def blender_bin():
    """Optional: Blender is never installed automatically. Set VELLUR_BLENDER or put `blender` on PATH."""
    return os.environ.get("VELLUR_BLENDER") or shutil.which("blender") or ""


def _inside(root, target):
    root, target = os.path.realpath(root), os.path.realpath(target)
    return target == root or target.startswith(root + os.sep)


def _gltf_problem(raw, repo, rel_dir):
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return f"glTF JSON is not valid ({e.__class__.__name__})"
    if not isinstance(doc, dict):
        return "glTF root must be an object"
    asset = doc.get("asset")
    if not isinstance(asset, dict):
        return "glTF asset metadata is invalid"
    version = str(asset.get("version") or "")
    if not version.startswith("2"):
        return "only glTF 2.0 is supported"
    for key in ("buffers", "images"):
        for i, item in enumerate(doc.get(key) or []):
            if not isinstance(item, dict):
                return f"{key}[{i}] is invalid"
            uri = item.get("uri")
            if not uri:
                continue
            if not isinstance(uri, str):
                return f"{key}[{i}].uri is invalid"
            low = uri.strip().lower()
            if low.startswith("data:"):
                continue
            if re.match(r"^(?:[a-z]+:)?//", low) or low.startswith(("file:", "blob:")):
                return f"{key}[{i}] uses an external URL; only files inside the project are allowed"
            clean = unquote(uri.split("#", 1)[0].split("?", 1)[0])
            if not clean or os.path.isabs(clean):
                return f"{key}[{i}] points outside the project"
            target = os.path.normpath(os.path.join(rel_dir, clean))
            if target.startswith("..") or not _inside(repo, os.path.join(repo, target)):
                return f"{key}[{i}] points outside the project"
            if not os.path.isfile(os.path.join(repo, target)):
                return f"{key}[{i}] references a missing file ({clean[:80]})"
    return ""


def _glb_problem(raw, size):
    if len(raw) < 12:
        return "GLB file is too short"
    if raw[:4] != b"glTF":
        return "not a GLB file (missing glTF header)"
    if int.from_bytes(raw[4:8], "little") != 2:
        return "only glTF 2.0 is supported"
    length = int.from_bytes(raw[8:12], "little")
    if length < 12 or length > MAX_MODEL_BYTES:
        return "GLB declared length is invalid"
    if length > size:
        return "GLB file is truncated"
    return ""


def _valid_glb(path):
    try:
        with open(path, "rb") as f:
            raw = f.read(64)
        size = os.path.getsize(path)
    except OSError:
        return False
    return size <= MAX_MODEL_BYTES and not _glb_problem(raw, size)


def _model_entry(rel, root, source):
    path = os.path.join(root, rel)
    if not _inside(root, path):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    ext = os.path.splitext(rel)[1].lower()
    fmt = MODEL_FORMATS.get(ext)
    if not fmt:
        return None
    shown = rel.replace(os.sep, "/")
    entry = {"path": shown if source == "repo" else DERIVED_PREFIX + shown, "name": os.path.basename(rel),
             "format": fmt, "size": st.st_size, "mtime": st.st_mtime, "modified": st.st_mtime, "source": source,
             "previewable": True, "reason": ""}
    if st.st_size <= 0:
        entry.update(previewable=False, reason="file is empty")
    elif st.st_size > MAX_MODEL_BYTES:
        entry.update(previewable=False, reason=f"file is larger than {MAX_MODEL_BYTES // (1024 * 1024)} MB")
    else:
        try:
            if fmt == "glb":
                with open(path, "rb") as f:
                    problem = _glb_problem(f.read(64), st.st_size)
            elif fmt == "gltf":
                if st.st_size > MAX_GLTF_JSON:
                    problem = "glTF JSON is too large"
                else:
                    with open(path, "rb") as f:
                        problem = _gltf_problem(f.read(), root, os.path.dirname(rel))
            else:
                problem = ""
        except OSError as e:
            problem = f"file could not be read ({e.__class__.__name__})"
        if problem:
            entry.update(previewable=False, reason=problem)
    return entry


def model_files(t, refresh=False):
    """3D files in the workspace (and Blender exports). Results are cached for two seconds; scans are bounded."""
    key = t.get("dir")
    now = time.time()
    if not refresh and key in _model_cache and now - _model_cache[key][0] < 2:
        return _model_cache[key][1]
    repo = os.path.realpath(os.path.join(t["dir"], "repo"))
    out, seen = [], 0
    if os.path.isdir(repo):
        for base, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                seen += 1
                if seen > MAX_SCAN_FILES or len(out) >= MAX_MODELS:
                    break
                ext = os.path.splitext(name)[1].lower()
                if ext not in MODEL_FORMATS and ext != ".blend":
                    continue
                rel = os.path.relpath(os.path.join(base, name), repo)
                if ext == ".blend":
                    full = os.path.join(base, name)
                    if not _inside(repo, full):
                        continue
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    blender = blender_bin()
                    out.append({"path": rel.replace(os.sep, "/"), "name": name, "format": "blend", "size": st.st_size,
                                "mtime": st.st_mtime, "modified": st.st_mtime, "source": "repo", "previewable": False,
                                "needsExport": True, "blender": bool(blender),
                                "reason": "" if blender else "Blender is not installed in the agent environment"})
                else:
                    entry = _model_entry(rel, repo, "repo")
                    if entry:
                        out.append(entry)
            if seen > MAX_SCAN_FILES or len(out) >= MAX_MODELS:
                break
    derived = os.path.join(t["dir"], "derived")
    if os.path.isdir(derived):
        for name in sorted(os.listdir(derived)):
            if len(out) >= MAX_MODELS:
                break
            if not name.endswith(".glb"):
                continue
            entry = _model_entry(name, derived, "derived")
            if not entry:
                continue
            try:
                with open(os.path.join(derived, name + ".json")) as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    entry["sourcePath"] = str(meta.get("source") or "")
                    entry["name"] = os.path.basename(entry["sourcePath"]) or entry["name"]
            except (OSError, ValueError):
                pass
            out.append(entry)
    out.sort(key=lambda m: (m.get("previewable") is False, -m.get("modified", 0), m["name"].lower()))
    _model_cache[key] = (now, out)
    return out


def preview_plan(t):
    """How this task's project can be shown: {"mode": "static", "root"} | {"mode": "command", ...} | {"mode": "none", "why"}."""
    repo = os.path.join(t["dir"], "repo")
    cfg_path = os.path.join(repo, ".vellur", "preview.json")
    if os.path.isfile(cfg_path):
        try:
            cfg = json.load(open(cfg_path))
        except (OSError, ValueError) as e:
            return {"mode": "none", "why": f".vellur/preview.json is not valid JSON ({e})"}
        sub = os.path.normpath(str(cfg.get("dir") or cfg.get("static") or "."))
        if sub.startswith("..") or os.path.isabs(sub):
            return {"mode": "none", "why": ".vellur/preview.json points outside the project"}
        if isinstance(cfg.get("command"), str) and cfg["command"].strip():
            return {"mode": "command", "command": cfg["command"].strip()[:1000], "dir": sub, "key": f"{t['dir']}|{cfg['command']}|{sub}"}
        if cfg.get("static"):
            return {"mode": "static", "root": sub}
    for sub in STATIC_ROOTS:
        if os.path.isfile(os.path.join(repo, sub, "index.html")):
            return {"mode": "static", "root": sub}
    models = [m for m in model_files(t) if m.get("previewable")]
    if models:
        return {"mode": "model", "root": ".", "why": "3D models only (no web page)", "models": models[:20]}
    return {"mode": "none", "why": "no index.html was found and the project has no .vellur/preview.json yet"}


PREVIEW_PID = os.path.join(WORK, ".preview.pid")


def kill_group(pid):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(1)


def _in_work(pid):
    """True when the process works inside the agent workspace (so it is ours, not an unrelated/reused pid)."""
    try:
        cwd = os.path.realpath(f"/proc/{int(pid)}/cwd")
    except (OSError, ValueError):
        return False
    root = os.path.realpath(WORK)
    return cwd == root or cwd.startswith(root + os.sep)


def preview_listeners(port):
    """Pids in this container that LISTEN on 127.0.0.1:port (found through /proc, without extra tools)."""
    inodes = set()
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(fn) as f:
                next(f, None)
                for line in f:
                    c = line.split()
                    if len(c) < 10 or c[3] != "0A":
                        continue
                    try:
                        if int(c[1].rsplit(":", 1)[1], 16) == port:
                            inodes.add(c[9])
                    except (IndexError, ValueError):
                        continue
        except OSError:
            continue
    pids = set()
    if not inodes:
        return pids
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            for fd in os.listdir(f"/proc/{name}/fd"):
                try:
                    link = os.readlink(f"/proc/{name}/fd/{fd}")
                except OSError:
                    continue
                if link.startswith("socket:[") and link[8:-1] in inodes:
                    pids.add(int(name))
                    break
        except OSError:
            continue
    return pids


def stop_port_listeners(port):
    """Kill listeners of the preview port that belong to our workspace (never an unrelated or reused pid)."""
    for pid in preview_listeners(port):
        if pid == os.getpid() or not _in_work(pid):
            continue
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except OSError:
                break
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                break


def wait_port_closed(port, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if not port_open(port):
            return True
        time.sleep(0.1)
    return not port_open(port)


def stop_preview():
    """Stop the preview server, including one left behind by an earlier runner process (its id is kept on disk)."""
    p = preview["proc"]
    if p and p.poll() is None:
        kill_group(p.pid)
    try:
        old = int(open(PREVIEW_PID).read().strip())
        if (not p or old != p.pid) and _in_work(old):
            kill_group(old)
        os.remove(PREVIEW_PID)
    except (OSError, ValueError):
        pass
    preview.update(tid=None, proc=None, key=None)
    stop_port_listeners(PREVIEW_PORT)


def port_open(port):
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_preview(tid, t, plan, wait=45):
    """Start (or keep) the preview server of a command-mode project. Returns "" or the reason it is not running."""
    with plock:
        p = preview["proc"]
        if not (preview["key"] == plan["key"] and p and p.poll() is None):
            stop_preview()
            repo, home = os.path.join(t["dir"], "repo"), os.path.join(t["dir"], "home")
            env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
            env.update({"HOME": home, "PORT": str(PREVIEW_PORT), "VELLUR_PREVIEW_PORT": str(PREVIEW_PORT), "HOST": "127.0.0.1",
                        "CI": "1", "NO_COLOR": "1", "BROWSER": "none", "NODE_ENV": "development"})
            log = open(os.path.join(t["dir"], "preview.log"), "ab", buffering=0)
            log.write(f"\n[preview] starting: {plan['command']}\n".encode())
            if port_open(PREVIEW_PORT):
                stop_port_listeners(PREVIEW_PORT)          # a preview left by an earlier runner, no pid file
                wait_port_closed(PREVIEW_PORT)
            if port_open(PREVIEW_PORT):
                return (f"port {PREVIEW_PORT} is used by a process outside the agent workspace; "
                        "restart the agent workspace (sudo vac agent disable && sudo vac agent enable)")
            try:
                proc = subprocess.Popen(["bash", "-lc", plan["command"]], cwd=os.path.join(repo, plan["dir"]), env=env,
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as e:
                return f"the preview command could not start: {e}"
            preview.update(tid=tid, proc=proc, key=plan["key"], started=time.time())
            with contextlib.suppress(OSError):
                with open(PREVIEW_PID, "w") as f:
                    f.write(str(proc.pid))
    end = time.time() + wait
    while time.time() < end:
        if preview["proc"] is not None and preview["proc"].poll() is None and port_open(PREVIEW_PORT):
            return ""
        if preview["proc"] is None or preview["proc"].poll() is not None:
            return "the preview command exited; see preview.log: " + preview_log(t)[-400:]
        time.sleep(0.4)
    return f"the preview server did not open port {PREVIEW_PORT} within {wait}s; the command must listen on $PORT"


def preview_log(t):
    try:
        with open(os.path.join(t["dir"], "preview.log"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4000))
            return f.read().decode(errors="replace")
    except OSError:
        return ""


ROOT_ATTR = re.compile(rb"""(\s(?:src|href|action|poster|data-src)\s*=\s*["'])/(?!/)""", re.I)
# Scripts in a previewed page often call root-absolute URLs ("/api/items"). This shim keeps them inside the preview.
PREVIEW_SHIM = (b"<script>(()=>{const B=%s;const f=u=>typeof u==='string'&&u[0]==='/'&&u[1]!=='/'&&!u.startsWith(B)?B+u.slice(1):u;"
                b"const F=window.fetch;window.fetch=(u,o)=>F(u instanceof Request?u:f(u),o);"
                b"const X=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u,...r){return X.call(this,m,f(u),...r)};"
                b"const E=window.EventSource;if(E){window.EventSource=function(u,c){return new E(f(u),c)};window.EventSource.prototype=E.prototype}"
                b"})()</script>")


def in_preview(html, tid):
    """Keep a previewed page inside its preview: root-absolute links and script requests get the preview prefix.
    (The controller swaps this prefix for the owner's link.)"""
    prefix = f"/tasks/{tid}/preview/".encode()
    html = ROOT_ATTR.sub(lambda m: m.group(1) + prefix, html)
    shim = PREVIEW_SHIM % json.dumps(prefix.decode()).encode()
    head = re.search(rb"<head[^>]*>", html, re.I)
    return html[:head.end()] + shim + html[head.end():] if head else shim + html


def serve_static(handler, tid, t, plan, rel):
    import mimetypes
    root = os.path.realpath(os.path.join(t["dir"], "repo", plan["root"]))
    rel = rel.split("?", 1)[0].split("#", 1)[0]
    from urllib.parse import unquote
    target = os.path.realpath(os.path.join(root, unquote(rel).lstrip("/")))
    if not (target == root or target.startswith(root + os.sep)) or "/.git/" in target + "/":
        return handler.send(404, {"error": "not found"})
    if os.path.isdir(target):
        target = os.path.join(target, "index.html")
    if not os.path.isfile(target):
        if "." in os.path.basename(rel):                       # a missing asset stays a 404
            return handler.send(404, {"error": "not found"})
        target = os.path.join(root, "index.html")             # single-page apps route in the browser
        if not os.path.isfile(target):
            return handler.send(404, {"error": "not found"})
    if os.path.getsize(target) > MAX_PREVIEW_BODY:
        return handler.send(413, {"error": "file too large for the preview"})
    ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
        ctype += "; charset=utf-8"
    with open(target, "rb") as f:
        data = f.read()
    return handler.send(200, raw=in_preview(data, tid) if ctype.startswith("text/html") else data, ctype=ctype)


def serve_asset(handler, t, rel):
    """Serve one 3D asset (and its relative textures/buffers) from the workspace or the derived Blender output."""
    rel = unquote(rel.split("?", 1)[0].split("#", 1)[0]).lstrip("/")
    if not rel:
        return handler.send(404, {"error": "not found"})
    if rel.startswith(DERIVED_PREFIX):
        root, sub = os.path.join(t["dir"], "derived"), rel[len(DERIVED_PREFIX):]
    else:
        root, sub = os.path.join(t["dir"], "repo"), rel
    target = os.path.realpath(os.path.join(root, sub))
    if not _inside(root, target) or "/.git/" in target + "/":
        return handler.send(404, {"error": "not found"})
    if not os.path.isfile(target):
        return handler.send(404, {"error": "not found"})
    try:
        size = os.path.getsize(target)
    except OSError:
        return handler.send(404, {"error": "not found"})
    if size > MAX_MODEL_BYTES:
        return handler.send(413, {"error": f"file is larger than {MAX_MODEL_BYTES // (1024 * 1024)} MB"})
    ext = os.path.splitext(target)[1].lower()
    if ext not in ASSET_EXTS:
        return handler.send(404, {"error": "not found"})
    ctype = MODEL_MIME[ext]
    try:
        with open(target, "rb") as f:
            data = f.read(MAX_MODEL_BYTES + 1)
    except OSError:
        return handler.send(404, {"error": "not found"})
    if len(data) > MAX_MODEL_BYTES:
        return handler.send(413, {"error": f"file is larger than {MAX_MODEL_BYTES // (1024 * 1024)} MB"})
    return handler.send(200, raw=data, ctype=ctype)


def export_blend(t, rel):
    """Export a .blend scene to GLB with a headless Blender, if one is installed. Never installed automatically."""
    blender = blender_bin()
    if not blender:
        raise RuntimeError("Blender is not installed in the agent environment")
    rel = unquote(str(rel or "")).strip().lstrip("/")
    repo = os.path.realpath(os.path.join(t["dir"], "repo"))
    src = os.path.realpath(os.path.join(repo, rel))
    if not _inside(repo, src) or os.path.splitext(src)[1].lower() != ".blend" or not os.path.isfile(src):
        raise ValueError("unknown Blender file")
    st = os.stat(src)
    if st.st_size > 512 * 1024 * 1024:
        raise ValueError("Blender file is too large")
    key = hashlib.sha1(f"{rel}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:12]
    outdir = os.path.join(t["dir"], "derived")
    os.makedirs(outdir, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.splitext(os.path.basename(rel))[0])[:40] or "scene"
    out = os.path.join(outdir, f"{stem}-{key}.glb")
    if os.path.isfile(out) and not _valid_glb(out):
        with contextlib.suppress(OSError):
            os.remove(out)
        with contextlib.suppress(OSError):
            os.remove(out + ".json")
    if not os.path.isfile(out):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_export.py")
        tmp = os.path.join(t["dir"], "tmp")
        os.makedirs(tmp, exist_ok=True)
        env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
        env.update({"HOME": os.path.join(t["dir"], "home"), "TMPDIR": tmp})
        cmd = [blender, "--background", "--factory-startup", src, "--python", script, "--", out]
        r = subprocess.run(cmd, cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=180)
        if r.returncode != 0 or not os.path.isfile(out):
            with contextlib.suppress(OSError):
                os.remove(out)
            tail = (r.stdout or b"")[-1200:].decode(errors="replace")
            raise RuntimeError(f"Blender export failed (exit {r.returncode}): {tail}")
        if os.path.getsize(out) > MAX_MODEL_BYTES:
            os.remove(out)
            raise RuntimeError("the exported model is larger than the preview limit")
        if not _valid_glb(out):
            os.remove(out)
            raise RuntimeError("Blender wrote an invalid GLB file")
        with open(out + ".json", "w") as f:
            json.dump({"source": rel, "label": os.path.basename(rel), "mtime": st.st_mtime, "size": st.st_size}, f)
    _model_cache.pop(t.get("dir"), None)
    entry = _model_entry(os.path.basename(out), outdir, "derived")
    if not entry:
        raise RuntimeError("Blender output could not be read")
    entry["sourcePath"] = rel
    entry["name"] = os.path.basename(rel)
    return entry


def proxy_app(handler, tid, method, rel, body):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", PREVIEW_PORT, timeout=60)
    try:
        headers = {k: v for k, v in handler.headers.items()
                   if k.lower() in ("accept", "content-type", "accept-language", "user-agent", "range", "x-vellur-request")}
        headers["Host"] = f"127.0.0.1:{PREVIEW_PORT}"
        conn.request(method, "/" + rel.lstrip("/"), body=body or None, headers=headers)
        r = conn.getresponse()
        data = r.read(MAX_PREVIEW_BODY + 1)
        if len(data) > MAX_PREVIEW_BODY:
            return handler.send(413, {"error": "response too large for the preview"})
        ctype = r.getheader("content-type") or "application/octet-stream"
        if ctype.startswith("text/html"):
            data = in_preview(data, tid)
        handler.send_response(r.status)
        handler.send_header("content-type", ctype)
        loc = r.getheader("location")
        if loc:      # a redirect to "/somewhere" must stay inside the preview
            handler.send_header("location", f"/tasks/{tid}/preview{loc}" if loc.startswith("/") and not loc.startswith("//") else loc)
        handler.send_header("content-length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    except OSError as e:
        handler.send(502, {"error": f"the preview server did not answer ({e.__class__.__name__})"})
    finally:
        conn.close()


def preview_info(tid, t):
    plan = preview_plan(t)
    info = {"mode": plan["mode"], "why": plan.get("why", ""), "root": plan.get("root", ""), "command": plan.get("command", ""),
            "running": False, "log": "", "models": [], "model": "", "blender": bool(blender_bin())}
    if plan["mode"] == "command":
        p = preview["proc"]
        info["running"] = bool(preview["key"] == plan["key"] and p and p.poll() is None and port_open(PREVIEW_PORT))
        info["log"] = preview_log(t)[-1500:]
    models = model_files(t)
    info["models"] = models[:60]
    info["model"] = next((m["path"] for m in models if m.get("previewable")), "")
    return info


def screenshot_notice(t, message):
    text = f"[runner] screenshot notice: {message}"[:MAX_EVENT]
    event = {"type": "raw", "category": "screenshot_notice", "text": text, "run": t["run"]}
    with contextlib.suppress(OSError):
        with open(os.path.join(t["dir"], "events.jsonl"), "ab") as out:
            out.write((json.dumps(event) + "\n").encode())
    with contextlib.suppress(OSError):
        with open(os.path.join(t["dir"], "log"), "ab") as out:
            out.write((text + "\n").encode())


def auto_shots(tid, t):
    plan = preview_plan(t)
    if plan["mode"] == "none":
        screenshot_notice(t, f"preview unavailable: {plan.get('why', 'no preview')}")
        return
    if plan["mode"] == "model":
        screenshot_notice(t, "3D model preview has no web page; open it in VELLUR 3D Studio")
        return
    if plan["mode"] == "command":
        why = ensure_preview(tid, t, plan, wait=40)
        if why:
            screenshot_notice(t, f"preview unavailable: {why}")
            return
    shots = os.path.join(t["dir"], "shots")
    os.makedirs(shots, exist_ok=True)
    port = int(os.environ.get("AGENT_PORT", "7070"))
    for size in ("desktop", "mobile"):
        try:
            result = subprocess.run(["vellur-screenshot", f"http://127.0.0.1:{port}/tasks/{tid}/preview/", f"auto-run{t['run']}-{size}", size],
                                    env=dict(os.environ, VELLUR_SHOTS=shots), capture_output=True, timeout=120)
            if result.returncode:
                detail = (result.stderr or result.stdout or b"").decode(errors="replace").strip()[-1000:]
                screenshot_notice(t, f"{size} screenshot failed (exit {result.returncode}): {detail}")
        except Exception as e:
            screenshot_notice(t, f"{size} screenshot failed: {type(e).__name__}: {e}")


def cleanup():
    done = sorted((t["ended"] or 0, tid) for tid, t in tasks.items() if t["state"] not in ("created", "running"))
    for _, tid in done[:-KEEP_TASKS]:
        if preview["tid"] == tid:
            stop_preview()
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
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        is_preview = len(parts) >= 3 and parts[0] == "tasks" and parts[2] == "preview"
        is_asset = len(parts) >= 4 and parts[0] == "tasks" and parts[2] == "asset"
        # Inside the container (screenshots, the agent's own checks) the preview needs no secret; everything else does.
        local_preview = is_preview and self.client_address[0] in ("127.0.0.1", "::1")
        if not local_preview and (not SECRET or self.headers.get("x-runner-secret") != SECRET):
            return self.send(401, {"error": "unauthorized"})
        if is_asset:
            t = tasks.get(int(parts[1])) if parts[1].isdigit() else None
            if t is None:
                return self.send(404, {"error": "unknown task"})
            return serve_asset(self, t, self.path.split("/asset", 1)[1])
        if is_preview:
            t = tasks.get(int(parts[1])) if parts[1].isdigit() else None
            if t is None:
                return self.send(404, {"error": "unknown task"})
            rel = self.path.split("/preview", 1)[1]
            plan = preview_plan(t)
            if plan["mode"] == "static":
                return serve_static(self, int(parts[1]), t, plan, rel)
            if plan["mode"] == "command":
                why = ensure_preview(int(parts[1]), t, plan)
                if why:
                    return self.send(503, {"error": why})
                return proxy_app(self, int(parts[1]), method, rel, self.body(MAX_PREVIEW_BODY) if method in ("POST", "PUT", "PATCH") else None)
            if plan["mode"] == "model":
                return self.send(404, {"error": "this project has 3D models but no web page; open VELLUR 3D Studio"})
            return self.send(404, {"error": plan["why"]})
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
            initialize_repo(repo)
            return self.send(200, {"ok": True})
        if method == "PUT" and action == "attachments":
            if t["state"] == "running":
                return self.send(409, {"error": "cannot add attachments while the workspace is running"})
            if len(parts) < 4:
                return self.send(400, {"error": "missing file name"})
            name = os.path.basename(unquote(parts[3])).strip().replace("\x00", "")
            if not name or name in (".", "..") or len(name) > 180:
                return self.send(400, {"error": "invalid file name"})
            data = self.body(MAX_ATTACHMENT)
            if not data:
                return self.send(400, {"error": "empty file"})
            folder = os.path.join(repo, ".vellur", "attachments")
            os.makedirs(folder, mode=0o700, exist_ok=True)
            target = os.path.join(folder, name)
            if not os.path.realpath(target).startswith(os.path.realpath(folder) + os.sep):
                return self.send(400, {"error": "invalid file name"})
            with open(target + ".tmp", "wb") as f:
                f.write(data)
            os.replace(target + ".tmp", target)
            os.chmod(target, 0o600)
            return self.send(200, {"ok": True, "path": f".vellur/attachments/{name}", "size": len(data)})
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
                t["meta"].update({k: body[k] for k in ("prompt", "proxy_token", "proxy_url", "model", "max_minutes", "vision") if k in body})
                t["meta"]["auto_screenshots"] = body.get("auto_screenshots") is True
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
                return self.send(200, {"shots": names[:80]})
            name = parts[3]
            if name not in names or os.path.getsize(os.path.join(sd, name)) > MAX_SHOT:
                return self.send(404, {"error": "no such screenshot"})
            with open(os.path.join(sd, name), "rb") as f:
                return self.send(200, raw=f.read(), ctype="image/png")
        if method == "GET" and action == "checkpoints":
            return self.send(200, {"checkpoints": checkpoints(repo)})
        if method == "POST" and action in ("checkpoints", "restore"):
            if t["state"] in ("created", "running"):
                return self.send(409, {"error": "running"})
            body = json.loads(self.body(100_000) or b"{}")
            try:
                sha = checkpoint(repo, str(body.get("label") or "Checkpoint"), named=True) if action == "checkpoints" else restore(repo, str(body.get("id", "")))
            except ValueError as e:
                return self.send(404, {"error": str(e)})
            t["changed"] = len(file_summary(repo))
            save_state(t)
            return self.send(200, {"id": sha, "changed": t["changed"], "checkpoints": checkpoints(repo)})
        if method == "GET" and action == "preview-info":
            return self.send(200, preview_info(tid, t))
        if method == "GET" and action == "models":
            return self.send(200, {"models": model_files(t, refresh=True), "blender": bool(blender_bin()),
                                   "limits": {"bytes": MAX_MODEL_BYTES, "count": MAX_MODELS}})
        if method == "POST" and action == "blend-export":
            body = json.loads(self.body(100_000) or b"{}")
            try:
                entry = export_blend(t, str(body.get("path") or ""))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
            except subprocess.TimeoutExpired:
                return self.send(504, {"error": "Blender export timed out"})
            except (RuntimeError, OSError) as e:
                return self.send(503, {"error": str(e)[:600]})
            return self.send(200, {"model": entry})
        if method == "POST" and action == "validate":
            if t["state"] in ("created", "running"):
                return self.send(409, {"error": "running"})
            return self.send(200, validation_snapshot(t))
        if method == "POST" and action == "rebaseline":
            # the owner published the changes: they become the new baseline, so later reports show only new work
            if t["state"] in ("created", "running"):
                return self.send(409, {"error": "running"})
            git(repo, "add", "-A", check=False)
            git(repo, "commit", "-q", "--allow-empty", "-m", "Published", check=False)
            git(repo, "tag", "-f", BASE, "HEAD", check=False)
            t["changed"] = 0
            save_state(t)
            return self.send(200, {"ok": True})
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
            stat = git(repo, "diff", "--cached", "--stat", base(repo), check=False, cap=20000)
            diff = git(repo, "diff", "--cached", base(repo), check=False, cap=MAX_DIFF)
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

    def do_PATCH(self):
        self.handle_one("PATCH")

    def do_HEAD(self):
        self.handle_one("GET")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if len(SECRET) < 24:
        raise SystemExit("AGENT_RUNNER_SECRET missing or too short")
    os.makedirs(WORK, exist_ok=True)
    load_state()
    stop_preview()        # a preview server left by a previous runner process would show stale files
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("AGENT_PORT", "7070"))), H).serve_forever()
