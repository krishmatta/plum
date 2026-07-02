from __future__ import annotations

from plum import Pipeline, Registry

from tests.example.methods.base import Method

# Pipelines key on their `name` class attr; the CLI's `run <name>` looks them up here.
PIPELINES: Registry[type[Pipeline]] = Registry("pipeline", key="name")
# Sources key on `id` (the Registry default).
METHODS: Registry[type[Method]] = Registry("method")
