"""The ``/v1`` routers, in the order they are mounted."""

from __future__ import annotations

from fastapi import APIRouter

from basis.api.routes import decisions, documents, evidence, studies


def api_routers() -> list[APIRouter]:
    return [
        studies.reads,
        studies.writes,
        documents.writes,
        decisions.writes,
        evidence.reads,
    ]
