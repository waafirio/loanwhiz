"""Introspect ``PRIMITIVE_REGISTRY`` into a JSON-serialisable catalogue.

This is the single source of truth the server uses both to build its tool list
(``live`` primitives only) and to expose the full catalogue (all primitives,
incl. ``library-only``) as an MCP resource. Keeping the introspection here keeps
``server.py`` thin and gives the smoke test a pure function to assert against.

The catalogue merges, per primitive:
- the registry metadata (name / version / description / author / tags /
  class_name) from ``PRIMITIVE_REGISTRY.describe()``, and
- the typed Pydantic input/output JSON schemas from the primitive class's own
  ``describe()`` classmethod, and
- the reachability (``live`` / ``library-only``) from
  :mod:`loanwhiz_primitives_mcp.reachability`.

Nothing is rewritten — this reads the same contracts ``GET /primitives`` reads.
"""

from __future__ import annotations

import importlib
from typing import Any

from loanwhiz_primitives_mcp.reachability import LIVE, reachability_of

# Importing each primitive module runs its ``@register_primitive`` decorator and
# populates the global ``PRIMITIVE_REGISTRY`` — the same registration-by-import
# pattern ``loanwhiz.api.main`` uses. We import the primitive modules directly
# (not ``loanwhiz.api``) so the MCP server has no dependency on the REST app.
_PRIMITIVE_MODULES = (
    "audit_logger",
    "cashflow_projector",
    "collections_aggregator",
    "covenant_monitor",
    "esma_tape_normaliser",
    "report_verifier",
    "waterfall_runner",
    "waterfall_state",  # registers multi_period_waterfall_runner
)


def ensure_primitives_registered() -> None:
    """Import every primitive module so the registry is fully populated.

    Idempotent: re-importing an already-imported module is a no-op, and the
    registry's own duplicate-name guard means a module can't double-register.
    """
    for module in _PRIMITIVE_MODULES:
        importlib.import_module(f"loanwhiz.primitives.{module}")


def primitive_input_type(primitive_class: type) -> type | None:
    """Recover a primitive's Pydantic *input* model from its generic base.

    Uses the same ``__orig_bases__`` walk as
    :meth:`loanwhiz.primitives.base.Primitive.describe` — Python's runtime
    generics don't expose the bound type arguments directly, so we read them off
    the ``Primitive[InputT, OutputT]`` parameterisation. Returns ``None`` if no
    parameterised base is found.
    """
    from pydantic import BaseModel

    for base in getattr(primitive_class, "__orig_bases__", []):
        args = getattr(base, "__args__", None)
        if args and len(args) == 2:
            in_type = args[0]
            if isinstance(in_type, type) and issubclass(in_type, BaseModel):
                return in_type
    return None


def build_catalogue() -> list[dict[str, Any]]:
    """Return the full primitive catalogue as a list of JSON-serialisable dicts.

    One entry per registered primitive (all 8 — ``live`` and ``library-only``),
    in the registry's insertion order. Each entry carries the registry metadata,
    the typed input/output JSON schemas, the reachability, and the framework's
    confidence semantics so a consumer can introspect the whole framework, not
    just the callable tools.
    """
    ensure_primitives_registered()

    # Imported lazily so ``ensure_primitives_registered`` has run first and so
    # importing this module never fails if ``loanwhiz`` is not yet on the path.
    from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY

    described = PRIMITIVE_REGISTRY.describe()
    catalogue: list[dict[str, Any]] = []
    for name, meta in described.items():
        registration = PRIMITIVE_REGISTRY.get(name)
        input_schema: dict[str, Any] = {}
        output_schema: dict[str, Any] = {}
        if registration is not None:
            primitive_meta = registration.primitive_class.describe()
            input_schema = primitive_meta.input_schema
            output_schema = primitive_meta.output_schema
        catalogue.append(
            {
                "name": meta["name"],
                "version": meta["version"],
                "description": meta["description"],
                "author": meta["author"],
                "tags": list(meta["tags"]),
                "class_name": meta["class_name"],
                "reachability": reachability_of(meta["name"]),
                "input_schema": input_schema,
                "output_schema": output_schema,
                "confidence": (
                    "Every primitive returns a PrimitiveResult with a confidence "
                    "score in [0.0, 1.0]: 1.0 for deterministic/rule-based "
                    "computation, lower when model or data-quality uncertainty "
                    "applies. The result also carries citations and a structured "
                    "audit_entry (input hash, timestamp, duration) for governance."
                ),
            }
        )
    return catalogue


def live_tool_names() -> list[str]:
    """Return the names of the primitives exposed as callable MCP tools.

    These are the registered primitives whose reachability is ``live`` — the
    intersection of "registered" and "reachable", so the server never advertises
    a tool for a primitive that isn't actually in the registry.
    """
    return [entry["name"] for entry in build_catalogue() if entry["reachability"] == LIVE]
