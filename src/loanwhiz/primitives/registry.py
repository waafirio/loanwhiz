"""
Primitive registry — named, versioned, discoverable catalogue of SF primitives.

Usage
-----
Register a primitive class with the decorator:

    from loanwhiz.primitives.registry import register_primitive

    @register_primitive(
        name="my_primitive",
        version="1.0.0",
        description="Does something useful.",
        author="acme-team",
        tags=["cashflow", "esma"],
    )
    class MyPrimitive:
        ...

Then discover all registered primitives:

    from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY

    PRIMITIVE_REGISTRY.list_all()
    PRIMITIVE_REGISTRY.list_by_tag("cashflow")
    PRIMITIVE_REGISTRY.describe()   # JSON-serialisable catalogue dict
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    # Avoid a hard import dependency on base.py — issue #4 is a parallel
    # issue that may not yet exist. At runtime `Type` is used without the
    # bound; mypy / pyright will use the narrower type when base.py is
    # present and the guard resolves.
    from loanwhiz.primitives.base import Primitive  # noqa: F401


@dataclass
class PrimitiveRegistration:
    """Metadata record for a single registered primitive."""

    name: str
    version: str
    description: str
    primitive_class: Type
    author: str = "loanwhiz"
    tags: list[str] = field(default_factory=list)


class PrimitiveRegistry:
    """Central registry for all LoanWhiz SF primitives.

    A global singleton (``PRIMITIVE_REGISTRY``) is provided at the bottom of
    this module. External teams register a new primitive by decorating their
    class with ``@register_primitive(...)`` — no changes to core code are
    needed.
    """

    def __init__(self) -> None:
        self._registry: dict[str, PrimitiveRegistration] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        primitive_class: Type,
        *,
        name: str | None = None,
        version: str | None = None,
        description: str | None = None,
        author: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Register *primitive_class* in the registry.

        Metadata is read from class-level dunder attributes
        (``__primitive_name__``, ``__primitive_version__``,
        ``__primitive_description__``, ``__primitive_author__``,
        ``__primitive_tags__``) when keyword arguments are omitted.

        Raises
        ------
        ValueError
            If a primitive with the same name is already registered, or if
            required metadata (name, version, description) is missing.
        """
        resolved_name = name or getattr(primitive_class, "__primitive_name__", None)
        resolved_version = version or getattr(primitive_class, "__primitive_version__", None)
        resolved_description = description or getattr(primitive_class, "__primitive_description__", None)
        resolved_author = author or getattr(primitive_class, "__primitive_author__", "loanwhiz")
        resolved_tags = tags if tags is not None else list(
            getattr(primitive_class, "__primitive_tags__", [])
        )

        if not resolved_name:
            raise ValueError(
                f"Cannot register {primitive_class!r}: missing 'name'. "
                "Pass name= explicitly or set __primitive_name__ on the class."
            )
        if not resolved_version:
            raise ValueError(
                f"Cannot register '{resolved_name}': missing 'version'. "
                "Pass version= explicitly or set __primitive_version__ on the class."
            )
        if not resolved_description:
            raise ValueError(
                f"Cannot register '{resolved_name}': missing 'description'. "
                "Pass description= explicitly or set __primitive_description__ on the class."
            )

        if resolved_name in self._registry:
            raise ValueError(
                f"A primitive named '{resolved_name}' is already registered "
                f"(class: {self._registry[resolved_name].primitive_class.__qualname__!r}). "
                "Use a different name or bump the version before re-registering."
            )

        self._registry[resolved_name] = PrimitiveRegistration(
            name=resolved_name,
            version=resolved_version,
            description=resolved_description,
            primitive_class=primitive_class,
            author=resolved_author,
            tags=resolved_tags,
        )

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> PrimitiveRegistration | None:
        """Return the registration for *name*, or ``None`` if not found."""
        return self._registry.get(name)

    def list_all(self) -> list[PrimitiveRegistration]:
        """Return all registered primitives, in insertion order."""
        return list(self._registry.values())

    def list_by_tag(self, tag: str) -> list[PrimitiveRegistration]:
        """Return all primitives whose ``tags`` list includes *tag*."""
        return [r for r in self._registry.values() if tag in r.tags]

    # ------------------------------------------------------------------
    # Catalogue
    # ------------------------------------------------------------------

    def describe(self) -> dict:
        """Return a JSON-serialisable catalogue of all registered primitives.

        The returned dict is keyed by primitive name; each value is a plain
        dict with string-only leaf values suitable for ``json.dumps()``.

        Example output::

            {
                "waterfall_runner": {
                    "name": "waterfall_runner",
                    "version": "1.0.0",
                    "description": "Execute the extracted waterfall …",
                    "author": "loanwhiz",
                    "tags": ["cashflow", "waterfall"],
                    "class_name": "WaterfallRunner"
                }
            }
        """
        catalogue: dict[str, dict] = {}
        for reg in self._registry.values():
            catalogue[reg.name] = {
                "name": reg.name,
                "version": reg.version,
                "description": reg.description,
                "author": reg.author,
                "tags": list(reg.tags),
                "class_name": reg.primitive_class.__qualname__,
            }
        # Validate serializability eagerly so callers get a clear error here
        # rather than a cryptic TypeError later.
        json.dumps(catalogue)
        return catalogue

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, name: object) -> bool:
        return name in self._registry

    def __repr__(self) -> str:
        return f"PrimitiveRegistry({list(self._registry.keys())!r})"


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

PRIMITIVE_REGISTRY = PrimitiveRegistry()


# ---------------------------------------------------------------------------
# Decorator factory
# ---------------------------------------------------------------------------


def register_primitive(
    name: str,
    version: str,
    description: str,
    author: str = "loanwhiz",
    tags: list[str] | None = None,
):
    """Class decorator that registers a ``Primitive`` subclass in ``PRIMITIVE_REGISTRY``.

    The decorator stamps the class-level dunder attributes
    (``__primitive_name__``, etc.) so the registration is introspectable
    without accessing the registry, then calls
    ``PRIMITIVE_REGISTRY.register(cls)``.

    Parameters
    ----------
    name:
        Unique snake_case identifier (e.g. ``"waterfall_runner"``).
    version:
        Semver string (e.g. ``"1.0.0"``).
    description:
        One-line human-readable description for the catalogue.
    author:
        Author/team identifier. Defaults to ``"loanwhiz"`` for core primitives.
    tags:
        Optional list of tags for ``list_by_tag`` filtering
        (e.g. ``["cashflow", "esma"]``).

    Example
    -------
    ::

        @register_primitive(
            name="waterfall_runner",
            version="1.0.0",
            description="Execute an extracted waterfall against monthly tape collections.",
            tags=["cashflow", "waterfall"],
        )
        class WaterfallRunner:
            ...
    """
    resolved_tags: list[str] = tags if tags is not None else []

    def decorator(cls: Type) -> Type:
        cls.__primitive_name__ = name
        cls.__primitive_version__ = version
        cls.__primitive_description__ = description
        cls.__primitive_author__ = author
        cls.__primitive_tags__ = resolved_tags
        PRIMITIVE_REGISTRY.register(
            cls,
            name=name,
            version=version,
            description=description,
            author=author,
            tags=resolved_tags,
        )
        return cls

    return decorator
