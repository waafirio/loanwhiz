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

Pure & dependency-light: depends only on
:class:`~loanwhiz.primitives.waterfall_interpreter.StepSpec`.
"""

from __future__ import annotations

from loanwhiz.primitives.waterfall_interpreter import StepSpec

#: The interpreter recipients whose need the registry COMPUTES from the deal
#: model alone (no report input). Reconciling these to the cent is the headline
#: independent check. Everything else in a revenue waterfall is report-supplied.
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
    otherwise ``"engine"`` when its recipient is in
    :data:`ENGINE_COMPUTED_RECIPIENTS` **and** its label is *not* in
    ``report_supplied_labels`` (a label override forces report-supplied even for an
    otherwise-computable recipient); otherwise ``"report-supplied"``, with the
    amount pulled from ``report_amounts``.
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
        elif recipient in ENGINE_COMPUTED_RECIPIENTS and label not in report_supplied_labels:
            source[recipient] = "engine"
        else:
            source[recipient] = "report-supplied"
            overrides[recipient] = report_amounts.get(label, 0.0)
    return specs, overrides, source
