"""Minimal HTTP client for a local ComfyUI instance.

ComfyUI's API is intentionally low-level: you POST a full workflow graph to
/prompt, then poll /history until the job finishes, then GET /view to pull
back whatever image files it wrote. This module wraps that three-step dance
so the rest of the server can just say "run this workflow, give me image
bytes" without knowing about ComfyUI's job model.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx


class ComfyUIError(RuntimeError):
    """Raised when ComfyUI rejects a prompt, a job errors, or polling times out."""


class ComfyUIClient:
    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # ComfyUI groups websocket progress messages by client_id; we don't
        # use the websocket here, but /prompt still expects one.
        self.client_id = str(uuid.uuid4())

    def queue_prompt(self, workflow: dict[str, Any]) -> str:
        """Submit a workflow graph (API-format JSON) and return its prompt_id."""
        resp = httpx.post(
            f"{self.base_url}/prompt",
            json={"prompt": workflow, "client_id": self.client_id},
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise ComfyUIError(f"ComfyUI rejected the workflow: {resp.status_code} {resp.text}")
        return resp.json()["prompt_id"]

    def wait_for_result(self, prompt_id: str, poll_interval: float = 1.0) -> dict[str, Any]:
        """Poll /history until the job completes, returning its history entry.

        ComfyUI's HTTP API has no "done" push notification (that requires the
        websocket endpoint). Polling is simpler and dependency-free, at the
        cost of latency granularity - fine for a single synchronous tool call.
        """
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            resp = httpx.get(f"{self.base_url}/history/{prompt_id}", timeout=10.0)
            resp.raise_for_status()
            history = resp.json()
            entry = history.get(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyUIError(f"ComfyUI job failed: {status}")
                if status.get("completed"):
                    return entry
            time.sleep(poll_interval)
        raise ComfyUIError(f"Timed out waiting for prompt {prompt_id} after {self.timeout}s")

    def fetch_first_image(self, history_entry: dict[str, Any]) -> bytes:
        """Pull the bytes of the first output image referenced in a job's history."""
        outputs = history_entry.get("outputs", {})
        for node_output in outputs.values():
            for image in node_output.get("images", []):
                params = {
                    "filename": image["filename"],
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                }
                resp = httpx.get(f"{self.base_url}/view", params=params, timeout=30.0)
                resp.raise_for_status()
                return resp.content
        raise ComfyUIError("Job completed but produced no image outputs")
