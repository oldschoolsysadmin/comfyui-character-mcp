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

Before encoding, the reference image is also rescaled (via an `ImageScale`
node) so its shorter side is ~512px, preserving aspect ratio - by default,
computed automatically from the reference image's own dimensions (see
`output_width`/`output_height` below). This is a **quality** setting, not a
display one: SD1.5-family checkpoints are trained around 512px, and encoding
either much smaller or much larger than that tends to hurt output quality
regardless of denoise. Shrinking the image for chat display is a separate,
later concern - see "Suggested system prompt" below for controlling that
with plain markdown, independent of the actual render resolution.

## Expression vocabulary

`vocabularies/expressions.json` is the shared, character-independent set of
emoji. Each maps to `{positive, negative}` prompt fragments (CLIP can't read
emoji, so this translation is mandatory). A preset overrides only the emoji it
wants to tune via `expression_overrides`; everything else falls back to the
shared default. The emoji keys are the allow-list `set_expression()` enforces.

## Suggested system prompt

Some MCP clients bridge to models over a text-only tool-result API (common
for local models served via Ollama/vLLM-style endpoints). Those clients
receive the image content block from `set_expression`, save it to disk
themselves, and hand the model back a text placeholder with a markdown
image link to include in its reply - the model never "sees" the pixels,
it just has to remember to echo that markdown back to the user. Models
reliably forget this unless told to.

Beyond just remembering to include the image, plain markdown image syntax
also forces a line break before and after the image, at full render
resolution (~512px, kept large for generation quality - see "How a render
works" above) - the picture ends up as a big block, disconnected from the
model's commentary about it. A raw HTML `<img>` tag with a `width` fixes
both problems at once: it shrinks the image for display and lets text wrap
beside it (most chat renderers that support markdown also render inline
HTML):

```
Don't do this (forces the image onto its own block, no wrap):

![Image](./image-1783113534781.png)

He grinned, clearly pleased with how that turned out.
```

```
Do this instead (text flows beside a small inline image):

<img src="./image-1783113534781.png" width="150" align="left"> He grinned,
clearly pleased with how that turned out, and asked what you wanted to see
next.
```

A system prompt covering all of this:

```
You have access to an avatar tool for this character. Guidelines:

- Call set_expression(emoji) whenever the character's emotional tone
  shifts in the conversation - don't wait to be asked for a picture.
- Only use emoji from the character's supported list (call
  list_characters() if you're unsure which ones are allowed). Using an
  unsupported emoji will be rejected.
- The tool result includes an image file reference. You MUST include that
  image in your reply - it will not be shown to the user unless you
  include it yourself.
- Wrap the image inline with your commentary instead of putting it on its
  own line: use `<img src="..." width="150" align="left">` (or
  `align="right"`) directly before the paragraph of text that comments on
  the expression, so the picture and the reaction read together. Don't use
  bare `![Image](...)` markdown for this - it breaks the image onto its
  own block and separates it from the text about it.
- Don't describe the image in words instead of showing it; the point of
  the tool is the picture, not a caption.
```

The middle two points matter for any client; the last two are the ones
that fix "generated the image but never showed it" and "showed it, but as
an ugly disconnected block" respectively.

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
   - `output_width` / `output_height` (optional) - the resolution the
     reference image is rescaled to before VAE encoding, via a resize node in
     the workflow. If omitted, both are computed automatically from the
     reference image's own dimensions so its shorter side lands at 512px,
     preserving aspect ratio - only set these explicitly to override that.
     Needs matching `output_width`/`output_height` bindings pointed at the
     resize node.
   - `bindings` - maps logical roles (`positive`, `negative`, `denoise`,
     `seed`, `reference_image`, plus optionally `output_width`/
     `output_height`) to `[node_id, input_name]` pairs in the workflow. This
     is the only place ComfyUI node ids appear.
   - `expression_overrides` (optional) - per-emoji fragment overrides.
3. The reference image itself (e.g. `<id>.reference.png`).

See `presets/example_avatar.*` for a worked (placeholder) example - before
real use, swap the reference image, set `ckpt_name` in the workflow to a
checkpoint you have installed, and rewrite `base_positive` for your character.
