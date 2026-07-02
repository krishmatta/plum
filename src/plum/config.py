from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


def parse_kw(pairs: list[str] | None) -> dict[str, Any]:
    """Parse CLI ``key=value`` pairs; values parse as JSON with raw-string fallback.

    ``n=3`` -> 3, ``flag=true`` -> True, ``tags=[1,2]`` -> [1, 2], ``label=hi`` -> "hi".
    Caveat: a literal string that is valid JSON (``"true"``, ``"3"``) can't be
    expressed; rely on downstream pydantic validation to coerce as needed.
    """
    out: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"expected key=value, got: {pair}")
        key, value = pair.split("=", 1)
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out
