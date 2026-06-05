"""Notes & Cash report parser — the *liability* ground truth for seasoned deals.

This module parses the DSA **"Notes and Cash Report"** (the quarterly *liability*
report, ESMA/DSA v2.0) into structured per-period actuals. Where the monthly
investor report (``extraction/collateral_ledger``) carries the **collateral**
side and **no** liability figures, the Notes & Cash report carries the liability
side in full:

- **Bond Report** — per-class note balances, factors, principal/interest
  payments, and the Principal Deficiency Ledger (PDL) per class.
- **Revenue Priority of Payments** + **Redemption Priority of Payments** — the
  *actual* per-step distributions, each step keyed by its prospectus priority
  label ``(a)…(k)`` with the EUR amount distributed this period.
- **Issuer Transaction Accounts** — the reserve account (target / drawings /
  end balance) and the other issuer cash accounts.
- **Transaction Triggers and Events** — each trigger's required value, current
  value, and OK / breached status.

Why this is the liability ground truth (epic #206)
--------------------------------------------------
The seasoned deals (Green Lion 2023-1 / 2024-1) publish this report quarterly.
Crucially it publishes BOTH the *available funds* AND the *per-step
distributions*, so the waterfall **engine** can be externally validated: feed a
report's own available funds into the interpreter, reconcile the engine's
distribution to the report's own published priority-of-payments. That is V4's
job; V3 (this module) produces the structured ground truth V4 reconciles
against. Each deal is validated only against its OWN data — never spliced.

The two seams (mirrors ``collateral_ledger``)
---------------------------------------------
- **Parse path (offline, unit-tested):** :func:`parse_report_text` maps the raw
  extracted PDF text into a typed :class:`NotesCashPeriod`. Pure; no network.
- **Extraction + cache (live, integration-gated):**
  :func:`parse_notes_cash_report` returns the report for a deal, served from a
  durable on-disk cache (``data/extraction_cache/``). On a cold cache it fetches
  each report PDF and extracts its text with ``pypdf`` (these reports are
  text-extractable, not scanned — deterministic, no LLM), then parses. The demo
  never re-fetches once the cache is warm.

The PoP step vocabulary (``priority`` / ``recipient`` / ``amount``) deliberately
mirrors ``primitives.waterfall_interpreter``'s ``StepResult`` so V4 joins the
report's published distribution to the engine's computed one step-for-step.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache location — same durable convention as collateral_ledger (PR #152).
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXTRACTION_CACHE_DIR = _REPO_ROOT / "data" / "extraction_cache"

# The three note classes the Green Lion structure carries, in seniority order.
# The Bond Report lays each class out as a column; the parser reads them in
# this order. Deal-agnostic in spirit (it is the standard senior/mezz/junior
# RMBS layout); a different deal's class set would extend this list.
_NOTE_CLASSES: tuple[str, ...] = ("class_a", "class_b", "class_c")


# ===========================================================================
# Numeric coercion helpers
# ===========================================================================

# A EUR amount as printed in these reports: optional leading minus, thousands
# separated by commas, two decimals. e.g. "1,402,891.43", "-67,557.12", "0.00".
_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?")


def _parse_money(token: str) -> float | None:
    """Parse one printed money/number token to float, or ``None`` if non-numeric.

    Handles thousands commas, a trailing ``%`` (stripped), and the report's
    ``N/A`` / blank sentinels (→ ``None``). The ``-/-`` reduction marker that
    precedes some "less:" lines is not a value and never reaches here.

    >>> _parse_money("1,402,891.43")
    1402891.43
    >>> _parse_money("25.00 %")
    25.0
    >>> _parse_money("N/A") is None
    True
    """
    if token is None:
        return None
    t = token.strip().rstrip("%").strip()
    if not t or t.upper() in {"N/A", "NA", "NOT APPLICABLE", "-/-"}:
        return None
    m = _NUMBER_RE.fullmatch(t)
    if not m:
        return None
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def _is_money_line(line: str) -> bool:
    """Whether a stripped line is a standalone printed number (a column value)."""
    return _parse_money(line) is not None and bool(_NUMBER_RE.fullmatch(line.strip().rstrip("%").strip()))


# ===========================================================================
# Typed model
# ===========================================================================


class NoteClassBalance(BaseModel):
    """One note class's liability figures from the Bond Report.

    All monetary fields are in the deal currency (EUR). ``note_class`` is the
    canonical key (``class_a`` / ``class_b`` / ``class_c``) so V4 joins to
    ``DealState.class_{a,b,c}_balance`` / ``_pdl``.
    """

    note_class: str = Field(..., description="Canonical class key, e.g. 'class_a'.")
    principal_balance_after_payment: float | None = Field(
        default=None, description="Outstanding note balance after this period's payment (EUR)."
    )
    total_principal_payments: float | None = Field(
        default=None, description="Principal repaid to this class this period (EUR)."
    )
    factor_after_payment: float | None = Field(
        default=None, description="Note factor after payment (balance / original)."
    )
    total_interest_payments: float | None = Field(
        default=None, description="Interest paid to this class this period (EUR)."
    )
    pdl_balance_after_payment: float | None = Field(
        default=None, description="Principal Deficiency Ledger balance after payment (EUR)."
    )


class PoPStep(BaseModel):
    """One executed priority-of-payments step (revenue or redemption).

    The vocabulary mirrors ``waterfall_interpreter.StepResult`` so V4 reconciles
    the engine's computed distribution against the report's published one,
    step-for-step.

    Attributes
    ----------
    priority:
        The prospectus priority label, e.g. ``"(d)"`` (the waterfall step id).
    recipient:
        The step's description / recipient text as printed in the report.
    amount:
        The EUR amount actually distributed at this step in the *current* period.
    previous_amount:
        The same step's amount in the *previous* period (the report prints both
        columns). Useful as a chaining cross-check; ``None`` if not printed.
    """

    priority: str = Field(..., description="Prospectus priority label, e.g. '(d)'.")
    recipient: str = Field(..., description="Step description / recipient as printed.")
    amount: float = Field(..., description="EUR distributed at this step (current period).")
    previous_amount: float | None = Field(
        default=None, description="Same step's amount in the previous period (EUR)."
    )


class IssuerAccount(BaseModel):
    """One issuer transaction account's end-of-period balance.

    ``name`` is a normalised key (e.g. ``reserve_account``,
    ``issuer_collection_account``). For the reserve account the target and the
    period's drawings are captured where the report prints them.
    """

    name: str = Field(..., description="Normalised account key, e.g. 'reserve_account'.")
    balance_end: float | None = Field(
        default=None, description="Account balance at end of the reporting period (EUR)."
    )
    target: float | None = Field(
        default=None, description="Target balance (reserve account only), if printed (EUR)."
    )
    drawings: float | None = Field(
        default=None, description="Drawings from the account this period (reserve only) (EUR)."
    )


class TriggerState(BaseModel):
    """One transaction trigger / event and whether it is breached.

    ``label`` is the trigger's priority label or row identifier; ``description``
    the printed condition text; ``breached`` the parsed status (the report prints
    ``OK`` when not breached). ``required_value`` / ``current_value`` carry the
    printed threshold and observed value where both are numeric.
    """

    label: str = Field(..., description="Trigger label / row id, e.g. '(a)'.")
    description: str = Field(..., description="Trigger condition text as printed.")
    required_value: float | None = Field(default=None, description="Printed threshold, if numeric.")
    current_value: float | None = Field(default=None, description="Printed observed value, if numeric.")
    breached: bool = Field(default=False, description="True if the report marks the trigger breached.")
    status: str = Field(default="OK", description="Raw status string as printed ('OK' / 'Breached').")


class NotesCashPeriod(BaseModel):
    """One Notes & Cash report — the liability actuals for one reporting period.

    Keyed by :attr:`reporting_date` (ISO period-end), matching
    ``DealState.reporting_date`` and the collateral ledger, so V4 joins liability
    ground truth to the reconstructed state by date.
    """

    # --- key / metadata ---
    reporting_date: str = Field(..., description="ISO reporting date — the report key (e.g. 2026-04-23).")
    period_label: str = Field(..., description='Human-readable period label, e.g. "March 2026".')
    deal_name: str | None = Field(default=None, description="Deal name as printed on the report.")
    esma_identifier: str | None = Field(default=None, description="ESMA identifier from the report header.")
    reporting_period: str | None = Field(
        default=None, description='Reporting period range as printed (e.g. "23 January 2026 - 23 April 2026").'
    )

    # --- liability sections ---
    note_balances: list[NoteClassBalance] = Field(
        default_factory=list, description="Per-class Bond Report balances."
    )
    revenue_pop: list[PoPStep] = Field(
        default_factory=list, description="Revenue Priority of Payments — executed per-step distributions."
    )
    redemption_pop: list[PoPStep] = Field(
        default_factory=list, description="Redemption Priority of Payments — executed per-step distributions."
    )
    available_revenue_funds: float | None = Field(
        default=None, description="Total Available Revenue Funds (current period) (EUR)."
    )
    available_principal_funds: float | None = Field(
        default=None, description="Total Available Principal Funds (current period) (EUR)."
    )
    issuer_accounts: list[IssuerAccount] = Field(
        default_factory=list, description="Issuer Transaction Accounts."
    )
    triggers: list[TriggerState] = Field(
        default_factory=list, description="Transaction Triggers and Events."
    )

    @field_validator("reporting_date")
    @classmethod
    def _non_empty_date(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("reporting_date must be a non-empty ISO date string")
        return v

    # --- join surfaces (the V4 reconciliation API) ---

    def note_balance(self, note_class: str) -> NoteClassBalance | None:
        """The Bond Report balance for one class, or ``None``."""
        return next((b for b in self.note_balances if b.note_class == note_class), None)

    def revenue_step(self, priority: str) -> PoPStep | None:
        """The revenue PoP step with the given priority label, or ``None``."""
        return next((s for s in self.revenue_pop if s.priority == priority), None)

    def redemption_step(self, priority: str) -> PoPStep | None:
        """The redemption PoP step with the given priority label, or ``None``."""
        return next((s for s in self.redemption_pop if s.priority == priority), None)

    def account(self, name: str) -> IssuerAccount | None:
        """The issuer account with the given normalised name, or ``None``."""
        return next((a for a in self.issuer_accounts if a.name == name), None)

    def trigger(self, label: str) -> TriggerState | None:
        """The trigger with the given label, or ``None``."""
        return next((t for t in self.triggers if t.label == label), None)

    @property
    def reserve_balance(self) -> float | None:
        """End-of-period reserve account balance (EUR), if present."""
        acct = self.account("reserve_account")
        return acct.balance_end if acct else None

    @property
    def reserve_target(self) -> float | None:
        """Reserve account target balance (EUR), if present."""
        acct = self.account("reserve_account")
        return acct.target if acct else None

    @property
    def any_trigger_breached(self) -> bool:
        """Whether any parsed trigger is marked breached."""
        return any(t.breached for t in self.triggers)

    @property
    def total_pdl(self) -> float:
        """Sum of per-class PDL balances after payment (treating None as 0)."""
        return sum((b.pdl_balance_after_payment or 0.0) for b in self.note_balances)

    def revenue_distributed_total(self) -> float:
        """Sum of all revenue PoP step amounts (current period) (EUR)."""
        return sum(s.amount for s in self.revenue_pop)

    def redemption_distributed_total(self) -> float:
        """Sum of all redemption PoP step amounts (current period) (EUR)."""
        return sum(s.amount for s in self.redemption_pop)


class NotesCashReport(BaseModel):
    """All Notes & Cash report periods for one deal, keyed by reporting date.

    Periods are held sorted by :attr:`NotesCashPeriod.reporting_date` (mirrors
    ``CollateralLedger``). JSON-serialisable for the durable cache.
    """

    deal_name: str = Field(..., description="The deal this report set covers.")
    periods: list[NotesCashPeriod] = Field(
        default_factory=list, description="Periods, sorted by reporting_date."
    )

    @field_validator("periods")
    @classmethod
    def _sort_periods(cls, v: list[NotesCashPeriod]) -> list[NotesCashPeriod]:
        return sorted(v, key=lambda p: p.reporting_date)

    @property
    def reporting_dates(self) -> list[str]:
        """The ISO reporting dates present, in order."""
        return [p.reporting_date for p in self.periods]

    @property
    def by_date(self) -> dict[str, NotesCashPeriod]:
        """Map of ISO reporting date → period (the V4 join surface)."""
        return {p.reporting_date: p for p in self.periods}

    def period_for(self, reporting_date: str) -> NotesCashPeriod | None:
        """Look a period up by its ISO reporting date, or ``None``."""
        return self.by_date.get(reporting_date)


# ===========================================================================
# Header parsing
# ===========================================================================

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "23 April 2026" / "23 Apr 2026"
_DMY_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")


def _dmy_to_iso(text: str) -> str | None:
    """Convert a '23 April 2026' style date to ISO '2026-04-23', or ``None``."""
    m = _DMY_RE.search(text)
    if not m:
        return None
    day, month_name, year = m.group(1), m.group(2).lower(), m.group(3)
    month = _MONTHS.get(month_name)
    if month is None:
        return None
    return f"{year}-{month:02d}-{int(day):02d}"


# ===========================================================================
# Pure parse path (the unit-tested seam)
# ===========================================================================


def _slug(name: str) -> str:
    """Filesystem-safe slug from a deal name (mirrors collateral_ledger._slug).

    >>> _slug("Green Lion 2024-1 B.V.")
    'green-lion-2024-1-bv'
    """
    lowered = re.sub(r"[.,]+", "", name.lower())
    replaced = re.sub(r"\s+", "-", lowered)
    return re.sub(r"-{2,}", "-", replaced).strip("-")


def _lines(text: str) -> list[str]:
    """Split extracted text into stripped, non-empty lines."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _parse_header(lines: list[str]) -> dict[str, str | None]:
    """Pull deal name, ESMA id, reporting period range, and reporting date."""
    out: dict[str, str | None] = {
        "deal_name": None,
        "esma_identifier": None,
        "reporting_period": None,
        "reporting_date_iso": None,
    }
    for i, ln in enumerate(lines):
        low = ln.lower()
        if out["deal_name"] is None and "b.v." in low and "report" not in low:
            out["deal_name"] = ln
        if low.startswith("esma identifier"):
            out["esma_identifier"] = ln.split(":", 1)[-1].strip() or (
                lines[i + 1].strip() if i + 1 < len(lines) else None
            )
        if low.startswith("reporting period"):
            val = ln.split(":", 1)[-1].strip()
            out["reporting_period"] = val or (lines[i + 1].strip() if i + 1 < len(lines) else None)
        if low.startswith("reporting date"):
            val = ln.split(":", 1)[-1].strip() or (lines[i + 1].strip() if i + 1 < len(lines) else "")
            out["reporting_date_iso"] = _dmy_to_iso(val)
    return out


def _take_class_values(lines: list[str], start: int, count: int = 3) -> list[float | None]:
    """From ``start``, collect the next ``count`` standalone-number lines.

    In the Bond Report each labelled row is followed by one value per note class
    (Class A / B / C), each on its own line. ``N/A`` cells are included as
    ``None`` so positional alignment with the class list is preserved.
    """
    vals: list[float | None] = []
    i = start
    while i < len(lines) and len(vals) < count:
        ln = lines[i]
        if _is_money_line(ln):
            vals.append(_parse_money(ln))
        elif ln.upper() in {"N/A", "NA"}:
            vals.append(None)
        else:
            # A non-value, non-N/A line: the row's values ended early.
            break
        i += 1
    while len(vals) < count:
        vals.append(None)
    return vals


def _find_row(lines: list[str], label: str, *, start: int = 0, exact: bool = False) -> int:
    """Index of the first line equal to / containing ``label`` (case-insensitive)."""
    target = label.lower()
    for i in range(start, len(lines)):
        ln = lines[i].lower()
        if (ln == target) if exact else (target in ln):
            return i
    return -1


def _parse_bond_report(lines: list[str]) -> list[NoteClassBalance]:
    """Parse the Bond Report rows into per-class :class:`NoteClassBalance`."""
    fields = {
        "principal_balance_after_payment": "Principal Balance after Payment",
        "total_principal_payments": "Total Principal Payments",
        "factor_after_payment": "Factor after Payment",
        "total_interest_payments": "Total Interest Payments",
        "pdl_balance_after_payment": "PDL Balance after the Payment Date",
    }
    by_class: dict[str, dict[str, float | None]] = {c: {} for c in _NOTE_CLASSES}
    for field, label in fields.items():
        idx = _find_row(lines, label, exact=True)
        if idx < 0:
            # Some labels recur ("Total Principal Payments" appears once); fall
            # back to a contains-match if no exact line existed.
            idx = _find_row(lines, label)
        if idx < 0:
            continue
        vals = _take_class_values(lines, idx + 1, count=len(_NOTE_CLASSES))
        for cls_key, val in zip(_NOTE_CLASSES, vals):
            by_class[cls_key][field] = val
    return [NoteClassBalance(note_class=c, **by_class[c]) for c in _NOTE_CLASSES]


# A priority label at line start: "(a)", "(b)", "(14)", and the top-level
# letters/numbers the PoP and trigger rows use. Must be a single short token so
# inline "(a)" / "(i)" references in description prose don't false-match.
_LABEL_RE = re.compile(r"^\(\s*([a-zA-Z]|\d{1,2})\s*\)")


def _step_label(lines: list[str], i: int) -> tuple[str | None, int]:
    """Return ``(priority_label, next_index)`` if line ``i`` opens a PoP step.

    Handles the normal ``"(d) fourth, ..."`` line and the ``pypdf`` quirk where
    a two-digit label wraps to ``"("`` / ``"13"`` / ``")"`` on three lines.
    ``next_index`` is the line after the consumed label tokens.
    """
    ln = lines[i]
    m = _LABEL_RE.match(ln)
    if m:
        return f"({m.group(1)})", i + 1
    # Split label: "(" then a bare number then a line beginning ")".
    if ln == "(" and i + 2 < len(lines) and lines[i + 1].isdigit() and lines[i + 2].startswith(")"):
        # fold the trailing ") ...desc" remainder back as description
        return f"({lines[i + 1]})", i + 2
    return None, i + 1


def _parse_pop_section(lines: list[str], start: int, end: int) -> list[PoPStep]:
    """Parse a priority-of-payments block between line indices ``[start, end)``.

    Each executed step is a label line (its priority text begins with ``(x)``)
    followed — possibly several wrapped lines later — by exactly two standalone
    numbers (previous period, current period). Wrapped description lines fold
    into the recipient text. Numbers interrupted by an intervening text line
    reset, keeping alignment on multi-line rows.
    """
    steps: list[PoPStep] = []
    i = start
    n = min(end, len(lines))
    while i < n:
        priority, after_label = _step_label(lines, i)
        if priority is None:
            i += 1
            continue
        desc_parts = [lines[i]]
        j = after_label
        nums: list[float] = []
        while j < n:
            cand = lines[j]
            nxt_label, _ = _step_label(lines, j)
            if nxt_label is not None:
                break  # next step started before we found 2 numbers
            if _is_money_line(cand):
                nums.append(_parse_money(cand))  # type: ignore[arg-type]
                if len(nums) == 2:
                    j += 1
                    break
            else:
                if nums:
                    nums = []  # numbers interrupted by text — not a clean row
                desc_parts.append(cand)
            j += 1
        if len(nums) == 2:
            steps.append(
                PoPStep(
                    priority=priority,
                    recipient=" ".join(p.strip() for p in desc_parts).strip(),
                    previous_amount=nums[0],
                    amount=nums[1],
                )
            )
            i = j
        else:
            i += 1
    return steps


def _pop_block_bounds(lines: list[str], section: str, total_label: str) -> tuple[int, int]:
    """[start, end) of a PoP *distribution* block.

    The section title (e.g. ``"Revenue Priority of Payments"``) appears multiple
    times — in the table of contents, as the body header that precedes the step
    rows, and as a page footer. The distribution block is the occurrence whose
    next priority-labelled line is the first ``(a)`` step; it ends at the
    ``"Total ... Priority of Payments"`` line. Returns ``(-1, -1)`` if not found.
    """
    n = len(lines)
    for i in range(n):
        if lines[i].lower() != section.lower():
            continue
        # Is this occurrence immediately followed (within a few lines) by a
        # priority-labelled step row? The TOC / footer occurrences are not.
        for k in range(i + 1, min(i + 4, n)):
            lbl, _ = _step_label(lines, k)
            if lbl is not None:
                end = _find_row(lines, total_label, start=k)
                return (i + 1, end if end >= 0 else n)
            # allow a single non-step line (e.g. "Previous Period") in between
            if _is_money_line(lines[k]):
                break
    return (-1, -1)


def _parse_total(lines: list[str], label: str) -> float | None:
    """The (current-period) total on a 'Total ...' line — its last number."""
    idx = _find_row(lines, label, exact=True)
    if idx < 0:
        idx = _find_row(lines, label)
    if idx < 0:
        return None
    # The current-period column is the last of the two numbers following label.
    nums = []
    for k in range(idx + 1, min(idx + 5, len(lines))):
        if _is_money_line(lines[k]):
            nums.append(_parse_money(lines[k]))
        elif nums:
            break
    return nums[-1] if nums else None


def _parse_accounts(lines: list[str]) -> list[IssuerAccount]:
    """Parse the Issuer Transaction Accounts section."""
    accounts: list[IssuerAccount] = []

    def _last_num_after(label: str, start: int = 0) -> tuple[float | None, int]:
        idx = _find_row(lines, label, start=start)
        if idx < 0:
            return (None, -1)
        nums = []
        for k in range(idx + 1, min(idx + 4, len(lines))):
            if _is_money_line(lines[k]):
                nums.append(_parse_money(lines[k]))
            elif nums:
                break
        return ((nums[-1] if nums else None), idx)

    # Reserve account: target, drawings, end balance.
    target, _ = _last_num_after("Target Reserve Account Balance at the end")
    drawings, _ = _last_num_after("Drawings from Reserve Account")
    reserve_end, _ = _last_num_after("Reserve Account Balance at the end of the Reporting Period")
    if any(v is not None for v in (target, drawings, reserve_end)):
        accounts.append(
            IssuerAccount(
                name="reserve_account",
                balance_end=reserve_end,
                target=target,
                drawings=drawings,
            )
        )

    # Other accounts: just the end balance.
    simple = {
        "issuer_collection_account": "Issuer Transaction Account balance at the end of the Reporting Period",
        "construction_deposit_account": "Construction Deposit Account balance at the end of the Reporting Period",
        "swap_collateral_account": "Swap Collateral Account balance at the end of the Reporting Period",
        "issuer_expense_account": "Issuer Expense Account Balance at the end of the Reporting Period",
    }
    for key, label in simple.items():
        bal, idx = _last_num_after(label)
        if idx >= 0:
            accounts.append(IssuerAccount(name=key, balance_end=bal))
    return accounts


def _parse_triggers(lines: list[str], start: int) -> list[TriggerState]:
    """Parse the Transaction Triggers and Events section from ``start``.

    Each trigger row is a label ``(x)`` description, then Required Value, Current
    Value, a Status (``OK`` / ``Breached``), and a Consequence line. Numeric
    required/current values are captured where present; the status drives
    ``breached``.
    """
    triggers: list[TriggerState] = []
    label_re = re.compile(r"^\(?\s*([a-zA-Z0-9]{1,3})\s*[\)\.]")
    status_words = {"ok", "breached", "breach", "not breached"}
    n = len(lines)
    i = start
    while i < n:
        ln = lines[i]
        m = label_re.match(ln)
        if not m:
            i += 1
            continue
        label = f"({m.group(1)})"
        desc_parts = [ln]
        req: float | None = None
        cur: float | None = None
        status: str | None = None
        nums: list[float] = []
        j = i + 1
        while j < n:
            cand = lines[j]
            low = cand.lower()
            if label_re.match(cand) and not _is_money_line(cand):
                break  # next trigger
            if low in status_words:
                status = "Breached" if low.startswith("breach") else "OK"
                j += 1
                break
            mv = _parse_money(cand)
            if mv is not None and (_is_money_line(cand) or cand.rstrip().endswith("%")):
                nums.append(mv)
            else:
                desc_parts.append(cand)
            j += 1
        if status is None:
            # No status line found before the next label → not a trigger row.
            i += 1
            continue
        if len(nums) >= 2:
            req, cur = nums[0], nums[1]
        elif len(nums) == 1:
            req = nums[0]
        triggers.append(
            TriggerState(
                label=label,
                description=" ".join(p.strip() for p in desc_parts).strip(),
                required_value=req,
                current_value=cur,
                breached=(status == "Breached"),
                status=status,
            )
        )
        i = j
    return triggers


def parse_report_text(
    text: str,
    *,
    period_label: str,
    reporting_date: str | None = None,
) -> NotesCashPeriod:
    """Parse extracted Notes & Cash report text into a :class:`NotesCashPeriod`.

    Pure and offline — the unit-tested seam. ``text`` is the concatenated text of
    all report pages (as ``pypdf`` extracts it). ``reporting_date`` overrides the
    date parsed from the header; if both are absent the header's "Reporting Date"
    is used, and a missing date raises.

    Parameters
    ----------
    text:
        Concatenated extracted PDF text.
    period_label:
        Human-readable period label, e.g. ``"March 2026"``.
    reporting_date:
        ISO reporting date override. Falls back to the header's reporting date.

    Raises
    ------
    ValueError
        If no reporting date can be determined (neither argument nor header).
    """
    lines = _lines(text)
    header = _parse_header(lines)
    iso = reporting_date or header["reporting_date_iso"]
    if not iso:
        raise ValueError(
            f"Notes & Cash report for {period_label!r} has no reporting date "
            "(none in header and none passed) — cannot key the report by date"
        )

    note_balances = _parse_bond_report(lines)

    # Revenue PoP: the distribution block (header followed by step rows),
    # terminated by its "Total ..." line.
    rev_start, rev_end = _pop_block_bounds(
        lines, "Revenue Priority of Payments", "Total Revenue Priority of Payments"
    )
    revenue_pop = _parse_pop_section(lines, rev_start, rev_end) if rev_start >= 0 else []

    red_start, red_end = _pop_block_bounds(
        lines, "Redemption Priority of Payments", "Total Redemption Priority of Payments"
    )
    redemption_pop = _parse_pop_section(lines, red_start, red_end) if red_start >= 0 else []

    available_revenue = _parse_total(lines, "Total Available Revenue Funds")
    available_principal = _parse_total(lines, "Total Available Principal Funds")

    issuer_accounts = _parse_accounts(lines)

    trig_start = _find_row(lines, "Transaction Triggers and Events")
    triggers = _parse_triggers(lines, trig_start + 1) if trig_start >= 0 else []

    return NotesCashPeriod(
        reporting_date=str(iso),
        period_label=period_label,
        deal_name=header["deal_name"],
        esma_identifier=header["esma_identifier"],
        reporting_period=header["reporting_period"],
        note_balances=note_balances,
        revenue_pop=revenue_pop,
        redemption_pop=redemption_pop,
        available_revenue_funds=available_revenue,
        available_principal_funds=available_principal,
        issuer_accounts=issuer_accounts,
        triggers=triggers,
    )


# ===========================================================================
# Cache + live extraction (the integration-gated seam)
# ===========================================================================


def _cache_path(deal_name: str, cache_dir: str | Path) -> Path:
    return Path(cache_dir) / f"notes-cash-{_slug(deal_name)}.json"


def _load_durable_cache(path: Path) -> NotesCashReport | None:
    if not path.exists():
        return None
    try:
        return NotesCashReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("Durable notes-cash cache read failed (%s): %s", path, exc)
        return None


def _write_durable_cache(report: NotesCashReport, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Durable notes-cash cache write failed (%s): %s", path, exc)


def _extract_pdf_text(url: str) -> str:  # pragma: no cover - network/integration only
    """Fetch a Notes & Cash report PDF and extract its full text with ``pypdf``.

    These reports are text-extractable (not scanned), so ``pypdf`` gets the text
    deterministically — no Docling/Gemini needed. Not exercised in the fast
    suite; :func:`parse_notes_cash_report` only reaches here on a cold cache,
    which the integration test triggers explicitly.
    """
    import io
    import urllib.request

    from pypdf import PdfReader

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (loanwhiz)"})
    data = urllib.request.urlopen(req, timeout=60).read()  # noqa: S310 - trusted ING URLs
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_notes_cash_report(
    deal_context: dict[str, Any],
    *,
    force_refresh: bool = False,
    cache_dir: str | Path = DEFAULT_EXTRACTION_CACHE_DIR,
) -> NotesCashReport:
    """Return the per-period Notes & Cash (liability) report set for a deal.

    Resolution order (cheapest first):

    1. **Durable cache** ``data/extraction_cache/notes-cash-{slug}.json`` — on hit
       (and not ``force_refresh``), load + validate + return. No network.
    2. **Live extraction** — fetch each ``notes_cash_report_urls`` PDF, extract
       its text with ``pypdf``, parse it, and persist to the durable cache.

    Parameters
    ----------
    deal_context:
        A deal-context dict (a ``DEAL_REGISTRY`` entry) with at least
        ``deal_name`` and ``notes_cash_report_urls``
        (``[{"period": str, "url": str}, ...]``).
    force_refresh:
        Bypass the cache and re-fetch / re-parse.
    cache_dir:
        Durable cache directory (default the repo's ``data/extraction_cache/``).

    Raises
    ------
    KeyError
        If ``deal_context`` lacks ``notes_cash_report_urls`` on a cold cache.
    """
    deal_name = deal_context["deal_name"]
    path = _cache_path(deal_name, cache_dir)

    if not force_refresh:
        cached = _load_durable_cache(path)
        if cached is not None:
            return cached

    report_urls = deal_context["notes_cash_report_urls"]
    periods: list[NotesCashPeriod] = []
    for entry in report_urls:
        period, url = entry["period"], entry["url"]
        text = _extract_pdf_text(url)
        periods.append(parse_report_text(text, period_label=period))

    report = NotesCashReport(deal_name=deal_name, periods=periods)
    _write_durable_cache(report, path)
    return report
