from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


def parse_kw(pairs: list[str] | None) -> dict[str, Any]:
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
