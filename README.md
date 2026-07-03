# comfyui-character-mcp

An MCP server that exposes ComfyUI-generated character "avatars" as tools.
Each character is a **preset**: a frozen ComfyUI workflow graph plus a small,
named set of **controls** (pose, expression, seed, ...) that a caller is
allowed to adjust. The model never sees or edits the ComfyUI graph itself -
only the specific knobs each preset chooses to expose.

## Why stdio, not HTTP

This server runs as a local subprocess that the MCP client (Claude Desktop /
Claude Code) launches directly, talking JSON-RPC over stdin/stdout. That's
the natural transport when ComfyUI and the MCP server both run on the same
machine, since there's no networking or auth to set up. Returned images are
sent as base64-encoded content blocks regardless of transport - stdio
doesn't limit what can flow back to the client, it only changes how the
process is reached. If ComfyUI ever needs to live on a different machine, or
you want one long-running server shared by multiple clients, this can move
to Streamable HTTP transport later without changing the tool logic at all.

## Requirements

- A running local ComfyUI instance (default: `http://127.0.0.1:8188`,
  override with the `COMFYUI_URL` environment variable)
- [uv](https://docs.astral.sh/uv/) for dependency management

## Running

```sh
uv run comfyui-character-mcp
```

To register it with an MCP client, point the client's config at this
command (adjust the path to wherever you cloned this repo):

```json
{
  "mcpServers": {
    "comfyui-character": {
      "command": "uv",
      "args": ["run", "--directory", "C:/Users/alexg/src/comfyui-character-mcp", "comfyui-character-mcp"]
    }
  }
}
```

## Tools

- `list_characters()` - lists every loaded character, its description, and the
  expressions (emoji + label) it supports.
- `set_expression(emoji, character?)` - renders the avatar showing that
  expression and returns the image. `character` is optional; omit it to reuse
  the currently-selected character. Only emoji in the character's allow-list
  are accepted - anything else is rejected before it reaches the model.
- `get_current_look()` - reports the currently-selected character and its last
  expression (the small bit of session state the setter tools maintain).

## How a render works

Each character is an **img2img** flow: the preset's reference image is uploaded
to ComfyUI, VAE-encoded, and run through the sampler at a **low denoise**. Low
denoise keeps the generated face locked to the reference (that's the identity
mechanism); the chosen expression's prompt fragments nudge the face within that
budget. Turn `denoise` up in the preset for more expression range, down to stay
closer to the reference. If denoise alone can't hold identity across strong
expressions, the next step is adding low-strength ControlNet edge-following.

## Expression vocabulary

`vocabularies/expressions.json` is the shared, character-independent set of
emoji. Each maps to `{positive, negative}` prompt fragments (CLIP can't read
emoji, so this translation is mandatory). A preset overrides only the emoji it
wants to tune via `expression_overrides`; everything else falls back to the
shared default. The emoji keys are the allow-list `set_expression()` enforces.

## Adding a character preset

Each character is defined by files dropped in `presets/`:

1. **`<id>.workflow.json`** - a ComfyUI img2img workflow exported in **API
   format** (enable Dev Mode in ComfyUI settings, then "Save (API Format)").
   This is the frozen graph; only the inputs named in `bindings` change per
   request.
2. **`<id>.preset.json`** - the character definition:
   - `reference_image` - the portrait this character is generated from.
   - `base_positive` / `base_negative` - the character's identity prompt and
     quality negatives. Expression fragments are appended to these.
   - `denoise` - the identity-vs-expression trade-off knob (not model-facing).
   - `bindings` - maps logical roles (`positive`, `negative`, `denoise`,
     `seed`, `reference_image`) to `[node_id, input_name]` pairs in the
     workflow. This is the only place ComfyUI node ids appear.
   - `expression_overrides` (optional) - per-emoji fragment overrides.
3. The reference image itself (e.g. `<id>.reference.png`).

See `presets/example_avatar.*` for a worked (placeholder) example - before
real use, swap the reference image, set `ckpt_name` in the workflow to a
checkpoint you have installed, and rewrite `base_positive` for your character.
