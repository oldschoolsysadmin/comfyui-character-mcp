"""The shared expression vocabulary.

An Expression is a single emoji mapped to the prompt fragments that render it.
The set of emoji forms the allow-list that set_expression() accepts - anything
outside it is rejected before it can reach CLIP, which is what stops the model
from asking ComfyUI to draw a fork or a flag.

The vocabulary is loaded once from vocabularies/expressions.json as the shared,
character-independent baseline. Each preset then layers its own overrides on top
via ExpressionVocabulary.merged(), so a preset only has to specify the emoji it
wants to tune - everything else falls back to the shared default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Expression:
    emoji: str
    label: str
    # Appended to the preset's base positive / negative prompts respectively.
    positive: str = ""
    negative: str = ""
    # Per-expression denoise override. None means "use the preset's default
    # denoise". Expressions that only shift a small facial muscle (a smirk, a
    # closed-mouth frown) hold identity fine at a low denoise; expressions that
    # need to open the mouth or bug out the eyes (surprised, laughing) need
    # more denoise budget to actually render, regardless of how emotionally
    # "intense" the expression is.
    denoise: float | None = None


class ExpressionVocabulary:
    def __init__(self, expressions: dict[str, Expression]) -> None:
        self._expressions = expressions

    def __contains__(self, emoji: str) -> bool:
        return emoji in self._expressions

    def get(self, emoji: str) -> Expression:
        return self._expressions[emoji]

    @property
    def allowed(self) -> list[str]:
        return list(self._expressions.keys())

    def to_schema(self) -> list[dict[str, str]]:
        """Advertise the vocabulary to the model: just emoji + human label."""
        return [{"emoji": e.emoji, "label": e.label} for e in self._expressions.values()]

    def merged(self, overrides: dict[str, dict[str, Any]]) -> ExpressionVocabulary:
        """Return a new vocabulary with per-emoji overrides applied on top.

        An override may replace some or all of an entry's fields; unspecified
        fields keep the shared-default value. Overriding an emoji that isn't in
        the base vocabulary adds it (a preset can introduce a bespoke emoji).
        """
        merged = dict(self._expressions)
        for emoji, fields in overrides.items():
            base = merged.get(emoji)
            merged[emoji] = Expression(
                emoji=emoji,
                label=fields.get("label", base.label if base else emoji),
                positive=fields.get("positive", base.positive if base else ""),
                negative=fields.get("negative", base.negative if base else ""),
                denoise=fields.get("denoise", base.denoise if base else None),
            )
        return ExpressionVocabulary(merged)


def load_default_vocabulary(path: Path) -> ExpressionVocabulary:
    raw = json.loads(path.read_text(encoding="utf-8"))
    expressions = {
        emoji: Expression(
            emoji=emoji,
            label=fields.get("label", emoji),
            positive=fields.get("positive", ""),
            negative=fields.get("negative", ""),
            denoise=fields.get("denoise"),
        )
        for emoji, fields in raw["expressions"].items()
    }
    return ExpressionVocabulary(expressions)
