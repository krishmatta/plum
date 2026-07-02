from __future__ import annotations

from plum import Pipeline, Registry

from tests.example.methods.base import Method

PIPELINES: Registry[type[Pipeline]] = Registry("pipeline", key="name")
METHODS: Registry[type[Method]] = Registry("method")
