"""MCP server exposing ComfyUI character presets as tools.

Transport is stdio: the MCP client (Claude Desktop/Code) spawns this process
directly and speaks JSON-RPC over stdin/stdout. That's a deliberate choice
for this project - ComfyUI and this server both run on the same machine, so
there's no need for HTTP, ports, or auth. Returned images travel as
base64-encoded content blocks either way; stdio vs HTTP only affects how the
*process* is reached, not whether images can flow back to the client.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from .comfyui_client import ComfyUIClient, ComfyUIError
from .presets import load_all_presets

# src/comfyui_character_mcp/server.py -> repo root, then /presets
PRESETS_DIR = Path(__file__).resolve().parents[2] / "presets"
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")

mcp = FastMCP("comfyui-character-mcp")
_presets = load_all_presets(PRESETS_DIR)
_client = ComfyUIClient(COMFYUI_URL)


@mcp.tool()
def list_characters() -> list[dict]:
    """List available character presets and the controls each one exposes."""
    return [preset.to_schema() for preset in _presets.values()]


@mcp.tool()
def generate_avatar(character: str, controls: dict[str, object] | None = None) -> Image:
    """Generate an image of a character preset, adjusted by the given controls.

    Call list_characters() first to see valid character ids and the controls
    (and allowed values) each one supports.
    """
    if character not in _presets:
        known = ", ".join(sorted(_presets)) or "(none loaded)"
        raise ValueError(f"Unknown character {character!r}. Known characters: {known}")
    preset = _presets[character]

    try:
        workflow = preset.build_workflow(controls or {})
        prompt_id = _client.queue_prompt(workflow)
        history_entry = _client.wait_for_result(prompt_id)
        image_bytes = _client.fetch_first_image(history_entry)
    except ComfyUIError as exc:
        raise RuntimeError(f"ComfyUI generation failed: {exc}") from exc

    return Image(data=image_bytes, format="png")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
