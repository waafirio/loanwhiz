"""A registered tape identifier must never be handed to a raw file reader.

The defect this pins
--------------------
A tape URL in the registry is an **identifier**, not a path. Since #470 it may
carry a provenance scheme that states what the tape *is* —
``derived+trustee-report:`` (no published file; rows reconstructed from a
trustee report) and, from #483, ``synthetic:`` (a published file whose rows
describe no real obligor). :mod:`loanwhiz.domain.tape_provenance` owns the
rules; :func:`~loanwhiz.primitives.esma_tape_normaliser._load_tape` is the one
ingestion seam that applies them.

Three readers held the identifier and called ``pandas`` on it directly:
``collections_aggregator._load_tape`` (behind ``GET /deal/{id}/collections``
and the agent tool), ``loanwhiz.data.green_lion.load_tape``, and the tape
section of ``demo/run_green_lion.py``. Re-identifying Green Lion's tapes broke
all three — and the suite stayed green, because every test touching those paths
needs the network and so never runs them.

That false-green is the point. It is the same class the repo already recorded
for the undeclared ``pypdf`` import (``tests/test_live_seam_dependencies.py``,
whose docstring names the class rather than the instance): *a live seam whose
only coverage is a test that never executes it*. So these tests pin the class —
"a reader bypassed the seam" — not the three instances, in two layers:

1. **Static (fully general).** Every ``pandas`` reader call under ``src/``,
   ``demo/``, ``scripts/`` and ``mcp/`` must be handed a path that went through
   :func:`~loanwhiz.domain.tape_provenance.underlying_url`. This needs no test
   to *exercise* the reader, so it catches a bypassing loader written tomorrow
   that nothing offline calls — exactly the hole the three loaders sat in.
2. **Behavioural (offline).** Every registered tape identifier is driven
   through every tape-reading entrypoint with ``pandas`` and the deriver
   replaced by tripwires, so the paths run end to end with no network. A scheme
   reaching ``pandas`` fails; so does a trustee-report PDF.

What is **not** covered, said plainly: layer 1 knows only ``pandas`` readers, so
a reader built on ``requests``/``fsspec``/``open`` would pass it; layer 2's
entrypoint list is enumerated by hand, so a new entrypoint is covered by layer 1
alone until someone adds it here. Neither layer says anything about a tape read
outside this repository.

Both layers carry an anti-vacuity test: a checker that quietly matches nothing,
or a tripwire that quietly never arms, is a green test proving nothing, which is
the failure mode this file exists to end.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

# Import a ``primitives`` module before ``loanwhiz.domain`` so the package-init
# import cycle resolves. Same convention as ``tests/test_tape_provenance.py``.
import loanwhiz.primitives  # noqa: F401  (import-order side effect)

from loanwhiz.config import DEAL_REGISTRY, GREEN_LION
from loanwhiz.data import green_lion
from loanwhiz.domain.tape_provenance import TapeScheme, scheme_for, underlying_url
from loanwhiz.primitives import collections_aggregator, esma_tape_normaliser

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Layer 1 — the static check
# ---------------------------------------------------------------------------

#: Directories whose readers must resolve an identifier before reading it.
#: ``tests/`` is deliberately absent: a test may hand a reader a literal path or
#: a buffer, and it is the code under test, not the test, that must hold the
#: contract.
_SCANNED_DIRS: tuple[str, ...] = ("src/loanwhiz", "demo", "scripts", "mcp")

#: ``pandas`` entrypoints that turn a path into a frame. Any of them handed a
#: registered identifier is the bypass.
_PANDAS_READERS: frozenset[str] = frozenset(
    {
        "read_csv",
        "read_parquet",
        "read_excel",
        "read_json",
        "read_table",
        "read_feather",
        "read_fwf",
        "read_orc",
        "read_stata",
        "read_xml",
        "read_pickle",
        "read_html",
    }
)

#: Keywords each reader accepts for the thing it reads, when it is not passed
#: positionally.
_PATH_KEYWORDS: frozenset[str] = frozenset(
    {"filepath_or_buffer", "path", "path_or_buf", "io", "source"}
)

#: The one function that turns an identifier into something readable.
_RESOLVER = "underlying_url"

#: Deliberate exceptions, as ``"<path>:<line>" -> reason``. Empty on purpose:
#: the invariant currently holds everywhere with no carve-out. An entry is a
#: **claim that the read cannot receive a registered identifier** — a bundled
#: fixture, a file this repo just wrote — and costs a sentence of justification,
#: which is the point: widening this must be a deliberate act someone reviews,
#: not a silent one.
_ALLOWED: dict[str, str] = {}


def _reader_name(func: ast.expr) -> str | None:
    """The pandas reader *func* names, or ``None`` if it names none."""
    if isinstance(func, ast.Attribute) and func.attr in _PANDAS_READERS:
        # ``pd.read_csv`` / ``pandas.read_csv``. Anything else with the same
        # attribute name (``self.read_csv``) is not the pandas entrypoint.
        if isinstance(func.value, ast.Name) and func.value.id in {"pd", "pandas"}:
            return func.attr
        return None
    if isinstance(func, ast.Name) and func.id in _PANDAS_READERS:
        # ``from pandas import read_csv``.
        return func.id
    return None


def _is_resolver_call(node: ast.expr | None) -> bool:
    """Whether *node* is a call to :func:`underlying_url`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == _RESOLVER
    if isinstance(func, ast.Attribute):
        return func.attr == _RESOLVER
    return False


def _resolved_names(scope: ast.AST) -> set[str]:
    """Names bound in *scope* whose **every** binding is ``underlying_url(...)``.

    Every binding, not any: a name assigned the resolved path on one branch and
    the raw identifier on another is not resolved. Nested function bodies are
    skipped — they bind their own names.
    """
    bindings: dict[str, list[ast.expr | None]] = {}

    def note(target: ast.expr, value: ast.expr | None) -> None:
        if isinstance(target, ast.Name):
            bindings.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            # Unpacking cannot be traced to a resolver call; record it as an
            # opaque binding so the name is not treated as resolved.
            for element in target.elts:
                note(element, None)

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    note(target, child.value)
            elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
                note(child.target, child.value)
            elif isinstance(child, ast.NamedExpr):
                note(child.target, child.value)
            elif isinstance(child, (ast.For, ast.AsyncFor)):
                note(child.target, None)
            elif isinstance(child, (ast.With, ast.AsyncWith)):
                for item in child.items:
                    if item.optional_vars is not None:
                        note(item.optional_vars, None)
            visit(child)

    visit(scope)
    return {
        name
        for name, values in bindings.items()
        if values and all(_is_resolver_call(value) for value in values)
    }


def _reader_calls(source: str) -> list[tuple[ast.Call, ast.AST]]:
    """Every pandas reader call in *source*, paired with its enclosing scope."""
    found: list[tuple[ast.Call, ast.AST]] = []

    def visit(node: ast.AST, scope: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else scope
            )
            if isinstance(child, ast.Call) and _reader_name(child.func) is not None:
                found.append((child, scope))
            visit(child, inner)

    tree = ast.parse(source)
    visit(tree, tree)
    return found


def _bypasses(source: str, label: str) -> list[str]:
    """Reader calls in *source* handed something that never met the resolver."""
    offenders: list[str] = []
    for call, scope in _reader_calls(source):
        if call.args:
            path: ast.expr | None = call.args[0]
        else:
            path = next(
                (kw.value for kw in call.keywords if kw.arg in _PATH_KEYWORDS),
                None,
            )
        if path is None:
            # No path argument at all — nothing to resolve.
            continue
        if isinstance(path, ast.Constant):
            # A literal cannot be a registered identifier.
            continue
        if _is_resolver_call(path):
            continue
        if isinstance(path, ast.Name) and path.id in _resolved_names(scope):
            continue
        where = f"{label}:{call.lineno}"
        if where in _ALLOWED:
            continue
        reader = _reader_name(call.func)
        offenders.append(f"{where}: pd.{reader}({ast.unparse(path)})")
    return offenders


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for directory in _SCANNED_DIRS:
        root = _REPO_ROOT / directory
        if root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return files


def test_no_reader_is_handed_an_unresolved_tape_identifier() -> None:
    """The general guard: every pandas read must resolve its identifier first.

    Static, so it does not depend on any test *exercising* the reader — which is
    precisely why it would have caught all three #483 bypasses, none of which
    the offline suite runs. It reds on a loader added tomorrow, in a module with
    no tests at all.
    """
    offenders: list[str] = []
    for file in _scanned_files():
        label = str(file.relative_to(_REPO_ROOT))
        offenders.extend(_bypasses(file.read_text(), label))

    assert not offenders, (
        "these readers are handed a value that never passed through "
        f"{_RESOLVER}(), so a registered tape identifier reaching them is read "
        f"as a file path and raises: {offenders}. A tape URL is an identifier, "
        "not a path — resolve it through the ingestion seam "
        "(esma_tape_normaliser._load_tape) or, for a direct read, through "
        f"loanwhiz.domain.tape_provenance.{_RESOLVER}."
    )


def test_the_static_check_reds_on_a_bypass() -> None:
    """The mutant, kept in the suite: a bypassing reader must be reported.

    Without this, ``_bypasses`` degrading to "always returns []" — a renamed
    pandas alias, a widened exemption — would leave the guard above green while
    guarding nothing.
    """
    bypass = (
        "import pandas as pd\n"
        "def load(entry):\n"
        "    url = entry['url']\n"
        "    return pd.read_csv(url, low_memory=False)\n"
    )
    assert _bypasses(bypass, "mutant.py") == [
        "mutant.py:4: pd.read_csv(url)"
    ]

    # ...and the resolved forms it must *not* report, so the check is not
    # simply "flag every read".
    for resolved in (
        "import pandas as pd\n"
        "def load(entry):\n"
        "    return pd.read_csv(underlying_url(entry['url']))\n",
        "import pandas as pd\n"
        "def load(entry):\n"
        "    target = underlying_url(entry['url'])\n"
        "    return pd.read_csv(target, low_memory=False)\n",
        "import pandas as pd\n"
        "def load(entry):\n"
        "    return pd.read_csv(filepath_or_buffer=underlying_url(entry['url']))\n",
    ):
        assert _bypasses(resolved, "ok.py") == []


def test_the_static_check_reds_when_only_one_branch_resolves() -> None:
    """A name resolved on one path and raw on another is not resolved.

    The likeliest way the fix rots: someone adds a branch that assigns the bare
    identifier and the read keeps compiling.
    """
    half_resolved = (
        "import pandas as pd\n"
        "def load(entry, strip):\n"
        "    if strip:\n"
        "        target = underlying_url(entry['url'])\n"
        "    else:\n"
        "        target = entry['url']\n"
        "    return pd.read_csv(target)\n"
    )
    assert _bypasses(half_resolved, "half.py") == ["half.py:7: pd.read_csv(target)"]


def test_the_static_check_actually_finds_the_repository_readers() -> None:
    """The scan must locate real reader calls, or it passes by finding nothing.

    A green ``test_no_reader_is_handed_an_unresolved_tape_identifier`` means
    something only if the scan is looking at code that reads tapes. This names
    the seam explicitly: if its reads move or are renamed away, this reds and
    sends someone to re-point the guard rather than letting it idle.
    """
    seam = _REPO_ROOT / "src/loanwhiz/primitives/esma_tape_normaliser.py"
    seam_readers = {
        _reader_name(call.func) for call, _ in _reader_calls(seam.read_text())
    }
    assert {"read_csv", "read_parquet"} <= seam_readers, (
        "the ingestion seam no longer contains the pandas reads this guard is "
        f"built around (found {sorted(seam_readers)}); re-point the scan."
    )

    scanned = _scanned_files()
    assert any(
        path.name == "run_green_lion.py" and path.parent.name == "demo"
        for path in scanned
    ), "the demo is one of the three #483 bypasses; the scan must cover it"


# ---------------------------------------------------------------------------
# Layer 2 — the offline behavioural guard
# ---------------------------------------------------------------------------


class SeamBypass(AssertionError):
    """Raised when a reader is handed something the seam should have resolved."""


def _stub_tape() -> pd.DataFrame:
    """A minimal well-formed tape, standing in for whatever the URL names.

    Columns are the ones the readers under test actually touch, so each path
    runs to completion offline. Values are arbitrary: this guard is about
    *which string reaches the reader*, never about the numbers.
    """
    return pd.DataFrame(
        {
            "loan_id": ["L1", "L2", "L3"],
            "reporting_date": ["2026-04-30"] * 3,
            "current_balance": [100_000.0, 200_000.0, 300_000.0],
            "current_interest_rate": [3.1, 3.4, 3.9],
            "cltomv_current": [65.0, 72.5, 80.0],
            "arrears_amount": [0.0, 0.0, 1_500.0],
            "days_past_due": [0, 0, 45],
            "arrears_bucket": ["current", "current", "1-2m"],
            "epc_label": ["A", "B", "C"],
            "seasoning_months": [12, 30, 48],
            "account_status": ["performing", "performing", "arrears"],
        }
    )


class _StubDerivedTape:
    """What the deriver returns, reduced to what the seam reads off it."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.columns = tuple(frame.columns)
        self.rows = frame.to_dict("records")


@pytest.fixture
def tripwire(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Replace every way a tape's bytes are fetched with an offline tripwire.

    ``pandas`` and the trustee-report deriver both stop here, so a path under
    test runs end to end with no network — which is what makes these readers
    testable at all, and what their absence of coverage cost.

    Fails loudly rather than returning a frame when:

    - a **provenance scheme** reaches ``pandas``: the reader treated an
      identifier as a path, which is #483 recurring;
    - a **source document** reaches ``pandas``: the reader stripped the scheme
      off a derived URI and handed the trustee-report PDF to the CSV parser —
      the failure ``underlying_url`` alone would have introduced.

    Returns the recorded paths, so a test can assert what was fetched rather
    than only what was not.
    """
    seen: dict[str, list[str]] = {"pandas": [], "derived": []}

    def guard(path: object, *args: object, **kwargs: object) -> pd.DataFrame:
        text = str(path)
        scheme = scheme_for(text)
        if scheme is not None:
            raise SeamBypass(
                f"a reader was handed the raw identifier {text!r}: the "
                f"{scheme.value!r} scheme declares what the tape is and is not "
                "part of any path. Read it through the ingestion seam, or "
                f"resolve it with tape_provenance.{_RESOLVER}()."
            )
        if text.split("?")[0].split("#")[0].lower().endswith(".pdf"):
            raise SeamBypass(
                f"a reader was handed the source document {text!r}. A derived "
                "tape names a report to be derived into rows, not a file to "
                "parse: stripping its scheme is not enough, it must go through "
                "the ingestion seam."
            )
        seen["pandas"].append(text)
        return _stub_tape()

    for reader in ("read_csv", "read_parquet"):
        monkeypatch.setattr(pd, reader, guard)

    def stub_derive(uri: str, *args: object, **kwargs: object) -> _StubDerivedTape:
        seen["derived"].append(uri)
        return _StubDerivedTape(_stub_tape())

    monkeypatch.setattr(
        esma_tape_normaliser.derived_tape, "derive_tape", stub_derive
    )
    return seen


def _registered_tape_urls() -> list[str]:
    """Every tape identifier the registry holds, across every deal."""
    urls: list[str] = []
    for deal in DEAL_REGISTRY.values():
        for tape in deal.get("tape_urls") or []:
            url = tape.get("url")
            if url:
                urls.append(url)
    return urls


def _schemed_tape_urls() -> list[str]:
    return [url for url in _registered_tape_urls() if scheme_for(url) is not None]


def _synthetic_tape_urls() -> list[str]:
    return [
        url
        for url in _registered_tape_urls()
        if scheme_for(url) is TapeScheme.SYNTHETIC
    ]


def test_the_registry_actually_holds_schemed_tapes() -> None:
    """Anti-vacuity: the parametrised guards below must receive real input.

    Every behavioural test here is parametrised over registered identifiers. If
    the registry stopped declaring any, pytest would collect zero cases and
    report green — an invariant that cannot fail because nothing reaches it.
    This reds instead.
    """
    schemed = _schemed_tape_urls()
    assert schemed, (
        "no registered tape declares a provenance scheme, so every behavioural "
        "guard in this file is vacuous. Either the registry regressed or this "
        "file needs re-pointing."
    )
    assert _synthetic_tape_urls(), (
        "no registered tape declares the synthetic: scheme — the identifier "
        "whose introduction broke three readers in #483."
    )


def test_the_tripwire_fires_on_a_raw_identifier(
    tripwire: dict[str, list[str]],
) -> None:
    """The mutant for layer 2: the tripwire must arm.

    A fixture that silently returned a frame for anything would make every test
    below pass while proving nothing. This is the bypass itself, performed.
    """
    raw = _synthetic_tape_urls()[0]
    with pytest.raises(SeamBypass):
        pd.read_csv(raw)
    with pytest.raises(SeamBypass):
        pd.read_csv("https://example.invalid/trustee-report.pdf")
    # The resolved form is what a fixed reader passes, and must go through.
    assert not pd.read_csv(underlying_url(raw)).empty


@pytest.mark.parametrize("url", _schemed_tape_urls())
def test_the_seam_never_leaks_a_scheme_to_a_reader(
    url: str, tripwire: dict[str, list[str]]
) -> None:
    """The one ingestion seam handles every registered identifier offline."""
    frame, channel = esma_tape_normaliser._load_tape(url, None)
    assert not frame.empty
    assert channel  # the channel comes from the identifier, never from a branch


@pytest.mark.parametrize("url", _schemed_tape_urls())
def test_the_collections_reader_never_leaks_a_scheme(
    url: str, tripwire: dict[str, list[str]]
) -> None:
    """``GET /deal/{id}/collections`` and the agent tool read through the seam.

    Both the synthetic and the derived identifier reach here in production — the
    endpoint runs over whatever tapes a deal registers, and the CLO deal
    registers derived ones. Before #483 this called ``pandas`` on the raw
    identifier and raised for both.
    """
    frame = collections_aggregator._load_tape(url)
    assert not frame.empty
    assert list(frame.columns) == [c.lower() for c in frame.columns]


def test_a_derived_identifier_reaches_the_deriver_not_a_parser(
    tripwire: dict[str, list[str]],
) -> None:
    """A derived tape must be *derived*, not stripped and parsed.

    This is what makes the collections fix a delegation to the seam rather than
    a call to ``underlying_url``: the resolver would have handed the CSV parser
    a trustee-report PDF. Reds if the reader ever resolves-and-parses instead.
    """
    derived = [
        url
        for url in _registered_tape_urls()
        if scheme_for(url) is TapeScheme.TRUSTEE_REPORT
    ]
    assert derived, "no registered tape is derived; this guard has no input"

    collections_aggregator._load_tape(derived[0])
    assert tripwire["derived"] == [derived[0]]
    assert tripwire["pandas"] == []


@pytest.mark.parametrize("entry", GREEN_LION["tape_urls"], ids=lambda e: e["date"])
def test_the_green_lion_loader_reads_the_file_the_identifier_names(
    entry: dict[str, str], tripwire: dict[str, list[str]]
) -> None:
    """``loanwhiz.data.green_lion.load_tape`` — the second #483 bypass.

    Asserting the exact string ``pandas`` received, not merely that nothing
    raised: the file fetched must be the one the identifier names, unchanged
    apart from the scheme.
    """
    frame = green_lion.load_tape(entry["date"])
    assert not frame.empty
    assert tripwire["pandas"] == [underlying_url(entry["url"])]


def test_the_demo_tape_section_runs_offline(
    tripwire: dict[str, list[str]],
) -> None:
    """``demo/run_green_lion.py`` — the third #483 bypass.

    The demo's tape section had no offline coverage at all: its only test is
    marked ``slow`` and fetches the live tapes, so the broken read shipped
    green. Running the section under the tripwire gives it coverage that does
    not need the network, and asserts it fetched exactly the files the
    registered identifiers name.
    """
    spec = importlib.util.spec_from_file_location(
        "run_green_lion_seam_guard", _REPO_ROOT / "demo" / "run_green_lion.py"
    )
    assert spec is not None and spec.loader is not None
    demo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = demo
    try:
        spec.loader.exec_module(demo)
        metrics = demo.section_esma_analytics()
    finally:
        sys.modules.pop(spec.name, None)

    assert metrics, "the demo section produced no tape metrics"
    assert tripwire["pandas"] == [
        underlying_url(entry["url"]) for entry in demo.reporting_tapes()
    ]
