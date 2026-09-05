"""Contract tests for the multi-annex loan-level schema registry (#451).

These tests exist to pin the contract, not merely to exercise the code. The
boundary bug this registry replaced was a *silent mis-resolution*: the tape
normaliser detected three annexes but resolved every one of them through the
Annex 2 (RMBS) table, so a corporate tape's balance column was resolved and
cited as ``RREL18``. Nothing failed; the number was simply attributed to the
wrong regulatory template.

So the assertions below are deliberately about what must **not** happen:

- a column belonging to another annex must stay unresolved, never be guessed;
- a column with no RTS field code must yield no locator, never a fabricated one;
- an annex whose detection signature its own table cannot resolve must be
  refused at registration, so detection and resolution cannot drift apart again.
"""

from __future__ import annotations

# Import a ``primitives`` module before ``loanwhiz.domain`` so the package-init
# graph resolves in the order that avoids a *pre-existing* circular import
# (``domain.__init__`` → ``inputs`` → ``provenance`` → ``primitives.base`` →
# ``primitives.__init__`` → … → ``domain.inputs``). Same guard, same reason as
# ``tests/test_esma_annex2.py``: production code always loads ``primitives``
# first, and this keeps the module runnable in isolation.
import loanwhiz.primitives.base  # noqa: F401  (import-order guard, see above)
import pytest

# Import the populated registry through the aggregator, which is what loads
# every annex table module.
from loanwhiz.domain.esma_annexes import (
    ANNEX2_RMBS,
    ANNEX4_CORPORATE,
    ANNEX5_AUTO,
    ANNEX_REGISTRY,
)
from loanwhiz.domain.esma_annex_registry import AnnexField, AnnexRegistry, AnnexSpec

ALL_SPECS = ANNEX_REGISTRY.all()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    *,
    annex_id: str = "annex_test",
    label: str = "Annex Test",
    asset_class: str = "Test",
    code_prefixes: tuple[str, ...] = ("TSTL",),
    signature_columns: frozenset[str] = frozenset({"sentinel_column"}),
    fields: tuple[AnnexField, ...] | None = None,
) -> AnnexSpec:
    """Build a throwaway spec; defaults are valid so each test varies one thing."""
    if fields is None:
        fields = (
            AnnexField(
                code="TSTL1",
                field_name="sentinel",
                description="Sentinel field.",
                canonical_column="sentinel_column",
            ),
        )
    return AnnexSpec(
        annex_id=annex_id,
        label=label,
        asset_class=asset_class,
        code_prefixes=code_prefixes,
        signature_columns=signature_columns,
        fields=fields,
    )


# ---------------------------------------------------------------------------
# Registry membership and ordering
# ---------------------------------------------------------------------------


class TestRegistryMembership:
    def test_the_three_shipped_annexes_are_registered(self) -> None:
        assert ANNEX_REGISTRY.get("annex_2") is ANNEX2_RMBS
        assert ANNEX_REGISTRY.get("annex_4") is ANNEX4_CORPORATE
        assert ANNEX_REGISTRY.get("annex_5") is ANNEX5_AUTO

    def test_detection_order_is_deterministic(self) -> None:
        # Detection returns the first matching spec, so the order must be a
        # reviewable property of the aggregator rather than an import accident.
        assert [s.annex_id for s in ALL_SPECS] == ["annex_2", "annex_4", "annex_5"]

    def test_annex_ids_and_labels_are_unique(self) -> None:
        assert len({s.annex_id for s in ALL_SPECS}) == len(ALL_SPECS)
        assert len({s.label for s in ALL_SPECS}) == len(ALL_SPECS)

    def test_corporate_is_annex_4_not_annex_8(self) -> None:
        # The RTS correction this issue turned on: Annex VIII is leasing, and
        # there is no standalone SME annex — corporate (incl. SMEs) is Annex IV
        # with CRPL codes. Reg. (EU) 2020/1224 Art. 2(1)(c).
        assert ANNEX4_CORPORATE.label == "Annex 4 (Corporate)"
        assert "CRPL" in ANNEX4_CORPORATE.code_prefixes
        assert not any("SME" in s.label for s in ALL_SPECS)
        assert not any(s.label.startswith("Annex 8") for s in ALL_SPECS)


# ---------------------------------------------------------------------------
# Governance — every resolved value keeps a regulatory locator
# ---------------------------------------------------------------------------


class TestLocatorsAndCodes:
    @pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.annex_id)
    def test_every_code_carries_one_of_its_annex_prefixes(self, spec: AnnexSpec) -> None:
        for rec in spec.fields:
            if rec.code is None:
                continue
            assert rec.code.upper().startswith(spec.code_prefixes), (
                f"{spec.annex_id}: {rec.code} is not one of {spec.code_prefixes}"
            )

    @pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.annex_id)
    def test_every_code_backed_field_yields_a_prefixed_locator(
        self, spec: AnnexSpec
    ) -> None:
        # Governance: a value resolved through any annex must still be citable
        # back to the regulatory field it came from.
        for rec in spec.fields:
            if rec.code is None:
                continue
            locator = spec.locator_for(rec.field_name)
            assert locator is not None, f"{spec.annex_id}: no locator for {rec.field_name}"
            assert locator.startswith(rec.code)
            assert rec.description in locator

    @pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.annex_id)
    def test_every_canonical_column_round_trips(self, spec: AnnexSpec) -> None:
        for rec in spec.fields:
            assert spec.canonical_column_for(rec.canonical_column) == rec.canonical_column

    def test_column_resolution_is_case_insensitive(self) -> None:
        assert ANNEX2_RMBS.code_for_column("CURRENT_BALANCE") == ANNEX2_RMBS.code_for_column(
            "current_balance"
        )


# ---------------------------------------------------------------------------
# THE CONTRACT — unresolved rather than guessed
# ---------------------------------------------------------------------------


class TestUnresolvedRatherThanGuessed:
    """A column not in the applicable annex's table must stay unresolved."""

    def test_unknown_column_resolves_to_none_in_every_annex(self) -> None:
        for spec in ALL_SPECS:
            assert spec.canonical_column_for("totally_made_up_column") is None
            assert spec.code_for_column("totally_made_up_column") is None
            assert spec.locator_for("totally_made_up_field") is None

    def test_another_annexes_column_does_not_leak_across(self) -> None:
        # epc_label/property_type are Annex 2 (RREL) concepts and are not in the
        # corporate table; enterprise_size is Annex 4 (CRPL16) and is not in the
        # RMBS table. Each must be invisible to the other annex — resolving it
        # anyway is the mis-attribution this registry exists to prevent.
        assert ANNEX4_CORPORATE.canonical_column_for("property_type") is None
        assert ANNEX4_CORPORATE.code_for_column("property_type") is None
        assert ANNEX2_RMBS.canonical_column_for("enterprise_size") is None
        assert ANNEX2_RMBS.code_for_column("enterprise_size") is None
        assert ANNEX2_RMBS.canonical_column_for("leveraged_transaction_flag") is None

    def test_the_same_column_resolves_to_its_own_annexes_code(self) -> None:
        # The regression that motivated the whole change: a shared canonical
        # column must carry the code of the annex the tape was published under,
        # not whichever table happened to be hardcoded.
        assert ANNEX2_RMBS.code_for_column("outstanding_balance") == "RREL18"
        assert ANNEX4_CORPORATE.code_for_column("outstanding_balance") == "CRPL39"
        assert ANNEX5_AUTO.code_for_column("outstanding_balance") == "AUTL30"

    def test_unmatched_tape_detects_nothing(self) -> None:
        assert ANNEX_REGISTRY.detect({"widget_id", "sprocket_count"}) is None


class TestExtensionFields:
    """A column the RTS does not define resolves, but is never given a code."""

    def test_vehicle_type_resolves_but_has_no_regulatory_anchor(self) -> None:
        # Annex V defines no vehicle-type field. The column must still resolve
        # (real tapes carry it, and detection keys on it) while yielding no
        # locator — so provenance is visibly absent rather than fabricated.
        assert ANNEX5_AUTO.canonical_column_for("vehicle_type") == "vehicle_type"
        assert ANNEX5_AUTO.code_for_column("vehicle_type") is None
        assert ANNEX5_AUTO.locator_for("vehicle_type") is None

    def test_no_annex_invents_a_code_for_an_extension_field(self) -> None:
        # Guards against a future edit "tidying up" a None code by inventing a
        # plausible-looking one. An extension field is identified by code=None
        # and must stay that way unless the RTS actually defines the field.
        extension_fields = [
            (spec, rec) for spec in ALL_SPECS for rec in spec.fields if rec.code is None
        ]
        # Guard the guard: if the shipped tables ever carry no extension field,
        # the sweep below would pass by having nothing to check.
        assert extension_fields, "no extension fields registered — sweep is vacuous"
        for spec, rec in extension_fields:
            assert spec.locator_for(rec.field_name) is None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class TestDetection:
    def test_signature_resolves_through_the_annexes_own_synonyms(self) -> None:
        # An issuer spelling EPC "epc_rating" is still Annex 2, because the
        # signature is matched through that annex's own synonym table.
        assert ANNEX_REGISTRY.detect({"epc_rating", "property_type"}) is ANNEX2_RMBS

    def test_company_size_synonym_detects_corporate(self) -> None:
        assert ANNEX_REGISTRY.detect({"company_size"}) is ANNEX4_CORPORATE

    def test_registered_signatures_are_mutually_disjoint(self) -> None:
        # If two annexes could match the same column set, detection order would
        # silently decide the asset class of a tape.
        for spec in ALL_SPECS:
            others = [s for s in ALL_SPECS if s is not spec]
            for other in others:
                assert not other.matches(set(spec.signature_columns)), (
                    f"{other.annex_id} also matches {spec.annex_id}'s signature"
                )


# ---------------------------------------------------------------------------
# Registration guards — the drift this registry must refuse to represent
# ---------------------------------------------------------------------------


class TestRegistrationGuards:
    def test_signature_unresolvable_in_own_table_is_refused(self) -> None:
        # THE anti-drift guard. A spec that can be detected on a column its own
        # field table cannot resolve is exactly the state the normaliser was in:
        # detection succeeds, resolution returns nothing. It must not be
        # registrable at all.
        registry = AnnexRegistry()
        drifted = _spec(signature_columns=frozenset({"column_not_in_the_table"}))
        with pytest.raises(ValueError, match="not.*resolvable"):
            registry.register(drifted)

    def test_signature_may_be_satisfied_by_an_extension_field(self) -> None:
        # The escape hatch that keeps the guard honest rather than merely
        # obstructive: a sentinel with no RTS code is declarable as an extension
        # field (this is exactly how vehicle_type is handled).
        registry = AnnexRegistry()
        registry.register(
            _spec(
                fields=(
                    AnnexField(
                        code=None,
                        field_name="sentinel",
                        description="Issuer-supplied sentinel, not an RTS field.",
                        canonical_column="sentinel_column",
                    ),
                )
            )
        )
        assert registry.get("annex_test") is not None

    def test_duplicate_annex_id_is_refused(self) -> None:
        registry = AnnexRegistry()
        registry.register(_spec())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_spec(label="Annex Test Two"))

    def test_duplicate_label_is_refused(self) -> None:
        registry = AnnexRegistry()
        registry.register(_spec())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_spec(annex_id="annex_test_2"))

    def test_code_not_carrying_an_annex_prefix_is_refused(self) -> None:
        # A stray RREL code in a corporate table would reintroduce exactly the
        # cross-annex mis-citation this change removes.
        registry = AnnexRegistry()
        bad = _spec(
            fields=(
                AnnexField(
                    code="RREL18",
                    field_name="sentinel",
                    description="Wrong annex's code.",
                    canonical_column="sentinel_column",
                ),
            )
        )
        with pytest.raises(ValueError, match="does not carry"):
            registry.register(bad)

    def test_duplicate_field_code_is_refused(self) -> None:
        registry = AnnexRegistry()
        dupe = _spec(
            fields=(
                AnnexField(
                    code="TSTL1",
                    field_name="sentinel",
                    description="A.",
                    canonical_column="sentinel_column",
                ),
                AnnexField(
                    code="TSTL1",
                    field_name="other",
                    description="B.",
                    canonical_column="other_column",
                ),
            )
        )
        with pytest.raises(ValueError, match="duplicate field code"):
            registry.register(dupe)

    def test_duplicate_canonical_column_is_refused(self) -> None:
        # Two rows resolving to one column would make code_for_column depend on
        # index order — the same tape column silently carrying whichever code
        # was written last. Ambiguity is refused, not resolved by convention.
        registry = AnnexRegistry()
        dupe = _spec(
            fields=(
                AnnexField(
                    code="TSTL1",
                    field_name="sentinel",
                    description="A.",
                    canonical_column="sentinel_column",
                ),
                AnnexField(
                    code="TSTL2",
                    field_name="other",
                    description="B.",
                    canonical_column="sentinel_column",
                ),
            )
        )
        with pytest.raises(ValueError, match="duplicate canonical_column"):
            registry.register(dupe)

    def test_duplicate_field_name_is_refused(self) -> None:
        registry = AnnexRegistry()
        dupe = _spec(
            fields=(
                AnnexField(
                    code="TSTL1",
                    field_name="sentinel",
                    description="A.",
                    canonical_column="sentinel_column",
                ),
                AnnexField(
                    code="TSTL2",
                    field_name="sentinel",
                    description="B.",
                    canonical_column="other_column",
                ),
            )
        )
        with pytest.raises(ValueError, match="duplicate field_name"):
            registry.register(dupe)

    def test_empty_signature_is_refused(self) -> None:
        registry = AnnexRegistry()
        with pytest.raises(ValueError, match="signature_columns is empty"):
            registry.register(_spec(signature_columns=frozenset()))

    def test_empty_code_prefixes_is_refused(self) -> None:
        registry = AnnexRegistry()
        with pytest.raises(ValueError, match="code prefix"):
            registry.register(_spec(code_prefixes=()))
