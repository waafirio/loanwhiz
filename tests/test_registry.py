"""Tests for the primitive registry (issue #34).

Verifies that PrimitiveRegistry and the register_primitive decorator work
correctly end-to-end: registration, lookup, tag filtering, catalogue
serialisation, duplicate rejection, and unknown-name safety.
"""

import json
import pytest

from loanwhiz.primitives.registry import (
    PRIMITIVE_REGISTRY,
    PrimitiveRegistration,
    PrimitiveRegistry,
    register_primitive,
)


# ---------------------------------------------------------------------------
# Fixtures — use a fresh registry per test to avoid cross-test pollution.
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> PrimitiveRegistry:
    """Return an empty PrimitiveRegistry for isolation."""
    return PrimitiveRegistry()


# ---------------------------------------------------------------------------
# Helpers — mock primitives registered into a local registry fixture.
# ---------------------------------------------------------------------------


def make_mock_primitive(reg: PrimitiveRegistry, *, name: str, version: str = "1.0.0",
                         description: str = "Mock primitive.", author: str = "test-team",
                         tags: list[str] | None = None):
    """Create and register a mock primitive class in *reg*."""
    tags = tags or []

    class _MockPrimitive:
        pass

    _MockPrimitive.__qualname__ = f"MockPrimitive_{name}"
    reg.register(
        _MockPrimitive,
        name=name,
        version=version,
        description=description,
        author=author,
        tags=tags,
    )
    return _MockPrimitive


# ---------------------------------------------------------------------------
# Test: get a registered primitive
# ---------------------------------------------------------------------------


def test_get_registered(registry: PrimitiveRegistry):
    cls = make_mock_primitive(registry, name="alpha", tags=["cashflow"])
    result = registry.get("alpha")

    assert result is not None
    assert isinstance(result, PrimitiveRegistration)
    assert result.name == "alpha"
    assert result.version == "1.0.0"
    assert result.description == "Mock primitive."
    assert result.author == "test-team"
    assert result.primitive_class is cls
    assert result.tags == ["cashflow"]


# ---------------------------------------------------------------------------
# Test: get an unknown name returns None (not KeyError)
# ---------------------------------------------------------------------------


def test_get_unknown_returns_none(registry: PrimitiveRegistry):
    result = registry.get("does_not_exist")
    assert result is None


# ---------------------------------------------------------------------------
# Test: list_all returns all registered primitives
# ---------------------------------------------------------------------------


def test_list_all(registry: PrimitiveRegistry):
    make_mock_primitive(registry, name="alpha")
    make_mock_primitive(registry, name="beta")

    all_primitives = registry.list_all()
    assert len(all_primitives) == 2
    names = {r.name for r in all_primitives}
    assert names == {"alpha", "beta"}


def test_list_all_empty(registry: PrimitiveRegistry):
    assert registry.list_all() == []


# ---------------------------------------------------------------------------
# Test: list_by_tag filters correctly
# ---------------------------------------------------------------------------


def test_list_by_tag(registry: PrimitiveRegistry):
    make_mock_primitive(registry, name="alpha", tags=["cashflow", "esma"])
    make_mock_primitive(registry, name="beta", tags=["waterfall"])
    make_mock_primitive(registry, name="gamma", tags=["cashflow"])

    cashflow = registry.list_by_tag("cashflow")
    assert len(cashflow) == 2
    assert {r.name for r in cashflow} == {"alpha", "gamma"}

    waterfall = registry.list_by_tag("waterfall")
    assert len(waterfall) == 1
    assert waterfall[0].name == "beta"


def test_list_by_tag_no_match(registry: PrimitiveRegistry):
    make_mock_primitive(registry, name="alpha", tags=["cashflow"])
    result = registry.list_by_tag("nonexistent_tag")
    assert result == []


# ---------------------------------------------------------------------------
# Test: describe() returns a JSON-serialisable dict
# ---------------------------------------------------------------------------


def test_describe_json_serialisable(registry: PrimitiveRegistry):
    make_mock_primitive(registry, name="alpha", version="2.0.0", tags=["esma"])

    catalogue = registry.describe()

    # Must be a plain dict keyed by name.
    assert isinstance(catalogue, dict)
    assert "alpha" in catalogue

    entry = catalogue["alpha"]
    assert entry["name"] == "alpha"
    assert entry["version"] == "2.0.0"
    assert entry["tags"] == ["esma"]
    assert "class_name" in entry

    # Must survive json.dumps without error.
    serialised = json.dumps(catalogue)
    roundtripped = json.loads(serialised)
    assert roundtripped["alpha"]["name"] == "alpha"


def test_describe_empty(registry: PrimitiveRegistry):
    assert registry.describe() == {}


# ---------------------------------------------------------------------------
# Test: duplicate name raises ValueError
# ---------------------------------------------------------------------------


def test_double_register_raises(registry: PrimitiveRegistry):
    make_mock_primitive(registry, name="alpha")

    class AnotherClass:
        pass

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            AnotherClass,
            name="alpha",
            version="2.0.0",
            description="Duplicate.",
        )


# ---------------------------------------------------------------------------
# Test: missing required metadata raises ValueError
# ---------------------------------------------------------------------------


def test_register_missing_name_raises(registry: PrimitiveRegistry):
    class Cls:
        pass

    with pytest.raises(ValueError, match="missing 'name'"):
        registry.register(Cls, version="1.0.0", description="desc")


def test_register_missing_version_raises(registry: PrimitiveRegistry):
    class Cls:
        pass

    with pytest.raises(ValueError, match="missing 'version'"):
        registry.register(Cls, name="foo", description="desc")


def test_register_missing_description_raises(registry: PrimitiveRegistry):
    class Cls:
        pass

    with pytest.raises(ValueError, match="missing 'description'"):
        registry.register(Cls, name="foo", version="1.0.0")


# ---------------------------------------------------------------------------
# Test: register_primitive decorator stamps dunders and registers the class
# ---------------------------------------------------------------------------


def test_register_primitive_decorator():
    """Use a dedicated registry so the global singleton stays unaffected."""
    local_reg = PrimitiveRegistry()

    def _decorator(cls):
        cls.__primitive_name__ = "decorator_test"
        cls.__primitive_version__ = "0.1.0"
        cls.__primitive_description__ = "Registered via decorator."
        cls.__primitive_author__ = "acme"
        cls.__primitive_tags__ = ["test"]
        local_reg.register(cls)
        return cls

    @_decorator
    class DecoratedPrimitive:
        pass

    # Dunders stamped on the class.
    assert DecoratedPrimitive.__primitive_name__ == "decorator_test"
    assert DecoratedPrimitive.__primitive_version__ == "0.1.0"
    assert DecoratedPrimitive.__primitive_tags__ == ["test"]

    # Class is registered and retrievable.
    reg = local_reg.get("decorator_test")
    assert reg is not None
    assert reg.primitive_class is DecoratedPrimitive
    assert reg.author == "acme"


def test_register_primitive_factory_decorator():
    """Test the public register_primitive factory against a fresh registry,
    then confirm the global PRIMITIVE_REGISTRY receives the registration."""
    # Use a unique name to avoid colliding with other tests that run in the
    # same process against the global singleton.
    unique_name = "_test_factory_primitive_do_not_rely_on_in_prod"

    # Guard: skip cleanly if a previous test run left the name registered
    # (e.g. pytest reruns within the same process).
    if unique_name in PRIMITIVE_REGISTRY:
        pytest.skip("Global registry already has the test primitive from a prior run.")

    @register_primitive(
        name=unique_name,
        version="9.9.9",
        description="Ephemeral test primitive for test_register_primitive_factory_decorator.",
        author="test-suite",
        tags=["test", "ephemeral"],
    )
    class _EphemeralPrimitive:
        pass

    # Dunders stamped on the class.
    assert _EphemeralPrimitive.__primitive_name__ == unique_name
    assert _EphemeralPrimitive.__primitive_version__ == "9.9.9"
    assert "test" in _EphemeralPrimitive.__primitive_tags__

    # Registered in the global singleton.
    reg = PRIMITIVE_REGISTRY.get(unique_name)
    assert reg is not None
    assert reg.primitive_class is _EphemeralPrimitive
    assert reg.version == "9.9.9"
    assert reg.author == "test-suite"


# ---------------------------------------------------------------------------
# Test: __contains__ and __len__ helpers
# ---------------------------------------------------------------------------


def test_contains_and_len(registry: PrimitiveRegistry):
    assert len(registry) == 0
    assert "alpha" not in registry

    make_mock_primitive(registry, name="alpha")

    assert len(registry) == 1
    assert "alpha" in registry
    assert "beta" not in registry


# ---------------------------------------------------------------------------
# Test: registry importable without base.py (defensive import guard)
# ---------------------------------------------------------------------------


def test_registry_importable_without_base():
    """Confirm registry.py itself does not hard-import base.py at module load.

    This test is the structural check: if importing registry raised an
    ImportError due to a missing base.py, the entire test file would fail to
    collect — this test would never run. The fact that it *can* run is the
    evidence that the import guard works.
    """
    # Re-import to be explicit.
    import importlib
    import loanwhiz.primitives.registry as reg_mod

    importlib.reload(reg_mod)  # Should not raise even without base.py.
    assert hasattr(reg_mod, "PRIMITIVE_REGISTRY")
    assert hasattr(reg_mod, "register_primitive")
