"""HyperAI adapter — ASSISTED mode.

Verified (2026-11): HyperAI has no documented REST API for starting/stopping workspaces. Its official MCP server
(mcp.hyper.ai) needs an interactive browser OAuth login with a pre-registered client, and the `bayes` CLI is
documented for OpenBayes only. So this adapter does not start or stop HyperAI compute. It:
  * attaches to a running workspace over SSH (host/port entered in the dashboard; the port changes per execution),
  * detects model readiness and idleness, estimates included/extra hours,
  * tells the owner when the workspace should be stopped in the HyperAI console.
"""
from __future__ import annotations

import os

from .base import GPUProvider, InstanceStatus, ProviderError

START_HELP = ("HyperAI cannot be started automatically (no documented API). Start a workspace in the HyperAI "
              "console (RTX 5090 or 3090, vllm image), run `bash /hyperai/home/hyperai-serve.sh`, then enter the SSH "
              "port shown in its SSH panel on the Autopilot page.")
STOP_HELP = ("Stop the workspace in the HyperAI console (Stop execution). HyperAI keeps billing until it is "
             "stopped there; Autopilot only disconnected.")


class HyperAIProvider(GPUProvider):
    name = "hyperai"
    display = "HyperAI (assisted)"
    automated = False
    paid = True

    def __init__(self, settings_getter, ssh_key_path: str):
        self._settings, self.ssh_key_path = settings_getter, ssh_key_path

    def configured(self):
        s = self._settings()
        if not os.path.exists(self.ssh_key_path):
            return False, "SSH key for the GPU is missing on the server (run: sudo vac gpu init)"
        if not int(s.get("hyperai_ssh_port") or 0):
            return False, "Enter the SSH port of the running HyperAI workspace"
        return True, "ok"

    def endpoint(self) -> tuple[str, int]:
        s = self._settings()
        return str(s["hyperai_ssh_host"]), int(s["hyperai_ssh_port"])

    async def find_offer(self, profile, settings):
        raise ProviderError("manual", START_HELP)

    async def create(self, *a, **k):
        raise ProviderError("manual", START_HELP)

    async def status(self, instance_id):
        host, port = self.endpoint()
        return InstanceStatus(instance_id, "running", ssh_host=host, ssh_port=port, message="assisted")

    async def stop(self, instance_id, settings):
        raise ProviderError("manual", STOP_HELP)
