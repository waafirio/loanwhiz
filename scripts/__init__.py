"""Offline driver / seeding scripts for loanwhiz.

These are operational entry points (deal extraction, seeding, reconciliation)
that *drive* the ``loanwhiz`` package rather than being part of it. This file
marks the directory as a package so the scripts are importable from tests —
e.g. the #384 "one extraction path, not a fork" check imports
``scripts.extract_c2_deals`` to assert it shares ``extract_deal_model`` with
the on-demand job.
"""
