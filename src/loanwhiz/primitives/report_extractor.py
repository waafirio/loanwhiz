"""General, governed report extractor (#271).

One extractor that produces a typed :class:`ParsedReport` across arbitrary
issuer report layouts, with per-field provenance, returned in the governed
:class:`~loanwhiz.primitives.base.PrimitiveResult` envelope.

Mechanism — **hybrid** (locked decision in
``docs/superpowers/specs/2026-06-20-report-extractor-design.md``):

- A small **format registry** of ``(matches(text) -> bool, parse(...) ->
  PrimitiveResult[ParsedReport])`` entries. The existing deterministic
  :mod:`~loanwhiz.primitives.notes_cash_parser` (Green Lion "Notes & Cash"
  Bond Report layout) is the **first registered entry** — free, CI-stable,
  reconciles to the cent.
- When **no registered format matches**, fall back to **Docling/OCR → LLM
  structured-output**: the model fills the :class:`ParsedReport` JSON shape,
  the result is validated (retried once on validation failure), and each
  field carries a model-reported confidence + a citation verified against the
  source span. The general path for any issuer.
- Order: **deterministic-first, LLM-second** — a deterministic hit
  short-circuits the LLM (and its cost + nondeterminism).

Determinism for CI: parsed reports are **cached** keyed by a hash of the
report bytes / URL (mirrors the Docling + ``report_verifier`` caches), so the
fast suite reads the cache and never hits the live LLM. For the Green Lion
deal the deterministic parser already gives a reproducible parse for free; the
cache matters for the LLM path on other deals.

Separation of concerns (spec §"Where it sits"): this extractor owns only
``report PDF -> ParsedReport``. Turning a :class:`ParsedReport` into the
engine's ``(seed, PeriodInputs[])`` is the :class:`ReportAdapter`'s job
(already built in #267); reconciliation-as-gate is #272. To keep the
deterministic path adapter-compatible without modifying #267's adapter, a
:meth:`ParsedReport.to_notes_cash_report` bridge reconstructs the concrete
``NotesCashReport`` the adapter already consumes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from loanwhiz.domain.provenance import FieldProvenance, ProvenanceMap
from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.notes_cash_parser import (
    NoteClassBalance,
    NotesCashPeriod,
    NotesCashReport,
    PoPStep,
    TriggerState,
    parse_report_text,
)
from loanwhiz.primitives.registry import register_primitive

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PRIMITIVE_NAME = "report_extractor"
_PRIMITIVE_VERSION = "0.1.0"
_DETERMINISTIC_CONFIDENCE = 1.0

#: Durable cache for parsed reports — sibling of the notes-cash + Docling
#: caches under the repo's ``data/extraction_cache/``.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXTRACTION_CACHE_DIR = _REPO_ROOT / "data" / "extraction_cache"

#: Confidence floor applied to an LLM-extracted field whose citation locator
#: could not be verified against the source span (spec §Governance — "an
#: unverifiable citation drops confidence").
_UNVERIFIED_CITATION_CONFIDENCE_CAP = 0.5


# ===========================================================================
# ParsedReport schema (the extractor's output)
# ===========================================================================
#
# A general typed model — generalizes today's ``NotesCashReport``. Every field
# is optional (a report carries what it carries); every extracted value is
# provenanced via the sidecar ``ProvenanceMap`` keyed by dotted field path.


class NoteBalance(BaseModel):
    """One note class's structural balances for one period (all optional)."""

    note_class: str = Field(..., description="Canonical class key, e.g. 'class_a'.")
    opening: float | None = Field(
        default=None, description="Opening (period-start) note balance (EUR)."
    )
    closing: float | None = Field(
        default=None, description="Closing (post-payment) note balance (EUR)."
    )
    principal_paid: float | None = Field(
        default=None, description="Principal repaid to this class this period (EUR)."
    )
    interest_paid: float | None = Field(
        default=None, description="Interest paid to this class this period (EUR)."
    )
    pdl: float | None = Field(
        default=None, description="Principal Deficiency Ledger balance after payment (EUR)."
    )


class ReportedStep(BaseModel):
    """One priority-of-payments step as printed in the report."""

    priority_label: str = Field(..., description="Priority label, e.g. '(d)'.")
    recipient: str | None = Field(
        default=None, description="Step description / recipient as printed."
    )
    amount: float | None = Field(
        default=None, description="Amount distributed at this step this period (EUR)."
    )


class ReportedTrigger(BaseModel):
    """One reported trigger / event and its printed breach state."""

    label: str = Field(..., description="Trigger label / row id, e.g. '(a)'.")
    description: str | None = Field(default=None, description="Condition text as printed.")
    breached: bool | None = Field(
        default=None, description="Reported breach state, if printed."
    )
    required_value: float | None = Field(default=None, description="Printed threshold, if numeric.")
    current_value: float | None = Field(default=None, description="Printed observed value, if numeric.")


class ParsedReportPeriod(BaseModel):
    """One reporting period's extracted figures (every field optional)."""

    reporting_date: str = Field(..., description="ISO reporting date (the period key).")
    # opening / closing structural figures (for the period-0 seed, B5)
    note_balances: list[NoteBalance] = Field(
        default_factory=list, description="Per-class structural balances."
    )
    reserve_balance: float | None = Field(default=None, description="Reserve account balance (EUR).")
    reserve_target: float | None = Field(default=None, description="Reserve account target (EUR).")
    reserve_drawings: float | None = Field(
        default=None, description="Reserve drawings taken this period (EUR)."
    )
    pool_balance: float | None = Field(default=None, description="Pool / portfolio balance (EUR).")
    # available funds + the actual PoP the report published
    available_revenue: float | None = Field(default=None, description="Total available revenue funds (EUR).")
    available_principal: float | None = Field(default=None, description="Total available principal funds (EUR).")
    revenue_pop: list[ReportedStep] = Field(
        default_factory=list, description="Revenue priority-of-payments, as printed."
    )
    redemption_pop: list[ReportedStep] = Field(
        default_factory=list, description="Redemption priority-of-payments, as printed."
    )
    triggers: list[ReportedTrigger] = Field(
        default_factory=list, description="Reported trigger / breach states."
    )

    def note_balance(self, note_class: str) -> NoteBalance | None:
        """The :class:`NoteBalance` for one class, or ``None``."""
        return next((b for b in self.note_balances if b.note_class == note_class), None)


class ParsedReport(BaseModel):
    """A general, typed, provenanced report — the extractor's output.

    Generalizes :class:`~loanwhiz.primitives.notes_cash_parser.NotesCashReport`
    across arbitrary issuer layouts. ``provenance`` is the sidecar
    :data:`~loanwhiz.domain.provenance.ProvenanceMap` keyed by dotted field
    path (e.g. ``"periods.0.reserve_balance"``).
    """

    deal_name: str = Field(..., description="Deal name as printed / supplied.")
    report_type: Literal["notes_and_cash", "investor_report", "unknown"] = Field(
        default="unknown", description="Report family."
    )
    periods: list[ParsedReportPeriod] = Field(
        default_factory=list, description="Periods, sorted by reporting_date."
    )
    provenance: ProvenanceMap = Field(
        default_factory=dict, description="Per dotted-field-path provenance sidecar."
    )
    extraction_method: Literal["deterministic", "ocr+llm"] = Field(
        default="deterministic", description="How this report was extracted."
    )

    @property
    def reporting_dates(self) -> list[str]:
        """The ISO reporting dates present, in order."""
        return [p.reporting_date for p in self.periods]

    def period_for(self, reporting_date: str) -> ParsedReportPeriod | None:
        """Look a period up by its ISO reporting date, or ``None``."""
        return next((p for p in self.periods if p.reporting_date == reporting_date), None)

    # -- adapter bridge -----------------------------------------------------

    def to_notes_cash_report(self) -> NotesCashReport:
        """Reconstruct the concrete ``NotesCashReport`` the #267 adapter consumes.

        Lets the existing :class:`~loanwhiz.primitives.report_adapter.ReportAdapter`
        seed + build ``PeriodInputs`` from this general report **without modifying
        the adapter** (spec §"Where it sits": #271 owns extract, #267 owns adapt).
        Maps the general optional fields back onto the adapter's expected shape;
        the reserve account is rebuilt as the single ``reserve_account`` the
        adapter looks up.
        """
        from loanwhiz.primitives.notes_cash_parser import IssuerAccount

        periods: list[NotesCashPeriod] = []
        for p in self.periods:
            note_balances = [
                NoteClassBalance(
                    note_class=nb.note_class,
                    principal_balance_after_payment=nb.closing,
                    total_principal_payments=nb.principal_paid,
                    total_interest_payments=nb.interest_paid,
                    pdl_balance_after_payment=nb.pdl,
                )
                for nb in p.note_balances
            ]
            issuer_accounts: list[IssuerAccount] = []
            if (
                p.reserve_balance is not None
                or p.reserve_target is not None
                or p.reserve_drawings is not None
            ):
                issuer_accounts.append(
                    IssuerAccount(
                        name="reserve_account",
                        balance_end=p.reserve_balance,
                        target=p.reserve_target,
                        drawings=p.reserve_drawings,
                    )
                )
            periods.append(
                NotesCashPeriod(
                    reporting_date=p.reporting_date,
                    period_label=p.reporting_date,
                    deal_name=self.deal_name,
                    note_balances=note_balances,
                    revenue_pop=[
                        PoPStep(
                            priority=s.priority_label,
                            recipient=s.recipient or "",
                            amount=s.amount or 0.0,
                        )
                        for s in p.revenue_pop
                    ],
                    redemption_pop=[
                        PoPStep(
                            priority=s.priority_label,
                            recipient=s.recipient or "",
                            amount=s.amount or 0.0,
                        )
                        for s in p.redemption_pop
                    ],
                    available_revenue_funds=p.available_revenue,
                    available_principal_funds=p.available_principal,
                    issuer_accounts=issuer_accounts,
                    triggers=[
                        TriggerState(
                            label=t.label,
                            description=t.description or "",
                            breached=bool(t.breached),
                            required_value=t.required_value,
                            current_value=t.current_value,
                            status="Breached" if t.breached else "OK",
                        )
                        for t in p.triggers
                    ],
                )
            )
        return NotesCashReport(deal_name=self.deal_name, periods=periods)


# ===========================================================================
# Input schema
# ===========================================================================


class ReportExtractInput(BaseInput):
    """Input for the governed report extractor.

    Exactly one of ``text`` (already-extracted report text — the unit-tested
    seam) or ``url`` (a report PDF to fetch + OCR) must be supplied. ``deal_name``
    is used for the deal label and as part of the cache key.
    """

    deal_name: str = Field(..., description="Deal name (label + cache-key component).")
    text: str | None = Field(
        default=None, description="Already-extracted report text (offline seam)."
    )
    url: str | None = Field(default=None, description="Report PDF URL to fetch + OCR.")


# ===========================================================================
# Format registry
# ===========================================================================


@dataclass(frozen=True)
class ReportFormat:
    """One registered report format: recognizer + deterministic parser.

    Attributes:
        name:    Short identifier for the format (audit / logging).
        matches: ``text -> bool`` — recognizes this format from extracted text.
        parse:   ``(text, deal_name) -> PrimitiveResult[ParsedReport]`` —
                 deterministic parse. Called only after ``matches`` returns True.
    """

    name: str
    matches: Callable[[str], bool]
    parse: Callable[[str, str], PrimitiveResult[ParsedReport]]


# --- first deterministic entry: Green Lion "Notes & Cash" Bond Report -------

#: Layout markers that identify a Green Lion-style Notes & Cash report. All
#: must be present — keeps the recognizer from false-positiving on a thin
#: investor report that happens to mention "Bond Report".
_GL_NOTES_CASH_MARKERS: tuple[str, ...] = (
    "notes and cash report",
    "bond report",
    "revenue priority of payments",
)


def _matches_gl_notes_cash(text: str) -> bool:
    """Recognize the Green Lion Notes & Cash Bond Report layout from text."""
    low = text.lower()
    return all(marker in low for marker in _GL_NOTES_CASH_MARKERS)


def _notes_cash_period_to_parsed(period: NotesCashPeriod) -> ParsedReportPeriod:
    """Map a deterministic :class:`NotesCashPeriod` onto :class:`ParsedReportPeriod`."""
    return ParsedReportPeriod(
        reporting_date=period.reporting_date,
        note_balances=[
            NoteBalance(
                note_class=nb.note_class,
                closing=nb.principal_balance_after_payment,
                principal_paid=nb.total_principal_payments,
                interest_paid=nb.total_interest_payments,
                pdl=nb.pdl_balance_after_payment,
            )
            for nb in period.note_balances
        ],
        reserve_balance=period.reserve_balance,
        reserve_target=period.reserve_target,
        reserve_drawings=(
            acct.drawings
            if (acct := period.account("reserve_account")) is not None
            else None
        ),
        available_revenue=period.available_revenue_funds,
        available_principal=period.available_principal_funds,
        revenue_pop=[
            ReportedStep(priority_label=s.priority, recipient=s.recipient, amount=s.amount)
            for s in period.revenue_pop
        ],
        redemption_pop=[
            ReportedStep(priority_label=s.priority, recipient=s.recipient, amount=s.amount)
            for s in period.redemption_pop
        ],
        triggers=[
            ReportedTrigger(
                label=t.label,
                description=t.description,
                breached=t.breached,
                required_value=t.required_value,
                current_value=t.current_value,
            )
            for t in period.triggers
        ],
    )


def _deterministic_provenance(report: ParsedReport, citation: Citation) -> ProvenanceMap:
    """Per-field provenance for a deterministic parse — every extracted field at 1.0."""
    prov: ProvenanceMap = {}
    fp = lambda: FieldProvenance(  # noqa: E731 - tiny local factory, deliberate
        source="report",
        method="deterministic",
        confidence=_DETERMINISTIC_CONFIDENCE,
        citation=citation,
    )
    for i, period in enumerate(report.periods):
        base = f"periods.{i}"
        if period.reserve_balance is not None:
            prov[f"{base}.reserve_balance"] = fp()
        if period.reserve_target is not None:
            prov[f"{base}.reserve_target"] = fp()
        if period.available_revenue is not None:
            prov[f"{base}.available_revenue"] = fp()
        if period.available_principal is not None:
            prov[f"{base}.available_principal"] = fp()
        for j, nb in enumerate(period.note_balances):
            for fld in ("opening", "closing", "principal_paid", "interest_paid", "pdl"):
                if getattr(nb, fld) is not None:
                    prov[f"{base}.note_balances.{j}.{fld}"] = fp()
    return prov


def _parse_gl_notes_cash(text: str, deal_name: str) -> PrimitiveResult[ParsedReport]:
    """Deterministic parse of a Green Lion Notes & Cash report (the first entry).

    Reuses the existing deterministic :func:`parse_report_text`, then maps the
    concrete period onto the general :class:`ParsedReport` with per-field
    provenance at ``confidence=1.0``.
    """
    t0 = time.perf_counter()
    period = parse_report_text(text, period_label=deal_name)
    parsed_period = _notes_cash_period_to_parsed(period)
    report = ParsedReport(
        deal_name=period.deal_name or deal_name,
        report_type="notes_and_cash",
        periods=[parsed_period],
        extraction_method="deterministic",
    )
    citation = Citation(
        document=f"{report.deal_name} — Notes & Cash Report",
        page_or_row=period.reporting_date,
        excerpt=(
            "Liability actuals parsed deterministically from the extracted Notes & "
            "Cash report text (Bond Report, Priority of Payments, Issuer Accounts, "
            "Triggers)."
        ),
    )
    report.provenance = _deterministic_provenance(report, citation)
    duration_ms = (time.perf_counter() - t0) * 1000.0

    audit = AuditEntry.now(
        primitive_name=_PRIMITIVE_NAME,
        version=_PRIMITIVE_VERSION,
        input_hash=hashlib.sha256(text.encode()).hexdigest(),
        duration_ms=duration_ms,
    )
    return PrimitiveResult[ParsedReport](
        output=report,
        confidence=_DETERMINISTIC_CONFIDENCE,
        citations=[citation],
        audit_entry=audit,
    )


#: The format registry — deterministic-first. Append a new ``ReportFormat`` to
#: add a fast-path for another issuer (spec: optional + incremental; the LLM
#: path covers everything until a deterministic parser is chosen). The Green
#: Lion Notes & Cash parser is the first (and currently only) entry.
FORMAT_REGISTRY: list[ReportFormat] = [
    ReportFormat(
        name="green_lion_notes_cash",
        matches=_matches_gl_notes_cash,
        parse=_parse_gl_notes_cash,
    )
]


def match_format(text: str) -> ReportFormat | None:
    """Return the first registered format whose ``matches`` accepts ``text``."""
    for fmt in FORMAT_REGISTRY:
        try:
            if fmt.matches(text):
                return fmt
        except Exception as exc:  # a buggy recognizer must not break dispatch
            logger.warning("Format recognizer %r raised: %s", fmt.name, exc)
    return None


# ===========================================================================
# Determinism cache (LLM path reproducibility for CI)
# ===========================================================================


def _cache_key(input: ReportExtractInput) -> str:
    """Stable cache key — SHA-256 of the deal name + the report bytes/URL."""
    basis = input.text if input.text is not None else (input.url or "")
    return hashlib.sha256(f"{input.deal_name}\x00{basis}".encode()).hexdigest()


def _cache_path(key: str, cache_dir: str | Path) -> Path:
    return Path(cache_dir) / f"parsed-report-{key}.json"


def _load_cache(path: Path) -> ParsedReport | None:
    if not path.exists():
        return None
    try:
        return ParsedReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("Parsed-report cache read failed (%s): %s", path, exc)
        return None


def _write_cache(report: ParsedReport, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Parsed-report cache write failed (%s): %s", path, exc)


# ===========================================================================
# LLM (Docling/OCR → structured-output) fallback
# ===========================================================================

#: A client matching the subset of the google-genai surface the LLM path uses
#: (``client.models.generate_content(...) -> response.text``). Injectable so
#: the fast suite never reaches the real network (mirrors report_verifier).
LlmClient = Any


def _llm_prompt(deal_name: str) -> str:
    """Build the structured-output prompt asking for the ParsedReport JSON shape."""
    schema = json.dumps(ParsedReport.model_json_schema(), indent=2)
    return (
        "You are a structured-finance analyst. Extract the investor / notes & cash "
        f"report for the deal {deal_name!r} into a single JSON object matching this "
        "JSON Schema. Every field is optional — emit only what the document states; "
        "omit (or null) anything absent. For every numeric value you emit, also emit "
        "a provenance entry under `provenance` keyed by the value's dotted field path "
        "(e.g. `periods.0.reserve_balance`) with `source`='report', `method`='ocr+llm', "
        "a `confidence` in [0,1] reflecting your certainty, and a `citation` "
        "{document, page_or_row, excerpt} pointing at the exact text the value came "
        "from. Set `extraction_method` to 'ocr+llm'.\n\n"
        "Return ONLY valid JSON — no markdown fences, no commentary.\n\n"
        f"JSON Schema:\n{schema}"
    )


def _strip_fences(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s*```$", "", raw)


def _verify_citations(report: ParsedReport, source_text: str | None) -> None:
    """Drop the confidence of any LLM field whose citation can't be verified.

    Spec §Governance: "The citation excerpt is verified against the source span
    where feasible … an unverifiable citation drops confidence." Verification is
    a containment check of the citation excerpt against the source text; when no
    source text is available (URL-only path) verification is skipped (the
    locator is taken on trust at the model's stated confidence).
    """
    if not source_text:
        return
    low = source_text.lower()
    for path, fp in report.provenance.items():
        if fp.method != "ocr+llm" or fp.citation is None:
            continue
        excerpt = (fp.citation.excerpt or "").strip().lower()
        verified = bool(excerpt) and excerpt in low
        if not verified and fp.confidence > _UNVERIFIED_CITATION_CONFIDENCE_CAP:
            report.provenance[path] = fp.model_copy(
                update={"confidence": _UNVERIFIED_CITATION_CONFIDENCE_CAP}
            )


def _extract_with_llm(
    input: ReportExtractInput,
    *,
    client: LlmClient,
    model: str,
    max_retries: int = 1,
) -> ParsedReport:
    """Docling/OCR → LLM structured-output parse against :class:`ParsedReport`.

    Sends the report (text inline, or the PDF via its URL) plus the schema-bound
    prompt to ``client``, parses + validates the JSON into a :class:`ParsedReport`,
    and retries once on a validation failure (feeding the validation error back).
    Citations are then verified against the source span.
    """
    prompt = _llm_prompt(input.deal_name)
    if input.text is not None:
        parts: list[dict[str, Any]] = [{"text": f"{prompt}\n\nReport text:\n{input.text}"}]
    else:
        parts = [
            {"file_data": {"mime_type": "application/pdf", "file_uri": input.url}},
            {"text": prompt},
        ]

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        contents = [{"role": "user", "parts": list(parts)}]
        response = client.models.generate_content(model=model, contents=contents)
        raw = _strip_fences(response.text or "")
        try:
            report = ParsedReport.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "LLM ParsedReport validation failed (attempt %d/%d): %s",
                attempt + 1,
                max_retries + 1,
                exc,
            )
            # Feed the error back so the retry can correct itself.
            parts = parts + [
                {"text": f"Your previous JSON failed validation: {exc}. Return corrected JSON only."}
            ]
            continue
        report.extraction_method = "ocr+llm"
        if not report.deal_name:
            report.deal_name = input.deal_name
        _verify_citations(report, input.text)
        return report

    raise ValueError(
        f"LLM extraction for {input.deal_name!r} did not yield a valid ParsedReport "
        f"after {max_retries + 1} attempts: {last_error}"
    )


def _llm_confidence(report: ParsedReport) -> float:
    """Envelope confidence for an LLM parse — mean of per-field provenance confidence."""
    confs = [fp.confidence for fp in report.provenance.values()]
    if not confs:
        return 0.6  # no per-field provenance emitted — conservative default
    return sum(confs) / len(confs)


# ===========================================================================
# Top-level extraction + governed primitive
# ===========================================================================


def extract_report(
    input: ReportExtractInput,
    *,
    client: LlmClient | None = None,
    model: str | None = None,
    cache_dir: str | Path = DEFAULT_EXTRACTION_CACHE_DIR,
    use_cache: bool = True,
    force_refresh: bool = False,
) -> PrimitiveResult[ParsedReport]:
    """Extract a report into a governed :class:`PrimitiveResult` [:class:`ParsedReport`].

    Resolution order (cheapest first):

    1. **Deterministic format registry** — if a registered format ``matches``
       the report text, parse it deterministically (free, CI-stable, reconciles
       to the cent). Short-circuits the LLM. Deterministic parses are *not*
       cached (the parse is already reproducible).
    2. **Determinism cache** — for the LLM path, a cache hit (keyed by a hash of
       deal name + report bytes/URL) returns the previously-extracted report,
       so CI never hits the live LLM.
    3. **LLM fallback** — Docling/OCR → structured output against
       :class:`ParsedReport`, validated + retried, citations source-span
       verified. The result is written to the determinism cache.

    Parameters
    ----------
    input:
        The report to extract (``text`` or ``url`` + ``deal_name``).
    client:
        Injected LLM client (google-genai-shaped). Required only when the LLM
        path is actually reached (no deterministic match and a cold cache); the
        deterministic + cache-hit paths never touch it. A real client is built
        lazily when ``None`` and the LLM path is reached.
    model:
        LLM model id; defaults to the configured extraction model.
    cache_dir:
        Determinism-cache directory (default the repo's ``data/extraction_cache/``).
    use_cache:
        Consult / populate the determinism cache on the LLM path (default True).
    force_refresh:
        Bypass the cache read (still writes) on the LLM path.

    Raises
    ------
    ValueError
        If neither ``text`` nor ``url`` is set, or the LLM path is reached on a
        cold cache with no usable client / text basis.
    """
    if input.text is None and input.url is None:
        raise ValueError("ReportExtractInput requires one of `text` or `url`.")

    # 1. Deterministic-first.
    if input.text is not None:
        fmt = match_format(input.text)
        if fmt is not None:
            logger.info("Report extractor: deterministic format %r matched.", fmt.name)
            return fmt.parse(input.text, input.deal_name)

    # 2. Determinism cache (LLM path reproducibility).
    cache_path = _cache_path(_cache_key(input), cache_dir)
    if use_cache and not force_refresh:
        cached = _load_cache(cache_path)
        if cached is not None:
            logger.info("Report extractor: determinism-cache hit (%s).", cache_path.name)
            t0 = time.perf_counter()
            return PrimitiveResult[ParsedReport](
                output=cached,
                confidence=_llm_confidence(cached),
                citations=[
                    Citation(
                        document=f"{cached.deal_name} — cached extraction",
                        page_or_row=None,
                        excerpt="Loaded from the determinism cache (no live LLM call).",
                    )
                ],
                audit_entry=AuditEntry.now(
                    primitive_name=_PRIMITIVE_NAME,
                    version=_PRIMITIVE_VERSION,
                    input_hash=input.input_hash(),
                    duration_ms=(time.perf_counter() - t0) * 1000.0,
                ),
            )

    # 3. LLM fallback (cold cache, no deterministic match).
    if client is None:
        from google import genai

        from loanwhiz.config import GCP_LOCATION, GCP_PROJECT

        client = genai.Client(  # pragma: no cover - network/integration only
            vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION
        )
    if model is None:
        from loanwhiz.config import MODEL_PRO

        model = MODEL_PRO

    t0 = time.perf_counter()
    report = _extract_with_llm(input, client=client, model=model)
    duration_ms = (time.perf_counter() - t0) * 1000.0
    if use_cache:
        _write_cache(report, cache_path)

    return PrimitiveResult[ParsedReport](
        output=report,
        confidence=_llm_confidence(report),
        citations=[
            Citation(
                document=f"{report.deal_name} — report extraction (OCR+LLM)",
                page_or_row=input.url,
                excerpt="Extracted via Docling/OCR → LLM structured output against ParsedReport.",
            )
        ],
        audit_entry=AuditEntry.now(
            primitive_name=_PRIMITIVE_NAME,
            version=_PRIMITIVE_VERSION,
            input_hash=input.input_hash(),
            duration_ms=duration_ms,
        ),
    )


# ===========================================================================
# General per-deal report resolution (the #398 cold-start seam)
# ===========================================================================
#
# Turns *any* deal's published Notes & Cash reports into one canonical
# :class:`ParsedReport`, replacing the hand-written per-deal loader maps the API
# used to require (``api.main._REPORT_LOADERS`` / ``_REPORT_GATE_BUILDERS``). A
# new deal cold-starts the report path zero-touch: drop a registry entry with
# ``notes_cash_report_urls`` and the resolver fetches + extracts it generally,
# deterministic-first, under the same governed extractor + determinism cache.
#
# Resolution order (cheapest, most-reproducible first), mirroring
# ``notes_cash_parser.parse_notes_cash_report``:
#   1. **Committed offline fixtures** — a deal registered in
#      ``COMMITTED_REPORT_FIXTURES`` (Green Lion 2024-1 ships its three quarterly
#      ``.txt`` extracts) parses each fixture through ``extract_report``; the
#      deterministic format registry recognizes them, so the parse is byte-stable
#      in CI with no network / no LLM. This is what keeps GL-2024-1's
#      validated-to-the-cent proof unchanged after the loader map is deleted.
#   2. **Durable report cache** ``data/extraction_cache/notes-cash-{slug}.json``
#      (the cache ``notes_cash_parser`` writes) — load + bridge offline on hit.
#   3. **Live extraction** (``allow_live=True`` only) — fetch each
#      ``notes_cash_report_urls`` PDF, extract its text
#      (``notes_cash_parser._extract_pdf_text``), run ``extract_report``
#      (deterministic format if recognized, else the governed OCR/LLM fallback),
#      and persist to the durable cache. Network/LLM only — never reached in the
#      fast suite. The **synchronous API GETs** call with the default
#      ``allow_live=False`` so they never block on a live extraction and degrade
#      honestly when only step 3 would resolve; an async ingest flow (#399 /
#      the on-demand extraction endpoint) opts in to populate the cache.


class ReportUnavailable(RuntimeError):
    """A deal has no resolvable Notes & Cash report source (#398).

    Raised by :func:`resolve_parsed_report` when none of committed fixtures, a
    durable cache, or live ``notes_cash_report_urls`` yields a report — so the
    caller can degrade honestly (a labelled 422 / ``available=false``) rather
    than fold an empty series.
    """


#: Per-deal **committed** Notes & Cash fixtures, keyed by canonical deal id. Each
#: value is a ``(fixture_dir, [(filename, period_label), ...])`` pair of
#: deterministic ``pypdf`` text extracts (oldest-first — the fold + reconciler
#: read them positionally). This is the offline source that keeps a deal's report
#: path network-free in CI; it is **pure data** — adding a deal's offline fixtures
#: is a data edit, not a new code loader. A deal absent here simply falls through
#: to the durable cache / live extraction. Patchable in tests.
_NOTES_CASH_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "notes_cash"
COMMITTED_REPORT_FIXTURES: dict[str, tuple[Path, tuple[tuple[str, str], ...]]] = {
    "green-lion-2024-1": (
        _NOTES_CASH_FIXTURE_DIR,
        (
            ("green-lion-2024-1-september-2025.txt", "September 2025"),
            ("green-lion-2024-1-december-2025.txt", "December 2025"),
            ("green-lion-2024-1-march-2026.txt", "March 2026"),
        ),
    ),
}


def _splice_periods(
    deal_name: str, period_reports: list[ParsedReport]
) -> ParsedReport:
    """Splice per-period :class:`ParsedReport` extractions into one report.

    Each ``extract_report`` call over a single Notes & Cash period yields a
    one-period :class:`ParsedReport`; the cold-start fold + reconciler read a
    deal's periods positionally (oldest-first), so the per-period results are
    concatenated into a single report. ``extraction_method`` is ``deterministic``
    only when *every* period was; any LLM-extracted period downgrades it to
    ``ocr+llm`` so the provenance stays honest.
    """
    periods: list[ParsedReportPeriod] = []
    methods: set[str] = set()
    for r in period_reports:
        periods.extend(r.periods)
        methods.add(r.extraction_method)
    method = "deterministic" if methods == {"deterministic"} else "ocr+llm"
    return ParsedReport(
        deal_name=deal_name,
        report_type="notes_and_cash",
        periods=periods,
        extraction_method=method,  # type: ignore[arg-type]
    )


def _parsed_from_committed_fixtures(
    deal_id: str, deal_name: str
) -> ParsedReport | None:
    """Build a :class:`ParsedReport` from a deal's committed fixtures, or ``None``."""
    entry = COMMITTED_REPORT_FIXTURES.get(deal_id)
    if entry is None:
        return None
    fixture_dir, fixtures = entry
    period_reports = [
        extract_report(
            ReportExtractInput(
                deal_name=deal_name,
                text=(fixture_dir / filename).read_text(encoding="utf-8"),
            )
        ).output
        for filename, _label in fixtures
    ]
    return _splice_periods(deal_name, period_reports)


def _parsed_from_notes_cash_report(report: NotesCashReport) -> ParsedReport:
    """Bridge a concrete :class:`NotesCashReport` (durable cache) → :class:`ParsedReport`.

    The inverse of :meth:`ParsedReport.to_notes_cash_report`; reuses the existing
    deterministic ``NotesCashPeriod -> ParsedReportPeriod`` mapping so a cached
    report folds identically to a freshly-extracted one.
    """
    return ParsedReport(
        deal_name=report.deal_name,
        report_type="notes_and_cash",
        periods=[_notes_cash_period_to_parsed(p) for p in report.periods],
        extraction_method="deterministic",
    )


def resolve_parsed_report(
    deal_id: str,
    deal: dict[str, Any],
    *,
    client: LlmClient | None = None,
    model: str | None = None,
    cache_dir: str | Path = DEFAULT_EXTRACTION_CACHE_DIR,
    use_cache: bool = True,
    allow_live: bool = False,
) -> ParsedReport:
    """Resolve *any* deal's Notes & Cash report into one canonical :class:`ParsedReport`.

    The general report cold-start (#398): replaces the API's per-deal
    ``_REPORT_LOADERS`` / ``_REPORT_GATE_BUILDERS`` maps so a new report-driven
    deal models zero-touch. See the module-section comment above for the
    resolution order (committed fixtures → durable cache → live extraction).

    Parameters
    ----------
    deal_id:
        Canonical deal id (the registry key + committed-fixture key).
    deal:
        The deal-registry dict; ``deal_name`` is required, ``notes_cash_report_urls``
        is consulted only on the live-extraction path.
    client / model:
        Injected LLM client / model id for the live OCR+LLM fallback (passed
        through to :func:`extract_report`); never reached on the
        fixture/cache paths.
    cache_dir / use_cache:
        Durable + determinism cache controls, passed through.
    allow_live:
        Gate on the live extraction path (step 3 — PDF fetch + OCR/LLM). The
        **synchronous API request path** (the waterfall + report-gate GETs) calls
        with the default ``False`` so it never blocks on a ~minutes-long network +
        LLM extraction and stays offline/deterministic in CI: a deal resolvable
        only by live extraction raises :class:`ReportUnavailable`, which the caller
        degrades honestly (labelled 422 / ``available=false``). An asynchronous
        ingest flow (the on-demand extraction endpoint / ingest-API #399) opts in
        with ``allow_live=True`` to populate the durable cache, after which the
        offline paths serve the deal. This keeps "general report ingestion" true
        without turning a GET into a live extraction.

    Raises
    ------
    ReportUnavailable
        When no committed fixture, durable cache, or (with ``allow_live``) live
        report source resolves.
    """
    deal_name = deal["deal_name"]

    # 1. Committed offline fixtures (deterministic, CI-stable, no network/LLM).
    committed = _parsed_from_committed_fixtures(deal_id, deal_name)
    if committed is not None:
        logger.info("Report resolver: committed fixtures for %r.", deal_id)
        return committed

    # 2. Durable report cache (offline on hit).
    from loanwhiz.primitives import notes_cash_parser as _ncp

    durable_path = _ncp._cache_path(deal_name, cache_dir)
    cached = _ncp._load_durable_cache(durable_path)
    if cached is not None:
        logger.info("Report resolver: durable notes-cash cache for %r.", deal_id)
        return _parsed_from_notes_cash_report(cached)

    # 3. Live extraction (network/LLM — cold cache only, opt-in).
    report_urls = deal.get("notes_cash_report_urls") or []
    if not allow_live or not report_urls:
        raise ReportUnavailable(
            f"Deal '{deal_id}' has no offline-resolvable Notes & Cash report: no "
            f"committed fixtures and no durable cache"
            + (
                "."
                if not report_urls
                else " (live extraction not requested — pass allow_live=True to "
                "fetch + extract its notes_cash_report_urls)."
            )
        )

    period_reports: list[ParsedReport] = []
    for entry in report_urls:  # pragma: no cover - network/integration only
        url = entry["url"]
        text = _ncp._extract_pdf_text(url)
        period_reports.append(
            extract_report(
                ReportExtractInput(deal_name=deal_name, text=text),
                client=client,
                model=model,
                cache_dir=cache_dir,
                use_cache=use_cache,
            ).output
        )
    resolved = _splice_periods(deal_name, period_reports)

    # Persist to the durable notes-cash cache so the next request is offline.
    try:  # pragma: no cover - network/integration only
        _ncp._write_durable_cache(resolved.to_notes_cash_report(), durable_path)
    except Exception as exc:  # caching is best-effort
        logger.warning("Report resolver: durable cache write failed: %s", exc)
    return resolved


@register_primitive(
    name=_PRIMITIVE_NAME,
    version=_PRIMITIVE_VERSION,
    description=(
        "General, governed report extractor: deterministic format-registry "
        "fast-path (Green Lion Notes & Cash first) + Docling/OCR→LLM structured-"
        "output fallback against the ParsedReport schema, with per-field provenance "
        "and a determinism cache."
    ),
    author="loanwhiz",
    tags=["extraction", "report", "governed"],
)
class ReportExtractor(Primitive[ReportExtractInput, ParsedReport]):
    """Governed report extractor primitive (#271).

    Deterministic-first, LLM-second. The LLM client is injectable via the
    constructor so the fast suite never reaches the real network; the
    deterministic + cache-hit paths never touch it regardless.
    """

    name = _PRIMITIVE_NAME
    version = _PRIMITIVE_VERSION
    description = (
        "General, governed report extractor (deterministic format registry + "
        "OCR/LLM fallback, per-field provenance, determinism cache)."
    )

    def __init__(
        self,
        *,
        client: LlmClient | None = None,
        model: str | None = None,
        cache_dir: str | Path = DEFAULT_EXTRACTION_CACHE_DIR,
        use_cache: bool = True,
    ) -> None:
        self._client = client
        self._model = model
        self._cache_dir = cache_dir
        self._use_cache = use_cache

    def execute(self, input: ReportExtractInput) -> PrimitiveResult[ParsedReport]:
        """Run the extractor (deterministic-first, cache, then LLM fallback)."""
        return extract_report(
            input,
            client=self._client,
            model=self._model,
            cache_dir=self._cache_dir,
            use_cache=self._use_cache,
        )
