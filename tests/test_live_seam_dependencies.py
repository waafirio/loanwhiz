"""The live seams' third-party imports must be declared dependencies.

Both PDF-reading seams import their parser lazily, inside the function that
needs it:

- ``notes_cash_parser.parse_notes_cash_report`` (the RMBS Notes & Cash path)
- ``collateral_schedule_parser.extract_report_lines`` (the CLO derived-tape path)

Every other test in the suite feeds those parsers **committed text fixtures**,
which is deliberate — the fixtures make parsing deterministic and offline. But it
means the suite never executes the lazy import, so an undeclared PDF dependency
is invisible to it: 1967 tests pass while every live load raises
``ModuleNotFoundError``.

That is exactly what happened. ``pypdf`` was imported by both modules and
declared by neither, and it surfaced only when the derived CLO tape was loaded
end-to-end for the first time. These tests pin the *class* of defect — a live
seam depending on something the packaging does not guarantee — rather than the
one instance.
"""

from __future__ import annotations

import importlib

import pytest


# (module under test, the third-party module its live seam imports lazily)
_LIVE_SEAM_IMPORTS: tuple[tuple[str, str], ...] = (
    ("loanwhiz.primitives.notes_cash_parser", "pypdf"),
    ("loanwhiz.primitives.collateral_schedule_parser", "pypdf"),
)


@pytest.mark.parametrize(("owner", "dependency"), _LIVE_SEAM_IMPORTS)
def test_live_seam_dependency_is_importable(owner: str, dependency: str) -> None:
    """The lazily-imported parser must actually be installed.

    Reds with ``ModuleNotFoundError`` if the dependency is dropped from
    ``pyproject.toml`` — the failure the fixture-based tests cannot see.
    """
    importlib.import_module(owner)  # the seam's own module must import cleanly
    importlib.import_module(dependency)


def test_pdf_reader_opens_a_real_pdf() -> None:
    """`PdfReader` must open and page-parse a genuine PDF, not merely import.

    A declared-but-broken install — wrong major version, missing native bits —
    would sail through a bare import check. Writing a real PDF and reading it
    back exercises the capability the live seam actually relies on.
    """
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)

    reader = PdfReader(buf)
    assert len(reader.pages) == 1
    # extract_text() is the call both live seams make; it must not raise.
    assert reader.pages[0].extract_text() is not None
