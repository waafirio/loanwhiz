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

from loanwhiz.domain.rules import RecipientType
from loanwhiz.primitives.waterfall_interpreter import StepSpec, _canonical_recipient

#: The interpreter recipients whose need the registry COMPUTES from the deal
#: model alone (no report input). Reconciling these to the cent is the headline
#: independent check. Everything else in a revenue waterfall is report-supplied.
#:
#: This is the authored *declaration*, pinned as a subset of the need contract by
#: ``tests/test_recipient_need_contract.py``. It is deliberately **not** what the
#: classifier tests against — see :data:`_ENGINE_COMPUTED_CANONICAL`. Adding a
#: deal's own spelling here would encode a second vocabulary in a table that
#: should only ever name recipients the registry can compute, which is precisely
#: the whack-a-mole #503 removed.
ENGINE_COMPUTED_RECIPIENTS: frozenset[str] = frozenset(
    {
        "class_a_interest",
        "class_b_interest",
        "class_c_interest",
        "class_a_pdl_replenishment",
        "class_b_pdl_replenishment",
        "class_c_pdl_replenishment",
        "reserve_account_replenishment",
        "reserve_replenishment",
    }
)


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
    ``unmapped`` today, so an inline guard would have been correct and
    permanently unexercised.
    """
    return frozenset(
        resolved
        for resolved in map(_canonical_recipient, names)
        if resolved is not None and resolved is not RecipientType.unmapped
    )


#: :data:`ENGINE_COMPUTED_RECIPIENTS` resolved into the canonical vocabulary —
#: what the classifier actually tests a step against.
#:
#: **Both sides resolve, not just the step.** The declaration above is itself
#: mixed: ``class_{a,b,c}_pdl_replenishment`` and ``reserve_account_replenishment``
#: are legacy spellings whose canonical forms (``class_*_pdl_cure``,
#: ``reserve_replenishment``) do not appear in it. Canonicalising only the
#: incoming recipient would therefore have flipped those RMBS steps from
#: ``engine`` to ``report-supplied`` while fixing the CLO ones.
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
