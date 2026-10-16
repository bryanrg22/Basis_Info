"""The ``/v1`` routers, in the order they are mounted."""

from __future__ import annotations

from fastapi import APIRouter

from basis.api.routes import decisions, documents, evidence, objects, studies, workflow


def api_routers() -> list[APIRouter]:
    return [
        studies.reads,
        studies.writes,
        documents.writes,
        decisions.writes,
        evidence.reads,
        objects.reads,
        objects.writes,
        workflow.writes,
        workflow.reads,
    ]
