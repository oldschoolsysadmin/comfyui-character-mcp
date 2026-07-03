"""Minimal HTTP client for a local ComfyUI instance.

ComfyUI's API is intentionally low-level: you (optionally) upload input images,
POST a full workflow graph to /prompt, poll /history until the job finishes,
then GET /view to pull back whatever image files it wrote. This module wraps
that dance so the rest of the server can just say "upload this reference, run
this workflow, give me image bytes" without knowing about ComfyUI's job model.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
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

    def upload_image(self, path: Path, overwrite: bool = True) -> str:
        """Upload a local image into ComfyUI's input space; return its ref name.

        This is what lets a preset keep its reference image in the repo instead
        of requiring the user to pre-stage files in ComfyUI's input folder. The
        returned name is what a LoadImage node's "image" input expects (prefixed
        with a subfolder if ComfyUI placed it in one).
        """
        with open(path, "rb") as f:
            resp = httpx.post(
                f"{self.base_url}/upload/image",
                files={"image": (path.name, f, "image/png")},
                data={"overwrite": "true" if overwrite else "false"},
                timeout=30.0,
            )
        if resp.status_code != 200:
            raise ComfyUIError(f"Reference upload failed: {resp.status_code} {resp.text}")
        info = resp.json()
        subfolder = info.get("subfolder", "")
        return f"{subfolder}/{info['name']}" if subfolder else info["name"]

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
