# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load JSON Schemas from ``schemas/`` and validate data files against them.

Tools write JSON/YAML artifacts on disk; those artifacts are the interface
between stages, so they are validated against the schemas in ``schemas/``.
"""

import json
from pathlib import Path

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


class SchemaError(RuntimeError):
    """Raised when data does not match its schema."""

    def __init__(self, name, errors):
        self.name = name
        self.errors = list(errors)
        detail = "; ".join(self.errors)
        super().__init__(f"{name}: {detail}")


def schema_path(name):
    return SCHEMA_DIR / (name if name.endswith(".json") else f"{name}.schema.json")


def load_schema(name):
    return json.loads(schema_path(name).read_text(encoding="utf-8"))


def validate(instance, name):
    validator = jsonschema.Draft202012Validator(load_schema(name))
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
        for error in errors
    ]


def validate_or_raise(instance, name):
    errors = validate(instance, name)
    if errors:
        raise SchemaError(name, errors)
    return instance