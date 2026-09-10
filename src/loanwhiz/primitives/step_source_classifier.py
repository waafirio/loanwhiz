"""Shared step-source classifier — ONE classifier for the engine slice (#266).

A real deal's published distribution mixes three kinds of waterfall line, and the
engine must label each one honestly so a reconciliation never manufactures a
false 100%:

- **engine** — a *formulaic* recipient the prospectus lets the engine compute
  from balances and rates (Class A interest, PDL-replenishment, reserve top-up).
  The interpreter's :data:`~loanwhiz.primitives.waterfall_interpreter.NEED_CALCULATORS`
  registry derives these with **no report input**; reconciling them to the cent
  is the independent part of the proof.
- **report-supplied** — a *servicer-actual* recipient with **no prospectus
  formula** (swap payments, the pari-passu fee bucket, the issuer-expense-account
  top-up). Its amount comes from the servicer's books, so it is taken from the
  report (``need_overrides``) and the engine is proven only to *route* it in the
  right priority order out of the right pot.
- **residual** — the terminal "whatever remains" sweep (e.g. "any Deferred
  Purchase Price Instalment to the Seller"), distributed as exactly what is left
  in the pot.

This module is the single source of that classification. It was extracted out of
the validation harness's private ``_build_specs`` (epic #257, design spec
``docs/superpowers/specs/2026-06-20-cold-start-edw-deal-engine-design.md`` →
"Migration sequence" item 2) so the live path (the ``ReportAdapter`` /
``run_period`` of the engine slice) and the validation harness share **one**
classifier and cannot drift.

The classifier preserves the harness's exact ``"engine" / "report-supplied" /
"residual"`` vocabulary. Translating to the canonical
:class:`~loanwhiz.domain.inputs.PeriodInputs` ``step_sources`` spelling
(``"reported"``) is the *adapter's* concern, deliberately kept out of this pure
kernel.

**The classification is decided in ONE vocabulary** (#511). An extracted step
carries the *document's* spelling — a CLO prospectus writes "Class A Notes
Interest" (``class_a_notes_interest``) where the enum says ``class_a_interest``
— so the raw string is resolved through
:func:`~loanwhiz.primitives.waterfall_interpreter._canonical_recipient`, the one
bridge #503 built, before it is tested. Testing the raw string sent every CLO
step to ``report-supplied``, where its need was overwritten with the report's
own figure: that is how #496 found the report reconciled against itself.

Pure & dependency-light: depends only on
:class:`~loanwhiz.primitives.waterfall_interpreter.StepSpec`, that resolver,
and the :class:`~loanwhiz.domain.rules.RecipientType` enum it resolves into.
"""

from __future__ import annotations

from loanwhiz.domain.rules import (
    RECIPIENT_NEED_SOURCE,
    AmountBasis,
    NeedSource,
    RecipientType,
    basis_for,
)
from loanwhiz.primitives.waterfall_interpreter import StepSpec, _canonical_recipient

#: Bases whose calculator is registered but whose **input no producer supplies**,
#: so the step's figure is not something the engine derived from the deal.
#:
#: A registered calculator is necessary for "the engine computes this" and not
#: sufficient. Both bases here read a field that exists on the funds and that
#: nothing in the codebase ever writes, and they fail differently — which is why
#: the property is stated as "the input is unsupplied" rather than as either
#: symptom:
#:
#: - ``fee_accrual`` reads ``WaterfallFunds.fee_rates_pct``, an empty dict by
#:   default. ``_make_collateral_fee_need`` returns ``None``, so ``compute_need``
#:   records ``input_unavailable`` and the step **refuses** — loudly. Crediting it
#:   would also drop the report figure the step needs and silently under-distribute
#:   the cascade by the whole fee.
#: - ``deferred_interest_balance`` reads ``TrancheFunds.deferred_interest_balance``,
#:   ``0.0`` by default. It does **not** refuse: it returns a confident ``0.00``
#:   that ties against a published ``0.00``. That is the more dangerous of the two,
#:   because a step which agrees with the report only because both sides are the
#:   default reads exactly like one the engine got right (#496).
#:
#: Neither is a judgement about the formula, which is correct and would compute the
#: moment a deal seeded its input. ``test_step_source_classifier`` asserts each
#: excluded basis really is unsupplied across the committed folds, so the day a
#: producer starts writing one of these fields that test reds and its line here
#: must go — the exclusion cannot outlive its reason.
_UNSUPPLIED_BASES: frozenset[AmountBasis] = frozenset(
    {"fee_accrual", "deferred_interest_balance"}
)


def _engine_computed_declaration() -> frozenset[str]:
    """The recipients the engine computes, derived from the need contract (#598).

    **Membership is derived, never authored.** This was a hand-written frozenset
    of eight spellings that stopped at ``class_c_interest``, so a deal with a
    stack deeper than three classes had its Class D/E/F interest graded
    ``report-supplied`` — compared against the report's own figure — even though
    the registry computed each one to the cent from a seeded balance, a published
    applied rate and a parsed day count. The list did not describe the engine; it
    described the three-tranche RMBS the list was written for, and every new class
    was another name somebody had to remember to add.

    So ask what makes a recipient engine-computed and read the answer off the two
    declarations that already state it:

    - :data:`~loanwhiz.domain.rules.RECIPIENT_NEED_SOURCE` says where the need
      comes from. :attr:`~loanwhiz.domain.rules.NeedSource.calculator` is exactly
      "an engine formula over deal data" — which is the property. The other four
      members are all report- or allocation-fed and must stay out:
      ``funds_input`` is registry-backed but the *number* is the servicer's,
      ``allocation`` is supplied by ``allocate_principal``, ``step_override``
      has no formula at all, and ``residual`` is the terminal sweep.
    - :func:`~loanwhiz.domain.rules.basis_for` says which formula, which is how
      :data:`_UNSUPPLIED_BASES` removes the ones that cannot run.

    Both are exhaustive over :class:`~loanwhiz.domain.rules.RecipientType` and
    asserted total at import, so a class added to the enum is credited the moment
    its need source says ``calculator`` — with no edit here.
    """
    return frozenset(
        r.value
        for r, src in RECIPIENT_NEED_SOURCE.items()
        if src is NeedSource.calculator and basis_for(r) not in _UNSUPPLIED_BASES
    )


#: The recipients whose need the registry COMPUTES from the deal model alone (no
#: report input). Reconciling these to the cent is the headline independent check.
#:
#: Derived by :func:`_engine_computed_declaration`, in the **canonical** spelling
#: by construction — so, unlike the authored set it replaces, it can no longer
#: hold a legacy spelling whose canonical form is absent from it (#511). The
#: resolution below is kept all the same: it is what makes that property hold
#: rather than merely happen to hold, and ``is_engine_computed`` still has to
#: resolve the *incoming* side.
ENGINE_COMPUTED_RECIPIENTS: frozenset[str] = _engine_computed_declaration()


def _canonical_view(names: frozenset[str]) -> frozenset[RecipientType]:
    """``names`` resolved into the canonical vocabulary, minus what cannot compute.

    Two strings are dropped, and the second is the subtle one:

    - a name nobody declared (``_canonical_recipient`` answers ``None``); and
    - a name we **recognise and have decided the engine cannot place**, which
      answers :attr:`~loanwhiz.domain.rules.RecipientType.unmapped`. Every string
      in ``RECOGNISED_UNEVALUABLE_RECIPIENTS`` collapses onto that single member,
      so admitting it once would classify *all* of them ``engine`` at a stroke —
      the exact inversion of what they mean (#503 named them to keep "we cannot
      place this" distinguishable from "nobody spelled this").

    Written as a function rather than inlined so that second rule has a seam a
    test can reach: no member of :data:`ENGINE_COMPUTED_RECIPIENTS` resolves to
    ``unmapped`` — since #598 it cannot, the derivation being keyed by
    ``RecipientType`` — so an inline guard would be correct and permanently
    unexercised. ``test_canonical_view_drops_unplaceable_and_undeclared_names``
    feeds this seam the strings the real declaration no longer contains.
    """
    return frozenset(
        resolved
        for resolved in map(_canonical_recipient, names)
        if resolved is not None and resolved is not RecipientType.unmapped
    )


#: :data:`ENGINE_COMPUTED_RECIPIENTS` resolved into the canonical vocabulary —
#: what the classifier actually tests a step against.
#:
#: **Both sides resolve, not just the step**, and that is now a property rather
#: than a repair. While the declaration was authored it was itself *mixed* —
#: ``class_{a,b,c}_pdl_replenishment`` and ``reserve_account_replenishment`` were
#: legacy spellings whose canonical forms (``class_*_pdl_cure``,
#: ``reserve_replenishment``) were absent from it, so canonicalising only the
#: incoming recipient would have flipped those RMBS steps from ``engine`` to
#: ``report-supplied`` while fixing the CLO ones (#511). Deriving the declaration
#: off ``RECIPIENT_NEED_SOURCE`` retires that whole failure: its keys are
#: :class:`~loanwhiz.domain.rules.RecipientType` members, so there is no second
#: vocabulary left to disagree with. The resolution stays because it is what keeps
#: the two sides comparable no matter how the declaration is spelled, and because
#: a legacy or CLO spelling still arrives on the *incoming* side every period.
#:
#: :attr:`~loanwhiz.domain.rules.RecipientType.unmapped` is excluded on purpose —
#: see :func:`_canonical_view`.
_ENGINE_COMPUTED_CANONICAL: frozenset[RecipientType] = _canonical_view(
    ENGINE_COMPUTED_RECIPIENTS
)


def is_engine_computed(recipient: str) -> bool:
    """Does the engine derive this recipient's need from the deal model itself?

    The **one** membership test over :data:`ENGINE_COMPUTED_RECIPIENTS`, resolved
    into the canonical vocabulary first. Public because it has three callers and
    every raw copy of it is a reader that disagrees: the classifier below
    (the engine's input side), and ``reconciler._source_of`` (the reconciliation's
    output label). Before #514 the reconciler kept its own raw-string copy, so a
    CLO step whose need the fold had genuinely computed was still *reported* as
    ``report-supplied`` and ``engine_computed_passed`` read zero — #503's "two
    readers disagree" arriving one layer further out.
    """
    return _canonical_recipient(recipient) in _ENGINE_COMPUTED_CANONICAL


def build_step_specs(
    steps: list[dict],
    *,
    residual_label: str,
    report_supplied_labels: frozenset[str],
    report_amounts: dict[str, float],
) -> tuple[list[StepSpec], dict[str, float], dict[str, str]]:
    """Build interpreter specs from extracted steps + classify each step's source.

    Returns ``(specs, need_overrides, source_by_recipient)``:

    - ``specs`` — one :class:`StepSpec` per extracted step, with the terminal
      ``residual_label`` step flagged ``residual=True`` and any extracted
      conditions cleared (a report's published distribution already reflects the
      conditions' resolution, so re-gating here would double-count). Pass an empty
      ``residual_label`` to disable the residual flag entirely (the redemption
      case, where the report leaves a documented unapplied-rounding remainder
      rather than sweeping the pot).
    - ``need_overrides`` — ``recipient -> report amount`` for the report-supplied
      steps (the interpreter has no formula for them).
    - ``source_by_recipient`` — ``recipient -> 'engine'|'report-supplied'|'residual'``.

    A step is classified ``"residual"`` when its label equals ``residual_label``;
    otherwise ``"engine"`` when its recipient **resolves** — through
    :func:`~loanwhiz.primitives.waterfall_interpreter._canonical_recipient` — to a
    member of :data:`_ENGINE_COMPUTED_CANONICAL` **and** its label is *not* in
    ``report_supplied_labels`` (a label override forces report-supplied even for an
    otherwise-computable recipient); otherwise ``"report-supplied"``, with the
    amount pulled from ``report_amounts``.

    Resolving is what lets a deal spelling its cascade in the document's own words
    reach the registry at all; a recipient nobody spelled, or one we recognise and
    have decided the engine cannot place, still lands in ``"report-supplied"``.

    Both returned dicts stay keyed by the **raw** extracted recipient — only the
    membership test canonicalises. The interpreter looks a step's override up by
    ``spec.recipient``, so re-keying here would silently detach every override
    from its step.
    """
    specs: list[StepSpec] = []
    overrides: dict[str, float] = {}
    source: dict[str, str] = {}
    for step in steps:
        label = str(step.get("priority", ""))
        recipient = str(step.get("recipient", ""))
        residual = label == residual_label
        # Clear conditions: the report is the post-resolution actual; the
        # interpreter's prose-condition evaluator must not re-suppress a step the
        # report already paid (or zeroed).
        spec = StepSpec(priority=label, recipient=recipient, residual=residual)
        specs.append(spec)
        if residual:
            source[recipient] = "residual"
        elif is_engine_computed(recipient) and label not in report_supplied_labels:
            source[recipient] = "engine"
        else:
            source[recipient] = "report-supplied"
            overrides[recipient] = report_amounts.get(label, 0.0)
    return specs, overrides, source
