"""MCP server exposing ComfyUI character avatars as tools.

Transport is stdio: the MCP client (Claude Desktop/Code) spawns this process
and speaks JSON-RPC over stdin/stdout. ComfyUI and this server both run on the
same machine, so there's no need for HTTP, ports, or auth. Returned images
travel as base64-encoded content blocks either way.

Design: the render engine (_render) is stateless - given a preset and an emoji
it produces image bytes. A thin layer of session state on top remembers which
character is "current" and its last expression, so the model can just say
set_expression("😊") in a conversation without repeating the character each turn.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from .comfyui_client import ComfyUIClient, ComfyUIError
from .presets import AvatarPreset, load_all_presets
from .vocabulary import load_default_vocabulary

# src/comfyui_character_mcp/server.py -> repo root
_ROOT = Path(__file__).resolve().parents[2]
PRESETS_DIR = _ROOT / "presets"
VOCAB_PATH = _ROOT / "vocabularies" / "expressions.json"
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
_MAX_SEED = 2**32 - 1

mcp = FastMCP("comfyui-character-mcp")
_vocabulary = load_default_vocabulary(VOCAB_PATH)
_presets = load_all_presets(PRESETS_DIR, _vocabulary)
_client = ComfyUIClient(COMFYUI_URL)

# Session state. Default the current character to the sole preset if there's
# exactly one, so a single-character setup needs no explicit selection.
_current_character: str | None = next(iter(_presets)) if len(_presets) == 1 else None
_current_expression: str | None = None

# Reference images only need uploading once per process; cache the ref name
# ComfyUI hands back so we don't re-upload on every expression change.
_uploaded_refs: dict[str, str] = {}


def _ensure_reference_uploaded(preset: AvatarPreset) -> str:
    if preset.id not in _uploaded_refs:
        _uploaded_refs[preset.id] = _client.upload_image(preset.reference_image)
    return _uploaded_refs[preset.id]


def _render(preset: AvatarPreset, emoji: str) -> bytes:
    """Stateless render: preset + emoji -> image bytes."""
    reference_name = _ensure_reference_uploaded(preset)
    seed = random.randint(0, _MAX_SEED)
    workflow = preset.build_workflow(emoji, reference_name, seed)
    prompt_id = _client.queue_prompt(workflow)
    history_entry = _client.wait_for_result(prompt_id)
    return _client.fetch_first_image(history_entry)


@mcp.tool()
def list_characters() -> list[dict]:
    """List available avatar characters and the expressions each one supports."""
    return [preset.to_schema() for preset in _presets.values()]


@mcp.tool()
def get_current_look() -> dict:
    """Report which character is currently selected and its last expression."""
    return {"character": _current_character, "expression": _current_expression}


@mcp.tool()
def set_expression(emoji: str, character: str | None = None) -> Image:
    """Render the avatar showing the given expression, and return the image.

    `emoji` must be one of the expressions the character supports (call
    list_characters() to see them). `character` selects which avatar to use;
    omit it to reuse the currently-selected character. The selection and
    expression are remembered, so follow-up calls can just pass a new emoji.
    """
    global _current_character, _current_expression

    character_id = character or _current_character
    if character_id is None:
        known = ", ".join(sorted(_presets)) or "(none loaded)"
        raise ValueError(f"No character selected. Pass character=... (known: {known}).")
    if character_id not in _presets:
        known = ", ".join(sorted(_presets)) or "(none loaded)"
        raise ValueError(f"Unknown character {character_id!r}. Known: {known}.")

    preset = _presets[character_id]
    if emoji not in preset.expressions:
        allowed = " ".join(preset.expressions.allowed)
        raise ValueError(f"Expression {emoji!r} not allowed for {character_id!r}. Allowed: {allowed}")

    try:
        image_bytes = _render(preset, emoji)
    except ComfyUIError as exc:
        raise RuntimeError(f"ComfyUI generation failed: {exc}") from exc

    _current_character = character_id
    _current_expression = emoji
    return Image(data=image_bytes, format="png")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
