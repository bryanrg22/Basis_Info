"""Stable error codes (``BASIS-<area>-<nnn>``) and the catalogue behind ``docs/ERROR_CODES.md``.

:mod:`basis.errors.codes` is pure (standard library only) so the workflow, the activities
and the API all import the same constants; :mod:`basis.errors.catalogue` describes each code
for the operator (where it occurs, what happened, what the user sees, how to fix it, whether a
retry helps) and renders the document.
"""
