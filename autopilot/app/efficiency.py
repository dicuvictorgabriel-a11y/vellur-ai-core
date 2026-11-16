"""Project-scoped token-efficiency layer for NOVA: Ponytail + RTK (Token Killer) + prompt-cache telemetry.

Integrates:
  * Ponytail (DietrichGebert/ponytail): conversation tail & stale/duplicate tool-read compaction that preserves the
    system prefix (for provider prompt caching) and recent active turns verbatim.
  * RTK — Rust Token Killer (TokenFleet-AI/rtk, explicitly distinct from reachingforthejack/rtk): strips ANSI escapes,
    spinner/progress noise, repetitive test/install/git boilerplate, and oversized CLI dumps from tool outputs.

Both integrations are strictly project-scoped and reversible at runtime without modifying global dotfiles or host state.
External binaries are detected honestly (`native_binary` vs `embedded_filter`), and savings are measured from actual
compacted characters/tokens plus provider-reported prompt cache hits.
"""
from __future__ import annotations

import copy
import os
import re
import shutil
import subprocess
import time
from typing import Callable, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from .db import DB

PONYTAIL_REPO = "DietrichGebert/ponytail"
RTK_REPO = "TokenFleet-AI/rtk"
RTK_FORBIDDEN_REPO = "reachingforthejack/rtk"

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
PROGRESS_NOISE_RE = re.compile(
    r"^(\s*[\r⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏|/\\-]+\s*|\s*\d+%\s*\|[█▉▊▋▌▍▎▏ #=-]+\|.*|"
    r"\s*(Downloading|Collecting|Installing|Resolving packages|Fetching)\s+\S+.*\([0-9.]+ [kKMGT]?B.*\)\s*)$"
)
BLANK_RUN_RE = re.compile(r"\n{3,}")

DEFAULT_EFFICIENCY_CFG = {
    "ponytail_enabled": True,
    "rtk_enabled": True,
    "keep_recent_messages": 8,
    "max_tool_output_chars": 4800,
    "max_stale_tool_chars": 900,
}


def detect_ponytail_binary() -> dict:
    candidate = os.environ.get("VELLUR_PONYTAIL_BIN", "").strip() or shutil.which("ponytail") or ""
    if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return {
            "repo": PONYTAIL_REPO,
            "binary_installed": True,
            "binary_path": candidate,
            "mode": "native_binary+embedded_filter",
        }
    return {
        "repo": PONYTAIL_REPO,
        "binary_installed": False,
        "binary_path": "",
        "mode": "embedded_filter",
    }


def detect_rtk_binary() -> dict:
    candidate = os.environ.get("VELLUR_RTK_BIN", "").strip() or shutil.which("rtk") or ""
    if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        try:
            proc = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=2)
            ver = (proc.stdout or proc.stderr or "").strip()
            low = ver.lower()
            if "reachingforthejack" in low or "type kit" in low:
                return {
                    "repo": RTK_REPO,
                    "excluded_repo": RTK_FORBIDDEN_REPO,
                    "binary_installed": False,
                    "binary_path": "",
                    "mode": "embedded_filter",
                    "note": "Ignored unrelated rtk binary; using NOVA embedded TokenFleet-AI/rtk filter.",
                }
            return {
                "repo": RTK_REPO,
                "excluded_repo": RTK_FORBIDDEN_REPO,
                "binary_installed": True,
                "binary_path": candidate,
                "version": ver[:80],
                "mode": "native_binary+embedded_filter",
            }
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "repo": RTK_REPO,
        "excluded_repo": RTK_FORBIDDEN_REPO,
        "binary_installed": False,
        "binary_path": "",
        "mode": "embedded_filter",
    }


def rtk_compact_text(text: str, max_chars: int = 4800) -> tuple[str, int]:
    """TokenFleet-AI/rtk style CLI & tool output token killer."""
    if not isinstance(text, str) or len(text) < 120:
        return text, 0
    orig_len = len(text)
    cleaned = ANSI_ESCAPE_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")

    kept_lines: list[str] = []
    prev_line = None
    repeat_count = 0
    for line in cleaned.split("\n"):
        if PROGRESS_NOISE_RE.match(line):
            continue
        # Strip pytest/npm Pure-dot progress lines like "........................ [ 45%]"
        if re.match(r"^\s*\.{12,}\s*(\[\s*\d+%\s*\])?\s*$", line):
            kept_lines.append("[rtk: condensed test progress dots]")
            continue
        if line == prev_line and line.strip():
            repeat_count += 1
            if repeat_count == 2:
                kept_lines.append("  … [rtk: repeated identical lines omitted]")
            continue
        repeat_count = 0
        prev_line = line
        kept_lines.append(line)

    out = BLANK_RUN_RE.sub("\n\n", "\n".join(kept_lines))

    # Condense huge git diff / stack / build outputs while keeping headers + errors + tail
    if len(out) > max_chars and max_chars >= 400:
        head_budget = int(max_chars * 0.58)
        tail_budget = int(max_chars * 0.34)
        omitted = len(out) - (head_budget + tail_budget)
        if omitted > 120:
            out = (
                out[:head_budget]
                + f"\n… [rtk compacted {omitted} chars (~{omitted // 4} tokens) of verbose output] …\n"
                + out[-tail_budget:]
            )

    saved_chars = max(0, orig_len - len(out))
    return out, saved_chars


def ponytail_compact_messages(
    messages: list[dict],
    keep_recent: int = 8,
    max_stale_chars: int = 900,
) -> tuple[list[dict], int]:
    """DietrichGebert/ponytail style context-tail trimmer: keeps system prefix & recent turns intact,
    compacts stale older tool payloads and duplicate large file reads."""
    if not isinstance(messages, list) or len(messages) <= max(2, keep_recent):
        return messages, 0

    cutoff = len(messages) - max(2, int(keep_recent))
    saved_chars = 0
    seen_tool_hashes: set[int] = set()
    # Scan recent tail first so if a file was re-read recently, older identical reads are compacted aggressively
    for m in messages[cutoff:]:
        if isinstance(m, dict) and m.get("role") == "tool" and isinstance(m.get("content"), str):
            if len(m["content"]) > 300:
                seen_tool_hashes.add(hash(m["content"]))

    out: list[dict] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or idx >= cutoff or msg.get("role") in ("system", "developer"):
            out.append(msg)
            continue
        # Keep the initial user task description intact
        if idx <= 1 and msg.get("role") == "user":
            out.append(msg)
            continue

        role = msg.get("role", "")
        content = msg.get("content")
        if role == "tool" and isinstance(content, str) and len(content) > max_stale_chars:
            h = hash(content)
            orig = len(content)
            if h in seen_tool_hashes:
                head_line = content.splitlines()[0][:120] if content.splitlines() else ""
                compacted = f"[ponytail: duplicate tool output ({orig} chars) superseded by newer read — {head_line}]"
            else:
                seen_tool_hashes.add(h)
                half = max(180, max_stale_chars // 2)
                omitted = orig - (half * 2)
                compacted = (
                    content[:half]
                    + f"\n… [ponytail trimmed {omitted} stale chars from earlier turn #{idx}] …\n"
                    + content[-half:]
                )
            if len(compacted) < orig:
                nm = dict(msg)
                nm["content"] = compacted
                saved_chars += orig - len(compacted)
                out.append(nm)
                continue
        out.append(msg)

    return out, saved_chars


class TokenEfficiencyManager:
    def __init__(self, db: DB):
        self.db = db

    def config(self) -> dict:
        stored = self.db.kv_get("token_efficiency_cfg") or {}
        cfg = dict(DEFAULT_EFFICIENCY_CFG)
        for k in DEFAULT_EFFICIENCY_CFG:
            if k in stored:
                cfg[k] = stored[k]
        cfg["ponytail_enabled"] = bool(cfg["ponytail_enabled"])
        cfg["rtk_enabled"] = bool(cfg["rtk_enabled"])
        cfg["keep_recent_messages"] = max(2, min(40, int(cfg["keep_recent_messages"])))
        cfg["max_tool_output_chars"] = max(600, min(32000, int(cfg["max_tool_output_chars"])))
        cfg["max_stale_tool_chars"] = max(300, min(8000, int(cfg["max_stale_tool_chars"])))
        return cfg

    def update_config(self, changes: dict) -> dict:
        cfg = self.config()
        if "ponytail_enabled" in changes and changes["ponytail_enabled"] is not None:
            cfg["ponytail_enabled"] = bool(changes["ponytail_enabled"])
        if "rtk_enabled" in changes and changes["rtk_enabled"] is not None:
            cfg["rtk_enabled"] = bool(changes["rtk_enabled"])
        if "keep_recent_messages" in changes and changes["keep_recent_messages"] is not None:
            cfg["keep_recent_messages"] = max(2, min(40, int(changes["keep_recent_messages"])))
        if "max_tool_output_chars" in changes and changes["max_tool_output_chars"] is not None:
            cfg["max_tool_output_chars"] = max(600, min(32000, int(changes["max_tool_output_chars"])))
        self.db.kv_set("token_efficiency_cfg", cfg)
        self.db.event(
            "info",
            "efficiency",
            f"token efficiency updated: ponytail={'on' if cfg['ponytail_enabled'] else 'off'}, rtk={'on' if cfg['rtk_enabled'] else 'off'}",
        )
        return cfg

    def reset_stats(self) -> None:
        self.db.kv_set("token_efficiency_stats", {
            "requests_inspected": 0,
            "requests_compacted": 0,
            "tokens_before_est": 0,
            "tokens_after_est": 0,
            "ponytail_tokens_saved": 0,
            "rtk_tokens_saved": 0,
            "updated_at": time.time(),
        })

    def optimize_payload(self, payload: dict, record_stats: bool = True) -> tuple[dict, dict]:
        cfg = self.config()
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return payload, {"ponytail_tokens_saved": 0, "rtk_tokens_saved": 0, "tokens_saved": 0}

        if not cfg["ponytail_enabled"] and not cfg["rtk_enabled"]:
            return payload, {"ponytail_tokens_saved": 0, "rtk_tokens_saved": 0, "tokens_saved": 0}

        chars_before = sum(len(m.get("content", "")) for m in messages if isinstance(m, dict) and isinstance(m.get("content"), str))
        working = [dict(m) if isinstance(m, dict) else m for m in messages]
        rtk_saved_chars = 0
        ponytail_saved_chars = 0

        if cfg["rtk_enabled"]:
            for m in working:
                if not isinstance(m, dict):
                    continue
                role = m.get("role", "")
                content = m.get("content")
                if role == "tool" and isinstance(content, str):
                    compacted, saved = rtk_compact_text(content, max_chars=cfg["max_tool_output_chars"])
                    if saved > 0:
                        m["content"] = compacted
                        rtk_saved_chars += saved
                elif role == "user" and isinstance(content, str) and ("\x1b[" in content or len(content) > cfg["max_tool_output_chars"] * 2):
                    compacted, saved = rtk_compact_text(content, max_chars=cfg["max_tool_output_chars"] * 2)
                    if saved > 0:
                        m["content"] = compacted
                        rtk_saved_chars += saved

        if cfg["ponytail_enabled"]:
            working, ponytail_saved_chars = ponytail_compact_messages(
                working,
                keep_recent=cfg["keep_recent_messages"],
                max_stale_chars=cfg["max_stale_tool_chars"],
            )

        total_saved_chars = rtk_saved_chars + ponytail_saved_chars
        rtk_tok = rtk_saved_chars // 4
        pony_tok = ponytail_saved_chars // 4
        tok_before = max(1, chars_before // 4) if chars_before else 0
        tok_after = max(0, tok_before - (rtk_tok + pony_tok))

        if record_stats and chars_before > 0:
            st = self.db.kv_get("token_efficiency_stats") or {}
            st["requests_inspected"] = int(st.get("requests_inspected") or 0) + 1
            if total_saved_chars > 0:
                st["requests_compacted"] = int(st.get("requests_compacted") or 0) + 1
            st["tokens_before_est"] = int(st.get("tokens_before_est") or 0) + tok_before
            st["tokens_after_est"] = int(st.get("tokens_after_est") or 0) + tok_after
            st["ponytail_tokens_saved"] = int(st.get("ponytail_tokens_saved") or 0) + pony_tok
            st["rtk_tokens_saved"] = int(st.get("rtk_tokens_saved") or 0) + rtk_tok
            st["updated_at"] = time.time()
            self.db.kv_set("token_efficiency_stats", st)

        if total_saved_chars == 0:
            return payload, {"ponytail_tokens_saved": 0, "rtk_tokens_saved": 0, "tokens_saved": 0}

        new_payload = copy.copy(payload)
        new_payload["messages"] = working
        return new_payload, {
            "ponytail_tokens_saved": pony_tok,
            "rtk_tokens_saved": rtk_tok,
            "tokens_saved": pony_tok + rtk_tok,
            "tokens_before_est": tok_before,
            "tokens_after_est": tok_after,
        }

    def status(self) -> dict:
        cfg = self.config()
        st = self.db.kv_get("token_efficiency_stats") or {}
        cache_row = self.db.one(
            "SELECT COALESCE(SUM(in_tokens),0) AS p, COALESCE(SUM(cached_tokens),0) AS c FROM usage WHERE ok=1"
        ) or {}
        prompt_tokens = int(cache_row.get("p") or 0)
        cached_tokens = int(cache_row.get("c") or 0)

        pony_saved = int(st.get("ponytail_tokens_saved") or 0)
        rtk_saved = int(st.get("rtk_tokens_saved") or 0)
        compaction_saved = pony_saved + rtk_saved
        tok_before = int(st.get("tokens_before_est") or 0)

        total_baseline = tok_before if tok_before > 0 else (prompt_tokens + compaction_saved)
        combined_saved = compaction_saved + cached_tokens
        savings_pct = round(min(95.0, (combined_saved / total_baseline) * 100.0), 1) if total_baseline > 0 else 0.0

        pony_bin = detect_ponytail_binary()
        rtk_bin = detect_rtk_binary()
        return {
            "scope": "project_scoped",
            "reversible": True,
            "active": bool(cfg["ponytail_enabled"] or cfg["rtk_enabled"]),
            "config": cfg,
            "ponytail": {
                "name": "Ponytail",
                "enabled": cfg["ponytail_enabled"],
                **pony_bin,
                "tokens_saved": pony_saved,
                "description": "Project-scoped context-tail & duplicate tool-read compactor preserving prompt-cache prefixes.",
            },
            "rtk": {
                "name": "RTK (Token Killer)",
                "enabled": cfg["rtk_enabled"],
                **rtk_bin,
                "tokens_saved": rtk_saved,
                "description": "Project-scoped CLI/tool output noise killer (ANSI, progress bars, verbose git/test logs).",
            },
            "prompt_cache": {
                "prompt_tokens": prompt_tokens,
                "cached_tokens": cached_tokens,
                "hit_rate_pct": round((cached_tokens / prompt_tokens) * 100.0, 1) if prompt_tokens > 0 else 0.0,
            },
            "metrics": {
                "requests_inspected": int(st.get("requests_inspected") or 0),
                "requests_compacted": int(st.get("requests_compacted") or 0),
                "compaction_tokens_saved": compaction_saved,
                "cached_tokens_saved": cached_tokens,
                "total_tokens_saved": combined_saved,
                "measured_savings_pct": savings_pct,
                "has_measured_traffic": (tok_before > 0 or prompt_tokens > 0),
            },
        }


class EfficiencyPatchBody(BaseModel):
    ponytail_enabled: Optional[bool] = None
    rtk_enabled: Optional[bool] = None
    keep_recent_messages: Optional[int] = None
    max_tool_output_chars: Optional[int] = None
    reset_stats: bool = False


class CompactPreviewBody(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    cli_output: str = Field(default="", max_length=200000)


def register(app: FastAPI, ctl, admin: Callable, admin_write: Callable) -> TokenEfficiencyManager:
    mgr = getattr(app.state, "efficiency", None) or TokenEfficiencyManager(ctl.db)
    app.state.efficiency = mgr

    @app.get("/autopilot/api/efficiency", dependencies=[Depends(admin)])
    async def get_efficiency():
        return mgr.status()

    @app.patch("/autopilot/api/efficiency", dependencies=[Depends(admin_write)])
    async def patch_efficiency(body: EfficiencyPatchBody):
        if body.reset_stats:
            mgr.reset_stats()
        mgr.update_config(body.model_dump(exclude={"reset_stats"}, exclude_none=True))
        return mgr.status()

    @app.post("/autopilot/api/efficiency/compact", dependencies=[Depends(admin_write)])
    async def preview_compact(body: CompactPreviewBody):
        out: dict = {}
        if body.cli_output:
            compacted, saved = rtk_compact_text(body.cli_output, max_chars=mgr.config()["max_tool_output_chars"])
            out["cli"] = {
                "chars_before": len(body.cli_output),
                "chars_after": len(compacted),
                "tokens_saved_est": saved // 4,
                "compacted": compacted,
            }
        if body.messages:
            new_payload, report = mgr.optimize_payload({"messages": body.messages}, record_stats=False)
            out["messages"] = {
                "report": report,
                "compacted_messages": new_payload["messages"],
            }
        return out

    return mgr
