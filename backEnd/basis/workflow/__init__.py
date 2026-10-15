"""Temporal workflow package: contracts, the StudyWorkflow, and helpers.

Workflow code must stay deterministic: modules in this package import only the
standard library, pydantic (passed through the sandbox), the Temporal SDK, and
``basis.workflow.contracts``. Provider and database clients live in
``basis.activities``.
"""
