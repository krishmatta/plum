from __future__ import annotations

from pydantic import BaseModel


class Numbers(BaseModel):
    values: list[int]


class Power(BaseModel):
    x: int
    y: int
