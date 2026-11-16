"""Controlled NOVA self-development workflow: INSPECT -> PLAN -> EDIT -> TEST -> PREVIEW -> REVIEW.

Enforces strict stage ordering, file/path/secret/frozen-auth guardrails, automated test verification before
preview/review, staging-only preview links, and mandatory human approval with zero automatic production deployment.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import py_compile
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .db import DB

STAGES = ("INSPECT", "PLAN", "EDIT", "TEST", "PREVIEW", "REVIEW")
TERMINAL_STAGES = ("APPROVED", "REJECTED")
ALL_STAGES = STAGES + TERMINAL_STAGES

FROZEN_AUTH_SHA256 = "9c46d8a89c90452ffadf95f88aa71286bca1647b2958c49e40b102e05bd0947c"

FORBIDDEN_EXACT_PATHS = {
    ".env",
    "autopilot/.env",
    "autopilot/app/auth.py",
    "autopilot/tests/test_auth_frozen.py",
    "bin/vac-autoupdate",
    "etc/caddy/Caddyfile",
}

FORBIDDEN_PATH_PATTERNS = (
    re.compile(r"(^|/)\.env(\..+)?$"),
    re.compile(r"(^|/)secrets(/|$)"),
    re.compile(r"(^|/)\.git(/|$)"),
    re.compile(r"\.(pem|key|p12|pfx|crt)$", re.I),
    re.compile(r"(^|/)id_(rsa|ed25519|ecdsa)(\.pub)?$", re.I),
    re.compile(r"^/"),
)

ALLOWED_ENV_EXAMPLE = {"env.example", "autopilot/env.example", ".env.example"}

DASHBOARD_FORBIDDEN_SNIPPETS = (
    ("localStorage", "dashboard.html must never use localStorage"),
    ("/autopilot/login", "dashboard.html must keep in-page login without /autopilot/login redirect"),
    ("X-Vellur-Admin", "browser static files must never reference internal X-Vellur-Admin header"),
)

DEPLOY_COMMAND_RE = re.compile(
    r"\b(vac-autoupdate|systemctl\s+(restart|stop|enable|disable)|service\s+\S+\s+restart|"
    r"docker\s+compose\s+.*\b(up|down|restart)\b|git\s+push\s+--force|ssh\s+root@)\b",
    re.I,
)


class SelfDevError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, violations: Optional[list[dict]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.violations = violations or []


def normalize_rel_path(raw: str) -> str:
    cleaned = str(raw or "").strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def audit_paths_and_diff(
    paths: list[str],
    diff_text: str = "",
    commands: Optional[list[str]] = None,
    workspace_root: Optional[Path] = None,
) -> dict:
    violations: list[dict] = []
    checked_paths: list[str] = []

    for raw in paths or []:
        rel = normalize_rel_path(raw)
        if not rel:
            continue
        checked_paths.append(rel)
        norm = posixpath.normpath(rel)
        if rel.startswith("/") or norm.startswith("..") or "/../" in f"/{rel}/":
            violations.append({"kind": "path_traversal", "path": rel, "reason": "Path escapes repository root."})
            continue
        if rel not in ALLOWED_ENV_EXAMPLE:
            if rel in FORBIDDEN_EXACT_PATHS:
                violations.append({"kind": "forbidden_file", "path": rel, "reason": f"'{rel}' is frozen or host-critical."})
                continue
            for pat in FORBIDDEN_PATH_PATTERNS:
                if pat.search(rel):
                    violations.append({"kind": "secret_or_protected", "path": rel, "reason": f"'{rel}' matches protected pattern."})
                    break

        if workspace_root and rel == "autopilot/app/static/dashboard.html":
            fp = workspace_root / rel
            if fp.is_file():
                text = fp.read_text(encoding="utf-8", errors="replace")
                for needle, reason in DASHBOARD_FORBIDDEN_SNIPPETS:
                    if needle in text:
                        violations.append({"kind": "dashboard_invariant", "path": rel, "reason": reason})

    if workspace_root:
        auth_fp = workspace_root / "autopilot/app/auth.py"
        if auth_fp.is_file():
            digest = hashlib.sha256(auth_fp.read_bytes()).hexdigest()
            if digest != FROZEN_AUTH_SHA256:
                violations.append({
                    "kind": "frozen_auth_tampered",
                    "path": "autopilot/app/auth.py",
                    "reason": "Frozen authentication module hash mismatch.",
                })

    if diff_text:
        for line in diff_text.splitlines():
            if line.startswith(("+++ b/", "--- a/")):
                dpath = normalize_rel_path(line[6:])
                if dpath and dpath != "/dev/null" and dpath not in checked_paths:
                    sub = audit_paths_and_diff([dpath])
                    violations.extend(sub["violations"])
        if "autopilot/app/static/dashboard.html" in diff_text:
            added_lines = "\n".join(
                ln[1:] for ln in diff_text.splitlines() if ln.startswith("+") and not ln.startswith("+++")
            )
            for needle, reason in DASHBOARD_FORBIDDEN_SNIPPETS:
                if needle in added_lines:
                    violations.append({"kind": "dashboard_invariant", "path": "autopilot/app/static/dashboard.html", "reason": reason})

    for cmd in commands or []:
        m = DEPLOY_COMMAND_RE.search(str(cmd or ""))
        if m:
            violations.append({
                "kind": "forbidden_command",
                "command": str(cmd)[:160],
                "reason": f"Live deployment or service mutation command '{m.group(0)}' is prohibited during self-development.",
            })

    return {
        "passed": len(violations) == 0,
        "checked_paths": checked_paths,
        "violations": violations,
        "auto_deploy_allowed": False,
        "checked_at": time.time(),
    }


def inspect_workspace_files(workspace_root: Path, target_paths: list[str]) -> dict:
    audit = audit_paths_and_diff(target_paths, workspace_root=workspace_root)
    inspected: list[dict] = []
    for rel in target_paths[:25]:
        norm = normalize_rel_path(rel)
        if not norm or norm.startswith("/") or ".." in norm.split("/"):
            continue
        fp = workspace_root / norm
        if fp.is_file():
            data = fp.read_bytes()
            inspected.append({
                "path": norm,
                "exists": True,
                "bytes": len(data),
                "lines": data.count(b"\n") + (1 if data else 0),
                "sha256_12": hashlib.sha256(data).hexdigest()[:12],
            })
        else:
            inspected.append({"path": norm, "exists": False, "bytes": 0, "lines": 0})
    return {
        "workspace": str(workspace_root),
        "files": inspected,
        "guardrail_precheck": audit,
        "inspected_at": time.time(),
    }


def run_sandbox_verification(workspace_root: Path, touched_paths: list[str], run_pytest_targets: Optional[list[str]] = None) -> dict:
    checks: list[dict] = []
    all_ok = True

    audit = audit_paths_and_diff(touched_paths, workspace_root=workspace_root)
    checks.append({
        "name": "guardrail_audit",
        "passed": audit["passed"],
        "detail": "All touched files satisfy path, secret, and frozen-auth guardrails." if audit["passed"]
        else f"{len(audit['violations'])} guardrail violation(s)",
    })
    if not audit["passed"]:
        all_ok = False

    for rel in touched_paths:
        norm = normalize_rel_path(rel)
        fp = workspace_root / norm
        if not fp.is_file():
            continue
        if norm.endswith(".py"):
            try:
                py_compile.compile(str(fp), doraise=True)
                checks.append({"name": f"py_compile:{norm}", "passed": True, "detail": "Syntax OK"})
            except py_compile.PyCompileError as exc:
                all_ok = False
                checks.append({"name": f"py_compile:{norm}", "passed": False, "detail": str(exc)[:240]})
        elif norm.endswith((".js", ".mjs", ".cjs")):
            try:
                proc = subprocess.run(["node", "--check", str(fp)], capture_output=True, text=True, timeout=15)
                ok = proc.returncode == 0
                if not ok:
                    all_ok = False
                checks.append({"name": f"node_check:{norm}", "passed": ok, "detail": (proc.stderr or "Syntax OK").strip()[:240]})
            except (OSError, subprocess.SubprocessError) as exc:
                all_ok = False
                checks.append({"name": f"node_check:{norm}", "passed": False, "detail": str(exc)[:200]})

    if run_pytest_targets:
        ap_dir = workspace_root / "autopilot"
        safe_targets = [
            normalize_rel_path(t) for t in run_pytest_targets
            if re.match(r"^tests/test_[A-Za-z0-9_]+\.py$", normalize_rel_path(t))
        ]
        if safe_targets and ap_dir.is_dir():
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=line", *safe_targets],
                cwd=str(ap_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            ok = proc.returncode == 0
            if not ok:
                all_ok = False
            out_tail = "\n".join((proc.stdout or proc.stderr or "").strip().splitlines()[-4:])
            checks.append({
                "name": f"pytest:{','.join(safe_targets)}",
                "passed": ok,
                "exit_code": proc.returncode,
                "detail": out_tail[:300],
            })

    return {
        "passed": all_ok,
        "exit_code": 0 if all_ok else 1,
        "checks": checks,
        "verified_at": time.time(),
    }


class SelfDevController:
    def __init__(self, db: DB, repo_root: Optional[Path] = None):
        self.db = db
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]

    def policy_summary(self) -> dict:
        return {
            "stages": list(STAGES),
            "terminal_stages": list(TERMINAL_STAGES),
            "strict_order": True,
            "requires_automated_tests_before_preview": True,
            "requires_staging_preview_before_review": True,
            "requires_human_review": True,
            "auto_deploy_to_production": False,
            "frozen_files": sorted(FORBIDDEN_EXACT_PATHS),
            "default_staging_preview_url": os.environ.get("VELLUR_PREVIEW_URL", ""),
        }

    def _hydrate(self, row: dict) -> dict:
        def _j(k: str, default):
            try:
                return json.loads(row.get(k) or "")
            except ValueError:
                return default

        stage = row["stage"]
        idx = STAGES.index(stage) if stage in STAGES else len(STAGES)
        checklist = [
            {
                "stage": s,
                "status": (
                    "done" if (stage == "APPROVED" or i < idx)
                    else ("rejected" if stage == "REJECTED" and s == "REVIEW"
                          else ("active" if s == stage else "pending"))
                ),
            }
            for i, s in enumerate(STAGES)
        ]
        return {
            "id": row["id"],
            "title": row["title"],
            "goal": row["goal"],
            "stage": stage,
            "checklist": checklist,
            "target_paths": _j("target_paths", []),
            "inspection": _j("inspection_json", {}),
            "plan_text": row.get("plan_text") or "",
            "edits": _j("edits_json", []),
            "tests": _j("test_json", {}),
            "preview": _j("preview_json", {}),
            "review": _j("review_json", {}),
            "guardrails": _j("guardrails_json", {}),
            "agent_task_id": row.get("agent_task_id"),
            "production_deploy_blocked": True,
            "created": row["created"],
            "updated": row["updated"],
        }

    def list_runs(self, limit: int = 30) -> list[dict]:
        rows = self.db.all(
            "SELECT * FROM selfdev_runs ORDER BY updated DESC, id DESC LIMIT ?",
            (max(1, min(100, int(limit))),),
        )
        return [self._hydrate(r) for r in rows]

    def get_run(self, run_id: int) -> dict:
        row = self.db.one("SELECT * FROM selfdev_runs WHERE id=?", (int(run_id),))
        if not row:
            raise SelfDevError("not_found", f"Self-development run #{run_id} not found.", 404)
        return self._hydrate(row)

    def create_run(
        self,
        title: str,
        goal: str = "",
        target_paths: Optional[list[str]] = None,
        agent_task_id: Optional[int] = None,
    ) -> dict:
        clean_title = " ".join(str(title or "").split())[:140]
        if not clean_title:
            raise SelfDevError("invalid", "Title is required.")
        paths = [normalize_rel_path(p) for p in (target_paths or []) if normalize_rel_path(p)]
        audit = audit_paths_and_diff(paths, workspace_root=self.repo_root)
        if not audit["passed"]:
            raise SelfDevError("guardrail_blocked", "Target paths violate self-development guardrails.", 422, audit["violations"])

        now = time.time()
        cur = self.db.x(
            """INSERT INTO selfdev_runs(title, goal, stage, target_paths, guardrails_json, agent_task_id, created, updated)
               VALUES (?, ?, 'INSPECT', ?, ?, ?, ?, ?)""",
            (clean_title, str(goal or "").strip()[:2000], json.dumps(paths), json.dumps(audit), agent_task_id, now, now),
        )
        rid = int(cur.lastrowid)
        self.db.event("info", "selfdev", f"started self-dev run #{rid} in INSPECT: {clean_title[:80]}")
        return self.get_run(rid)

    def _require_stage(self, run: dict, allowed: tuple[str, ...]) -> None:
        if run["stage"] not in allowed:
            raise SelfDevError(
                "stage_order",
                f"Stage transition denied: run #{run['id']} is in {run['stage']}, expected {' or '.join(allowed)}.",
                409,
            )

    def complete_inspect(
        self,
        run_id: int,
        summary: str,
        target_paths: Optional[list[str]] = None,
        constraints: Optional[list[str]] = None,
        workspace_root: Optional[Path] = None,
    ) -> dict:
        run = self.get_run(run_id)
        self._require_stage(run, ("INSPECT",))
        paths = [normalize_rel_path(p) for p in (target_paths if target_paths is not None else run["target_paths"]) if normalize_rel_path(p)]
        if not summary.strip() and not paths:
            raise SelfDevError("invalid_inspect", "INSPECT requires findings summary or inspected file paths.")
        ws = workspace_root or self.repo_root
        info = inspect_workspace_files(ws, paths)
        if not info["guardrail_precheck"]["passed"]:
            raise SelfDevError(
                "guardrail_blocked",
                "INSPECT blocked: target paths include protected or secret files.",
                422,
                info["guardrail_precheck"]["violations"],
            )
        info["summary"] = summary.strip()[:2000]
        info["constraints"] = [str(c)[:240] for c in (constraints or [])[:20]]
        now = time.time()
        self.db.x(
            "UPDATE selfdev_runs SET stage='PLAN', target_paths=?, inspection_json=?, guardrails_json=?, updated=? WHERE id=?",
            (json.dumps(paths), json.dumps(info), json.dumps(info["guardrail_precheck"]), now, int(run_id)),
        )
        return self.get_run(run_id)

    def submit_plan(
        self,
        run_id: int,
        plan_text: str,
        planned_files: Optional[list[str]] = None,
        verification_commands: Optional[list[str]] = None,
    ) -> dict:
        run = self.get_run(run_id)
        self._require_stage(run, ("PLAN",))
        clean_plan = str(plan_text or "").strip()
        if len(clean_plan) < 12:
            raise SelfDevError("invalid_plan", "PLAN requires an explicit implementation plan before EDIT.")
        files = [normalize_rel_path(p) for p in (planned_files if planned_files is not None else run["target_paths"]) if normalize_rel_path(p)]
        audit = audit_paths_and_diff(files, commands=verification_commands, workspace_root=self.repo_root)
        if not audit["passed"]:
            raise SelfDevError("guardrail_blocked", "PLAN rejected by file/command guardrails.", 422, audit["violations"])
        now = time.time()
        self.db.x(
            "UPDATE selfdev_runs SET stage='EDIT', target_paths=?, plan_text=?, guardrails_json=?, updated=? WHERE id=?",
            (json.dumps(files), clean_plan[:4000], json.dumps(audit), now, int(run_id)),
        )
        return self.get_run(run_id)

    def record_edits(
        self,
        run_id: int,
        touched_files: list[str],
        summary: str = "",
        diff_text: str = "",
        commands: Optional[list[str]] = None,
        workspace_root: Optional[Path] = None,
    ) -> dict:
        run = self.get_run(run_id)
        self._require_stage(run, ("EDIT",))
        files = [normalize_rel_path(p) for p in (touched_files or []) if normalize_rel_path(p)]
        if not files:
            raise SelfDevError("invalid_edit", "EDIT requires at least one modified file path.")
        ws = workspace_root or self.repo_root
        audit = audit_paths_and_diff(files, diff_text=diff_text, commands=commands, workspace_root=ws)
        if not audit["passed"]:
            self.db.x(
                "UPDATE selfdev_runs SET guardrails_json=?, updated=? WHERE id=?",
                (json.dumps(audit), time.time(), int(run_id)),
            )
            self.db.event("warn", "selfdev", f"blocked unsafe edit in run #{run_id}")
            raise SelfDevError("guardrail_blocked", "EDIT blocked by NOVA self-development guardrails.", 422, audit["violations"])

        edits = [{"path": f, "summary": summary[:400], "recorded_at": time.time()} for f in files]
        now = time.time()
        self.db.x(
            "UPDATE selfdev_runs SET stage='TEST', edits_json=?, guardrails_json=?, updated=? WHERE id=?",
            (json.dumps(edits), json.dumps(audit), now, int(run_id)),
        )
        return self.get_run(run_id)

    def complete_tests(
        self,
        run_id: int,
        passed: Optional[bool] = None,
        command: str = "",
        output: str = "",
        pytest_targets: Optional[list[str]] = None,
        run_local_checks: bool = True,
        workspace_root: Optional[Path] = None,
    ) -> dict:
        run = self.get_run(run_id)
        self._require_stage(run, ("TEST",))
        ws = workspace_root or self.repo_root
        touched = [e["path"] for e in run["edits"] if isinstance(e, dict) and e.get("path")] or run["target_paths"]

        local_report = run_sandbox_verification(ws, touched, pytest_targets) if run_local_checks else {
            "passed": True, "exit_code": 0, "checks": [], "verified_at": time.time()
        }
        caller_ok = True if passed is None else bool(passed)
        overall_ok = bool(local_report["passed"] and caller_ok)
        if not run_local_checks and passed is None:
            raise SelfDevError("invalid_test", "TEST stage requires automated check execution or explicit test result.")

        test_record = {
            "passed": overall_ok,
            "command": command[:300] or ("sandbox_verification" if run_local_checks else ""),
            "output": output[:3000],
            "sandbox_checks": local_report["checks"],
            "verified_at": time.time(),
        }
        now = time.time()
        if not overall_ok:
            self.db.x(
                "UPDATE selfdev_runs SET test_json=?, updated=? WHERE id=?",
                (json.dumps(test_record), now, int(run_id)),
            )
            raise SelfDevError("tests_failed", "TEST gate failed: fixes are required before advancing to PREVIEW.", 422)

        self.db.x(
            "UPDATE selfdev_runs SET stage='PREVIEW', test_json=?, updated=? WHERE id=?",
            (json.dumps(test_record), now, int(run_id)),
        )
        return self.get_run(run_id)

    def attach_preview(self, run_id: int, preview_url: str = "", notes: str = "") -> dict:
        run = self.get_run(run_id)
        self._require_stage(run, ("PREVIEW",))
        if not run["tests"].get("passed"):
            raise SelfDevError("unverified_tests", "Cannot enter PREVIEW/REVIEW without passing TEST gate.", 409)

        url = (preview_url or os.environ.get("VELLUR_PREVIEW_URL") or "/autopilot/").strip()
        if url.startswith(("https://ai.vellurdigital.com", "http://ai.vellurdigital.com")):
            raise SelfDevError("live_url_forbidden", "Staging PREVIEW must point to isolated preview, never live production.", 422)

        preview_data = {
            "url": url[:400],
            "staging_only": True,
            "notes": notes.strip()[:1000],
            "ready_at": time.time(),
        }
        now = time.time()
        self.db.x(
            "UPDATE selfdev_runs SET stage='REVIEW', preview_json=?, updated=? WHERE id=?",
            (json.dumps(preview_data), now, int(run_id)),
        )
        return self.get_run(run_id)

    def submit_review(self, run_id: int, decision: str, reviewer_note: str = "") -> dict:
        run = self.get_run(run_id)
        self._require_stage(run, ("REVIEW",))
        dec = str(decision or "").strip().lower()
        if dec not in ("approve", "reject", "request_changes"):
            raise SelfDevError("invalid_decision", "Decision must be 'approve', 'reject', or 'request_changes'.")
        if not run["tests"].get("passed"):
            raise SelfDevError("unverified_tests", "Cannot approve a self-development run without passed tests.", 409)

        next_stage = "APPROVED" if dec == "approve" else ("EDIT" if dec == "request_changes" else "REJECTED")
        review_data = {
            "decision": dec,
            "note": reviewer_note.strip()[:1500],
            "reviewed_at": time.time(),
            "auto_deployed": False,
            "next_step": (
                "Changes remain staged locally. Publish branch/PR and approve commit in Release Control when ready."
                if dec == "approve"
                else ("Returned to EDIT stage for follow-up modifications." if dec == "request_changes" else "Run closed without applying.")
            ),
        }
        now = time.time()
        self.db.x(
            "UPDATE selfdev_runs SET stage=?, review_json=?, updated=? WHERE id=?",
            (next_stage, json.dumps(review_data), now, int(run_id)),
        )
        self.db.event("info", "selfdev", f"owner reviewed self-dev run #{run_id}: {dec} -> {next_stage}")
        return self.get_run(run_id)


class CreateRunBody(BaseModel):
    title: str = Field(..., max_length=140)
    goal: str = Field(default="", max_length=2000)
    target_paths: list[str] = Field(default_factory=list)
    agent_task_id: Optional[int] = None


class InspectBody(BaseModel):
    summary: str = Field(default="", max_length=2000)
    target_paths: Optional[list[str]] = None
    constraints: list[str] = Field(default_factory=list)


class PlanBody(BaseModel):
    plan_text: str = Field(..., max_length=4000)
    planned_files: Optional[list[str]] = None
    verification_commands: list[str] = Field(default_factory=list)


class EditBody(BaseModel):
    touched_files: list[str]
    summary: str = Field(default="", max_length=600)
    diff_text: str = Field(default="", max_length=150000)
    commands: list[str] = Field(default_factory=list)


class TestBody(BaseModel):
    passed: Optional[bool] = None
    command: str = Field(default="", max_length=300)
    output: str = Field(default="", max_length=4000)
    pytest_targets: list[str] = Field(default_factory=list)
    run_local_checks: bool = True


class PreviewBody(BaseModel):
    preview_url: str = Field(default="", max_length=400)
    notes: str = Field(default="", max_length=1000)


class ReviewBody(BaseModel):
    decision: str
    reviewer_note: str = Field(default="", max_length=1500)


def register(app: FastAPI, ctl, admin: Callable, admin_write: Callable) -> SelfDevController:
    sdev = SelfDevController(ctl.db)

    def _raise(err: SelfDevError):
        raise HTTPException(err.status, {"error": err.code, "message": err.message, "violations": err.violations})

    @app.get("/autopilot/api/selfdev/policy", dependencies=[Depends(admin)])
    async def selfdev_policy():
        return sdev.policy_summary()

    @app.get("/autopilot/api/selfdev/runs", dependencies=[Depends(admin)])
    async def selfdev_list():
        return {"runs": sdev.list_runs(), "policy": sdev.policy_summary()}

    @app.post("/autopilot/api/selfdev/runs", dependencies=[Depends(admin_write)])
    async def selfdev_create(body: CreateRunBody):
        try:
            return sdev.create_run(body.title, body.goal, body.target_paths, body.agent_task_id)
        except SelfDevError as e:
            _raise(e)

    @app.get("/autopilot/api/selfdev/runs/{run_id}", dependencies=[Depends(admin)])
    async def selfdev_get(run_id: int):
        try:
            return sdev.get_run(run_id)
        except SelfDevError as e:
            _raise(e)

    @app.post("/autopilot/api/selfdev/runs/{run_id}/inspect", dependencies=[Depends(admin_write)])
    async def selfdev_inspect(run_id: int, body: InspectBody):
        try:
            return sdev.complete_inspect(run_id, body.summary, body.target_paths, body.constraints)
        except SelfDevError as e:
            _raise(e)

    @app.post("/autopilot/api/selfdev/runs/{run_id}/plan", dependencies=[Depends(admin_write)])
    async def selfdev_plan(run_id: int, body: PlanBody):
        try:
            return sdev.submit_plan(run_id, body.plan_text, body.planned_files, body.verification_commands)
        except SelfDevError as e:
            _raise(e)

    @app.post("/autopilot/api/selfdev/runs/{run_id}/edit", dependencies=[Depends(admin_write)])
    async def selfdev_edit(run_id: int, body: EditBody):
        try:
            return sdev.record_edits(run_id, body.touched_files, body.summary, body.diff_text, body.commands)
        except SelfDevError as e:
            _raise(e)

    @app.post("/autopilot/api/selfdev/runs/{run_id}/test", dependencies=[Depends(admin_write)])
    async def selfdev_test(run_id: int, body: TestBody):
        try:
            return sdev.complete_tests(run_id, body.passed, body.command, body.output, body.pytest_targets, body.run_local_checks)
        except SelfDevError as e:
            _raise(e)

    @app.post("/autopilot/api/selfdev/runs/{run_id}/preview", dependencies=[Depends(admin_write)])
    async def selfdev_preview(run_id: int, body: PreviewBody):
        try:
            return sdev.attach_preview(run_id, body.preview_url, body.notes)
        except SelfDevError as e:
            _raise(e)

    @app.post("/autopilot/api/selfdev/runs/{run_id}/review", dependencies=[Depends(admin_write)])
    async def selfdev_review(run_id: int, body: ReviewBody):
        try:
            return sdev.submit_review(run_id, body.decision, body.reviewer_note)
        except SelfDevError as e:
            _raise(e)

    return sdev
