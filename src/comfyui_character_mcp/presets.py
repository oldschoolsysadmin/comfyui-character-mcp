"""Character presets: a frozen ComfyUI workflow plus a small set of named
"controls" that a caller (an LLM, via MCP tools) is allowed to adjust.

The workflow graph itself is never touched at the tool layer - only the
specific node inputs a preset chooses to expose as controls. This is what
keeps ComfyUI's graph complexity out of the MCP tool surface entirely: the
model sees "pose", "expression", "seed", not node ids and class types.

Two kinds of control are supported:

- "prompt_fragment": free text or an enum choice that gets substituted into
  a fixed prompt template (see PromptTemplate below) rather than written
  directly to a node. This is how multiple controls (pose, expression, ...)
  can compose into a single CLIPTextEncode text input without overwriting
  each other.
- "direct": a value written straight to one node's input, e.g. a seed or
  cfg value on a KSampler node.
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ControlKind = Literal["prompt_fragment", "direct"]


@dataclass
class Control:
    name: str
    kind: ControlKind
    description: str
    default: Any = None
    choices: list[str] | None = None
    minimum: float | None = None
    maximum: float | None = None
    # If true and no value/default is available, draw a random int from
    # [minimum, maximum] instead of leaving the workflow's baked-in value in
    # place. Useful for seed-like controls where "omitted" should mean
    # "different every time", not "whatever the frozen workflow JSON has".
    random_if_missing: bool = False
    # Only set (and only meaningful) for kind="direct".
    node_id: str | None = None
    input_name: str | None = None

    def validate(self, value: Any) -> Any:
        if self.choices is not None and value not in self.choices:
            raise ValueError(f"{self.name}: {value!r} is not one of {self.choices}")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"{self.name}: {value} is below minimum {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"{self.name}: {value} is above maximum {self.maximum}")
        return value

    def to_schema(self) -> dict[str, Any]:
        """Describe this control for list_characters() - no node ids leak out."""
        schema: dict[str, Any] = {"description": self.description}
        if self.choices is not None:
            schema["choices"] = self.choices
        if self.default is not None:
            schema["default"] = self.default
        if self.random_if_missing:
            schema["random_if_omitted"] = True
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        return schema


@dataclass
class PromptTemplate:
    node_id: str
    input_name: str
    template: str


@dataclass
class CharacterPreset:
    id: str
    description: str
    workflow: dict[str, Any]
    controls: dict[str, Control]
    prompt_template: PromptTemplate | None = None

    def to_schema(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "controls": {name: c.to_schema() for name, c in self.controls.items()},
        }

    def build_workflow(self, values: dict[str, Any]) -> dict[str, Any]:
        """Apply control values to a fresh copy of the frozen workflow graph."""
        workflow = copy.deepcopy(self.workflow)
        fragments: dict[str, str] = {}

        for name, control in self.controls.items():
            if name in values:
                value = control.validate(values[name])
            elif control.default is not None:
                value = control.default
            elif control.random_if_missing:
                lo = int(control.minimum) if control.minimum is not None else 0
                hi = int(control.maximum) if control.maximum is not None else 2**32 - 1
                value = random.randint(lo, hi)
            else:
                continue

            if control.kind == "prompt_fragment":
                fragments[name] = str(value)
            elif control.kind == "direct":
                workflow[control.node_id]["inputs"][control.input_name] = value

        if self.prompt_template is not None:
            fragment_defaults = {
                name: (c.default or "")
                for name, c in self.controls.items()
                if c.kind == "prompt_fragment"
            }
            rendered = self.prompt_template.template.format(**{**fragment_defaults, **fragments})
            node = workflow[self.prompt_template.node_id]
            node["inputs"][self.prompt_template.input_name] = rendered

        return workflow


def _load_control(name: str, raw: dict[str, Any]) -> Control:
    return Control(
        name=name,
        kind=raw["kind"],
        description=raw["description"],
        default=raw.get("default"),
        choices=raw.get("choices"),
        minimum=raw.get("minimum"),
        maximum=raw.get("maximum"),
        random_if_missing=raw.get("random_if_missing", False),
        node_id=raw.get("node_id"),
        input_name=raw.get("input_name"),
    )


def load_preset(preset_path: Path) -> CharacterPreset:
    """Load one `<id>.preset.json` and the workflow JSON it points to."""
    raw = json.loads(preset_path.read_text())
    workflow_path = preset_path.parent / raw["workflow"]
    workflow = json.loads(workflow_path.read_text())

    prompt_template = None
    if "prompt_template" in raw:
        pt = raw["prompt_template"]
        prompt_template = PromptTemplate(
            node_id=pt["node_id"], input_name=pt["input_name"], template=pt["template"]
        )

    controls = {name: _load_control(name, c) for name, c in raw.get("controls", {}).items()}

    return CharacterPreset(
        id=raw["id"],
        description=raw["description"],
        workflow=workflow,
        controls=controls,
        prompt_template=prompt_template,
    )


def load_all_presets(presets_dir: Path) -> dict[str, CharacterPreset]:
    """Load every `*.preset.json` in a directory, keyed by preset id."""
    presets: dict[str, CharacterPreset] = {}
    for preset_path in sorted(presets_dir.glob("*.preset.json")):
        preset = load_preset(preset_path)
        presets[preset.id] = preset
    return presets
