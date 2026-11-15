"""Configuration.

Two layers:
  * Env (from /opt/vellur-ai-core/.env, set by vac): secrets, hard ceilings, live/dry-run. Never editable from the web.
  * Settings (SQLite, editable from the dashboard): limited by the hard ceilings above.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None or v == "" else v.strip().lower() in ("1", "true", "yes", "on")


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Env:
    # Live mode: only then may a provider be asked to create/start/stop/destroy anything billable.
    live: bool = field(default_factory=lambda: _b("AUTOPILOT_LIVE", False))
    hard_max_monthly_usd: float = field(default_factory=lambda: _f("AUTOPILOT_HARD_MAX_MONTHLY_USD", 50.0))
    hard_max_hourly_usd: float = field(default_factory=lambda: _f("AUTOPILOT_HARD_MAX_HOURLY_USD", 0.50))
    admin_header_secret: str = field(default_factory=lambda: os.environ.get("AUTOPILOT_ADMIN_SECRET", ""))
    data_dir: str = field(default_factory=lambda: os.environ.get("AUTOPILOT_DATA_DIR", "/data"))
    ssh_key_path: str = field(default_factory=lambda: os.environ.get("AUTOPILOT_SSH_KEY", "/run/vellur/gpu_ed25519"))
    ssh_known_hosts_seed: str = field(default_factory=lambda: os.environ.get("AUTOPILOT_KNOWN_HOSTS_SEED", "/run/vellur/gpu_known_hosts"))
    vllm_api_key: str = field(default_factory=lambda: os.environ.get("VELLUR_GPU_API_KEY", ""))
    gateway_url: str = field(default_factory=lambda: os.environ.get("AUTOPILOT_GATEWAY_URL", "http://gateway:4000"))
    gateway_master_key: str = field(default_factory=lambda: os.environ.get("LITELLM_MASTER_KEY", ""))
    vast_api_key: str = field(default_factory=lambda: os.environ.get("VAST_API_KEY", ""))
    vast_api_url: str = field(default_factory=lambda: os.environ.get("VAST_API_URL", "https://console.vast.ai"))
    notify_url: str = field(default_factory=lambda: os.environ.get("AUTOPILOT_NOTIFY_URL", ""))
    public_origin: str = field(default_factory=lambda: os.environ.get("AUTOPILOT_PUBLIC_ORIGIN", ""))
    # Seconds; test suites shorten these.
    tick_seconds: float = field(default_factory=lambda: _f("AUTOPILOT_TICK_SECONDS", 15.0))
    billing_sync_seconds: float = field(default_factory=lambda: _f("AUTOPILOT_BILLING_SYNC_SECONDS", 600.0))
    request_hold_seconds: float = field(default_factory=lambda: _f("AUTOPILOT_REQUEST_HOLD_SECONDS", 900.0))
    provision_timeout_seconds: float = field(default_factory=lambda: _f("AUTOPILOT_PROVISION_TIMEOUT", 1500.0))
    model_load_timeout_seconds: float = field(default_factory=lambda: _f("AUTOPILOT_MODEL_LOAD_TIMEOUT", 2400.0))
    poll_seconds: float = field(default_factory=lambda: _f("AUTOPILOT_POLL_SECONDS", 10.0))
    # Agent runner (isolated container). Empty URL/secret = agent tasks unavailable.
    agent_runner_url: str = field(default_factory=lambda: os.environ.get("AGENT_RUNNER_URL", ""))
    agent_runner_secret: str = field(default_factory=lambda: os.environ.get("AGENT_RUNNER_SECRET", ""))
    self_repo: str = field(default_factory=lambda: os.environ.get("VELLUR_SELF_REPO", "dicuvictorgabriel-a11y/vellur-ai-core"))
    agent_proxy_url: str = field(default_factory=lambda: os.environ.get("AGENT_PROXY_URL", "http://autopilot:8000/v1"))


# Defaults for the dashboard-editable settings. Values are clamped by Env ceilings in Settings.validate().
DEFAULT_SETTINGS: dict = {
    "enabled": True,                 # Autopilot reacts to requests and runs its watchdog
    "preferred_provider": "vastai",   # GPU is optional; HyperAI stays available but is no longer the default
    "profile": "coding",
    "idle_timeout_min": 20,
    "monthly_budget_usd": 50.0,
    "max_hourly_usd": 0.50,
    "max_session_hours": 3.0,
    "auto_start_paid": False,        # a GPU request may start a paid provider by itself
    "paid_fallback": False,          # if the preferred provider fails, try the next one
    "hyperai_ssh_host": "ssh.hyper.ai",
    "hyperai_ssh_port": 0,
    "hyperai_included_hours": 30.0,  # HyperAI Pro: RTX 5090 hours included per month (advertised)
    "hyperai_hourly_after_usd": 0.35,
    "vast_min_reliability": 0.98,
    "vast_geo_preference": "NL,DE,BE,FR,PL,RO,SE,FI,GB",
    "vast_idle_action": "destroy",   # destroy = no storage charges; stop = keeps disk (billed) for faster restart
    "vast_disk_gb": 60,
    "max_session_usd": 5.0,          # a GPU session is released when its estimated cost reaches this
    # Model routing: public model name → ordered targets ("provider:model-id" or "gpu"). First usable target wins.
    "routes": {"vellur-coder": [], "vellur-chat": [], "vellur-max": [], "vellur-image": []},
    # Manual prices, USD per million tokens: {"provider:model-id": [input, output]} (catalog prices are used otherwise)
    "prices": {},
    # Manual image prices, USD per generated image (catalog prices are used otherwise).
    "image_prices": {},
    "image_max_usd": 1.0,            # hard per-request estimate limit for image generation
    "image_confirm_paid": True,      # ask the owner before every paid image generation
    "agent_image_generation": True,  # expose the vellur-image tool to coding agents when a provider is configured
    "agent_image_max_usd": 0.50,     # per image requested by an agent

    # Shown in the cost report. in_budget=False: visible, but not counted against the monthly budget.
    "fixed_costs": [{"name": "VPS ro-prod-01", "usd": 0.0, "in_budget": False},
                    {"name": "HyperAI Pro (already purchased)", "usd": 4.99, "in_budget": False}],
    "memory": "",                    # project notes added as a system message to dashboard chats and agent tasks
    "agent_max_minutes": 30,
    "agent_max_usd": 2.0,
    "agent_vision": False,           # the model behind the agent accepts images: it may open screenshots to inspect them
    "agent_visual_inspection": False,
    "agent_auto_screenshots": False,
    "agent_auto_publish": True,
    "agent_auto_publish_main_repo": "",
    "attachment_max_mb": 25,           # largest single file the owner may attach
    "attachment_max_count": 10,        # files per message or task
    "attachment_max_total_mb": 60,     # total bytes accepted by one upload request
    "attachment_max_text_chars": 120000,  # extracted text included in model context
    "attachment_vision_max_mb": 10,    # larger images are described locally instead of sent to the model
}

ROLES: dict = {
    "vellur-coder": "Coding (default for agents and OpenCode)",
    "vellur-chat": "General chat (economical)",
    "vellur-max": "Hard problems (strongest model you connected)",
    "vellur-image": "Image generation (requires an image-output model)",
}

# Model profiles. The served name is always "vellur-coder" so LiteLLM and clients never change.
PROFILES: dict = {
    "fast": {
        "label": "Fast chat",
        "model_repo": "RedHatAI/Qwen3.8-27B-INT4",
        "min_vram_gb": 24, "max_model_len": 32768, "download_gb": 19,
        "verified": "RedHatAI/Qwen3.8-27B-INT4 ran on HyperAI H100 with vLLM 0.27.1 (owner output, 2026-11-13)",
    },
    "coding": {
        "label": "General coding",
        "model_repo": "RedHatAI/Qwen3.8-27B-INT4",
        "min_vram_gb": 32, "max_model_len": 131072, "download_gb": 19,
        "verified": "RedHatAI/Qwen3.8-27B-INT4 ran on HyperAI H100 with vLLM 0.27.1 (owner output, 2026-11-13)",
    },
    "advanced": {
        "label": "Advanced coding (FP8, ≥48 GB)",
        "model_repo": "Qwen/Qwen3.8-27B-FP8",
        "min_vram_gb": 48, "max_model_len": 131072, "download_gb": 30,
        "verified": "not verified: repository name and fit must be checked before use",
    },
}

VLLM_IMAGE = os.environ.get("AUTOPILOT_VLLM_IMAGE", "vllm/vllm-openai:v0.27.1")
MANAGED_LABEL = "vellur-autopilot"
