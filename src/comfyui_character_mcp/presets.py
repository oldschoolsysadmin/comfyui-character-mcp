"""Avatar presets: a frozen ComfyUI img2img workflow plus everything needed to
render one character at a chosen expression.

The model never sees any of this - it picks a character and an emoji, and the
preset is responsible for turning that into a concrete workflow graph. All the
ComfyUI-specific detail (which node holds the positive prompt, which holds the
reference image, what denoise to use) lives here, behind node "bindings".
"""

from __future__ import annotations

import copy
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .vocabulary import ExpressionVocabulary

# SD1.5-family checkpoints (what these presets target) are trained around
# 512px. Encoding a reference image at a much smaller or wildly different
# resolution tends to hurt generation quality, so the default is to rescale
# the reference so its shorter side lands here, preserving aspect ratio -
# not to shrink it for display. Display sizing is a separate, later concern
# (handled by the markdown the model writes), not baked into the render.
DEFAULT_ENCODE_MIN_SIDE = 512


def _read_png_dimensions(path: Path) -> tuple[int, int]:
    """Read (width, height) from a PNG's IHDR chunk, no Pillow required."""
    with open(path, "rb") as f:
        header = f.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG file")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _scale_to_min_side(width: int, height: int, min_side: int) -> tuple[int, int]:
    """Scale (width, height) so the shorter side becomes min_side, same aspect ratio.

    Rounds to the nearest multiple of 8, the spatial factor the VAE downsamples
    by - dimensions not divisible by 8 will fail or get silently truncated.
    """
    scale = min_side / min(width, height)

    def round8(n: float) -> int:
        return max(8, round(n / 8) * 8)

    return round8(width * scale), round8(height * scale)


@dataclass(frozen=True)
class NodeBinding:
    """Points at one input on one node in the workflow graph."""

    node_id: str
    input_name: str

    def write(self, workflow: dict[str, Any], value: Any) -> None:
        workflow[self.node_id]["inputs"][self.input_name] = value

    @classmethod
    def from_pair(cls, pair: list[str]) -> NodeBinding:
        node_id, input_name = pair
        return cls(node_id=node_id, input_name=input_name)


# The bindings every preset must define. Keeping this explicit means a
# malformed preset fails loudly at load time, not mid-render.
REQUIRED_BINDINGS = ("positive", "negative", "denoise", "seed", "reference_image")


@dataclass
class AvatarPreset:
    id: str
    description: str
    mode: str
    workflow: dict[str, Any]
    reference_image: Path
    base_positive: str
    base_negative: str
    denoise: float  # fallback used when an expression doesn't set its own
    bindings: dict[str, NodeBinding]
    expressions: ExpressionVocabulary
    # The resolution the reference image is rescaled to before VAE encoding.
    # Defaults (see load_preset) to the reference's own aspect ratio scaled so
    # its shorter side is DEFAULT_ENCODE_MIN_SIDE - a quality setting, not a
    # display setting. Only takes effect if the preset's workflow has a
    # resize node and both bindings are present.
    output_width: int | None = None
    output_height: int | None = None

    def to_schema(self) -> dict[str, Any]:
        """What list_characters() shows - no ComfyUI internals leak out."""
        return {
            "id": self.id,
            "description": self.description,
            "mode": self.mode,
            "expressions": self.expressions.to_schema(),
        }

    def compose_prompts(self, emoji: str) -> tuple[str, str]:
        """Combine the character's base prompts with the expression fragments."""
        expression = self.expressions.get(emoji)
        positive = self.base_positive
        if expression.positive:
            positive = f"{positive}, {expression.positive}"
        negative = self.base_negative
        if expression.negative:
            negative = f"{negative}, {expression.negative}"
        return positive, negative

    def build_workflow(self, emoji: str, reference_name: str, seed: int) -> dict[str, Any]:
        """Produce a ready-to-queue workflow for this character + expression.

        Works on a deep copy so the frozen template is never mutated. Only the
        bound inputs change; the graph's topology is exactly what the preset
        author exported from ComfyUI.
        """
        positive, negative = self.compose_prompts(emoji)
        expression = self.expressions.get(emoji)
        denoise = expression.denoise if expression.denoise is not None else self.denoise

        workflow = copy.deepcopy(self.workflow)
        self.bindings["positive"].write(workflow, positive)
        self.bindings["negative"].write(workflow, negative)
        self.bindings["denoise"].write(workflow, denoise)
        self.bindings["seed"].write(workflow, seed)
        self.bindings["reference_image"].write(workflow, reference_name)

        if self.output_width is not None and "output_width" in self.bindings:
            self.bindings["output_width"].write(workflow, self.output_width)
        if self.output_height is not None and "output_height" in self.bindings:
            self.bindings["output_height"].write(workflow, self.output_height)

        return workflow


def load_preset(preset_path: Path, base_vocabulary: ExpressionVocabulary) -> AvatarPreset:
    """Load one `<id>.preset.json`, its workflow, and its effective vocabulary."""
    raw = json.loads(preset_path.read_text(encoding="utf-8"))
    workflow_path = preset_path.parent / raw["workflow"]
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

    bindings = {name: NodeBinding.from_pair(pair) for name, pair in raw["bindings"].items()}
    missing = [name for name in REQUIRED_BINDINGS if name not in bindings]
    if missing:
        raise ValueError(f"{preset_path.name} is missing required bindings: {missing}")

    expressions = base_vocabulary.merged(raw.get("expression_overrides", {}))
    reference_image = preset_path.parent / raw["reference_image"]

    if "output_width" in raw and "output_height" in raw:
        output_width, output_height = raw["output_width"], raw["output_height"]
    else:
        ref_w, ref_h = _read_png_dimensions(reference_image)
        output_width, output_height = _scale_to_min_side(ref_w, ref_h, DEFAULT_ENCODE_MIN_SIDE)

    return AvatarPreset(
        id=raw["id"],
        description=raw["description"],
        mode=raw.get("mode", "portrait"),
        workflow=workflow,
        reference_image=reference_image,
        base_positive=raw["base_positive"],
        base_negative=raw["base_negative"],
        denoise=float(raw["denoise"]),
        bindings=bindings,
        expressions=expressions,
        output_width=output_width,
        output_height=output_height,
    )


def load_all_presets(
    presets_dir: Path, base_vocabulary: ExpressionVocabulary
) -> dict[str, AvatarPreset]:
    presets: dict[str, AvatarPreset] = {}
    for preset_path in sorted(presets_dir.glob("*.preset.json")):
        preset = load_preset(preset_path, base_vocabulary)
        presets[preset.id] = preset
    return presets
