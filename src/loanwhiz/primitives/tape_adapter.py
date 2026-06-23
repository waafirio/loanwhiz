"""``TapeAdapter`` — an ESMA tape → canonical ``source="tape"`` ``PeriodInputs`` (#364).

The **input adapter for the tape-driven deal path**, the tape-side sibling of
:class:`~loanwhiz.primitives.report_adapter.ReportAdapter`. It is the missing
``source="tape"`` constructor (#364, epic #360 "Tape-path canonicalisation"):
the report path already produces canonical
:class:`~loanwhiz.domain.inputs.PeriodInputs`, but the tape path used to stop at
the legacy tape-only :class:`~loanwhiz.primitives.deal_state.PeriodCollections`
and never assembled a :class:`~loanwhiz.domain.inputs.RiskSignals`
(``report_adapter.py`` set ``risk_signals=None``; no tape-source constructor
existed anywhere). This adapter closes that gap so the tape path folds through
the **same** ``run_period`` kernel and the **same** canonical schema the report
path uses.

What it assembles
-----------------
From the two artefacts the tape path already computes per period —

- a :class:`~loanwhiz.primitives.collections_aggregator.CollectionsOutput`
  (the five separated collection legs + the pool-level aggregates), and
- the normalised :class:`~loanwhiz.primitives.esma_tape_normaliser.EsmaTapeOutput`
  pool analytics (``pool_balance_eur``, ``pool_stats.wtd_ltv``,
  ``arrears_breakdown.{arrears_180d_plus_pct, default_pct}``) —

it builds one canonical ``PeriodInputs`` with ``source="tape"`` carrying:

- **``legs``** — a :class:`~loanwhiz.domain.inputs.CollectionLegs` mapped
  one-to-one from the ``CollectionsOutput`` legs (so ``_normalize_period``
  reduces it to the *exact* ``PeriodCollections`` the legacy tape path
  produced — the fold is byte-for-byte unchanged on real deals);
- **``risk_signals``** — a populated ``RiskSignals`` derived from the tape's
  pool analytics, with ESMA RTS Annex 2 RREL field codes anchored in each
  field's :class:`~loanwhiz.domain.provenance.FieldProvenance` citation.

Honouring each ``RiskSignals`` field's documented contract
----------------------------------------------------------
The canonical :class:`RiskSignals` fields are **not** all the same unit, and the
adapter honours each one's documented meaning rather than dumping percentages in:

- ``arrears_90d`` / ``arrears_180d`` — pool **balances** ("Balance >=N days in
  arrears"); the normaliser publishes them as percentages of loan count, so the
  adapter converts via ``pool_balance × pct / 100``.
- ``default_pct`` — the defaulted **fraction of the pool** (per its name and
  docstring, "Defaulted balance as a fraction of the pool"): the normaliser's
  ``default_pct`` percentage carried through as a 0–1 fraction (``pct / 100``).
- ``wa_ltv`` — the balance-weighted LTV ratio, passed through unchanged.
- ``pool_balance`` — the tape's ``pool_balance_eur`` balance.

``arrears_90d`` derivation (honest, conservative)
-------------------------------------------------
The normaliser's arrears buckets are ``<29d`` / ``180+d`` / defaulted — it
exposes **no distinct ≥90-day bucket**. Rather than fabricate one, ``arrears_90d``
is derived as the balance of loans **≥90 days in arrears**, which on the
available bucket schema is the ≥180d-arrears balance ∪ the defaulted balance
(≥90d ⊇ ≥180d ⊇ default). This is conservative-but-honest: it never invents a
finer bucket, and the docstring + provenance citation record exactly what it is.
A future child that adds a true ≥90d-<180d bucket to ``esma_tape_normaliser``
can refine this in one place. ``arrears_180d`` is the ≥180d-arrears balance.

Pure & offline: depends only on the two already-computed pool artefacts. No
network, no LLM, no engine call.
"""

from __future__ import annotations

from dataclasses import dataclass

from loanwhiz.domain.inputs import CollectionLegs, PeriodInputs, RiskSignals
from loanwhiz.domain.provenance import FieldProvenance, ProvenanceMap
from loanwhiz.primitives.base import Citation
from loanwhiz.primitives.collections_aggregator import CollectionsOutput
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeOutput

# ---------------------------------------------------------------------------
# ESMA RTS Annex 2 RREL field-code anchors for each RiskSignals field.
# These are the regulatory locators the schema design (decision D5/D8) fixes on
# RiskSignals provenance — the value's traceability back to the Annex 2 field.
# ---------------------------------------------------------------------------

#: Outstanding current balance — RREL18.
_RREL_BALANCE = "RREL18"
#: Current loan-to-value — RREL40.
_RREL_LTV = "RREL40"
#: Arrears bucket / balance — RREL64.
_RREL_ARREARS = "RREL64"
#: Default (CRR) status — RREL66.
_RREL_DEFAULT = "RREL66"


def _pct_to_balance(pool_balance: float, pct: float) -> float:
    """Convert a *percentage of pool* to an absolute balance, floored at 0.

    ``RiskSignals`` arrears / default fields are pool *balances*; the normaliser
    publishes them as percentages. ``balance = pool_balance × pct / 100``. Both
    inputs are floored at 0 (a negative pct/balance is meaningless here).
    """
    if pool_balance <= 0.0 or pct <= 0.0:
        return 0.0
    return pool_balance * pct / 100.0


@dataclass(frozen=True)
class TapeAdapter:
    """Turn an ESMA tape's per-period artefacts into a canonical ``PeriodInputs``.

    Stateless: every method takes the period's two pool artefacts directly. The
    adapter exists as a class (rather than free functions) to mirror
    :class:`~loanwhiz.primitives.report_adapter.ReportAdapter`'s shape and to be
    the obvious home for any future tape-specific configuration.
    """

    @staticmethod
    def risk_signals_from_tape(
        tape: EsmaTapeOutput,
    ) -> tuple[RiskSignals, ProvenanceMap]:
        """Derive a populated :class:`RiskSignals` (+ provenance) from the tape.

        Maps the normaliser's pool analytics onto the five canonical risk
        signals:

        - ``pool_balance`` ← ``tape.pool_balance_eur`` (RREL18);
        - ``wa_ltv`` ← ``tape.pool_stats["wtd_ltv"]`` (RREL40), 0.0 when absent;
        - ``default_pct`` ← defaulted **fraction** of the pool =
          ``arrears_breakdown["default_pct"] / 100`` (RREL66) — a 0–1 fraction,
          per the field's documented "fraction of the pool" contract;
        - ``arrears_180d`` ← balance ≥180 days in arrears = ``pool_balance ×
          arrears_breakdown["arrears_180d_plus_pct"] / 100`` (RREL64);
        - ``arrears_90d`` ← balance **≥90 days** in arrears. The normaliser has
          no distinct ≥90d bucket, so this is the conservative ≥180d-arrears ∪
          defaulted *balance* (≥90d ⊇ ≥180d ⊇ default) — documented, never
          fabricated.

        Returns ``(RiskSignals, ProvenanceMap)`` where the provenance map keys
        each ``risk_signals.<field>`` to a deterministic, fully-confident,
        tape-sourced :class:`FieldProvenance` whose citation carries the RREL
        anchor — the locator mechanism the canonical schema fixes (decision D8).
        """
        pool_balance = max(0.0, tape.pool_balance_eur)
        wa_ltv = float(tape.pool_stats.get("wtd_ltv", 0.0))

        arrears = tape.arrears_breakdown
        default_pct = float(arrears.get("default_pct", 0.0))
        arrears_180d_pct = float(arrears.get("arrears_180d_plus_pct", 0.0))

        default_balance = _pct_to_balance(pool_balance, default_pct)
        arrears_180d_balance = _pct_to_balance(pool_balance, arrears_180d_pct)
        # ≥90d-arrears *balance* ⊇ ≥180d-arrears ∪ defaulted (no finer bucket on
        # the tape). The two buckets are mutually exclusive in the normaliser's
        # priority scheme (a defaulted loan is not also counted as 180+d), so the
        # union is their sum.
        arrears_90d_balance = arrears_180d_balance + default_balance
        # ``default_pct`` is the defaulted *fraction* of the pool (0–1), per the
        # field's documented contract — NOT a balance like the arrears fields.
        default_fraction = max(0.0, default_pct) / 100.0

        signals = RiskSignals(
            arrears_90d=arrears_90d_balance,
            arrears_180d=arrears_180d_balance,
            wa_ltv=wa_ltv,
            default_pct=default_fraction,
            pool_balance=pool_balance,
        )

        def _prov(code: str, excerpt: str) -> FieldProvenance:
            return FieldProvenance(
                source="tape",
                method="deterministic",
                confidence=1.0,
                citation=Citation(
                    document=tape.transaction_name or "ESMA loan tape",
                    page_or_row=code,
                    excerpt=excerpt,
                ),
            )

        provenance: ProvenanceMap = {
            "risk_signals.pool_balance": _prov(
                _RREL_BALANCE, "Outstanding pool balance (sum of current_balance)."
            ),
            "risk_signals.wa_ltv": _prov(
                _RREL_LTV, "Balance-weighted current loan-to-value of the pool."
            ),
            "risk_signals.default_pct": _prov(
                _RREL_DEFAULT,
                "Defaulted balance (default_crr_flag) as a pool balance.",
            ),
            "risk_signals.arrears_180d": _prov(
                _RREL_ARREARS, "Balance >=180 days in arrears (arrears bucket)."
            ),
            "risk_signals.arrears_90d": _prov(
                _RREL_ARREARS,
                "Balance >=90 days in arrears — conservative >=180d union "
                "defaulted (no distinct >=90d bucket on the tape).",
            ),
        }
        return signals, provenance

    @staticmethod
    def legs_from_collections(collections: CollectionsOutput) -> CollectionLegs:
        """Map a :class:`CollectionsOutput` onto canonical :class:`CollectionLegs`.

        One-to-one with the five separated legs the aggregator already produces
        (and with ``CollectionsOutput.to_period_collections``), so the legs sum
        to the same aggregates and ``_normalize_period`` reduces this tape-source
        ``PeriodInputs`` to the identical ``PeriodCollections`` the legacy tape
        path produced.
        """
        return CollectionLegs(
            interest=collections.interest_collected,
            scheduled_principal=collections.scheduled_principal,
            prepayment=collections.unscheduled_principal,
            recovery=collections.recoveries,
            realized_loss=collections.realized_losses,
        )

    def period_inputs(
        self,
        collections: CollectionsOutput,
        tape: EsmaTapeOutput | None,
        *,
        reporting_date: str,
        days_in_period: int,
    ) -> PeriodInputs:
        """Build one canonical ``source="tape"`` :class:`PeriodInputs`.

        Carries the collection ``legs`` (from ``collections``) and, when the
        normalised tape analytics are available (``tape`` is not ``None``), a
        populated ``risk_signals`` (from ``tape``) with ESMA provenance — with
        the available revenue / principal aggregates and the period's realized
        loss. No step-overrides (the tape path computes every waterfall step
        in-engine), so the canonical inputs reduce — via ``_normalize_period``'s
        tape-legs branch — to the exact same engine behaviour as the legacy
        ``PeriodInput``.

        ``tape`` is ``None`` only when the period's pool analytics could not be
        resolved (no seed / no network); ``risk_signals`` is then left ``None``.
        Honest degradation: the numeric fold (driven by ``legs``) is unaffected —
        the RiskSignals enrichment is provenance the engine does not consume — so
        a missing tape never breaks the reconstruction, it only omits that
        period's risk signals.
        """
        legs = self.legs_from_collections(collections)
        signals: RiskSignals | None = None
        provenance: ProvenanceMap = {}
        if tape is not None:
            signals, provenance = self.risk_signals_from_tape(tape)
        return PeriodInputs(
            reporting_date=reporting_date,
            days_in_period=days_in_period,
            available_revenue=collections.available_revenue_funds,
            available_principal=collections.available_principal_funds,
            realized_loss=max(0.0, collections.realized_losses),
            legs=legs,
            risk_signals=signals,
            source="tape",
            provenance=provenance,
        )
