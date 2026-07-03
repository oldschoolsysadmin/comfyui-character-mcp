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

- `list_characters()` - lists every loaded preset, its description, and the
  controls it exposes (with descriptions, choices, defaults).
- `generate_avatar(character, controls)` - runs a preset's workflow with the
  given control values applied, and returns the resulting image.

## Adding a character preset

Each preset is two files dropped in `presets/`:

1. **`<id>.workflow.json`** - a ComfyUI workflow exported in **API format**
   (in the ComfyUI UI: enable Dev Mode in settings, then use
   "Save (API Format)"). This is the frozen graph; nothing here changes at
   request time except the specific inputs a control targets.
2. **`<id>.preset.json`** - describes the preset's controls. Two kinds:
   - `"kind": "prompt_fragment"` - the value gets substituted into
     `prompt_template.template` (a single string, rendered into one node's
     text input), so several controls can compose into one prompt without
     overwriting each other.
   - `"kind": "direct"` - the value is written straight to one
     `node_id`/`input_name` in the workflow, e.g. a KSampler's `seed`.

   A control can set `"random_if_missing": true` (only makes sense for
   numeric `direct` controls) so that omitting it draws a random value in
   `[minimum, maximum]` instead of reusing whatever the frozen workflow file
   happens to contain.

See `presets/example_hero.preset.json` and
`presets/example_hero.workflow.json` for a worked (placeholder) example -
before using it for real, swap `ckpt_name` in the workflow JSON for a
checkpoint you actually have installed, and rewrite the prompt template to
describe your actual character.
