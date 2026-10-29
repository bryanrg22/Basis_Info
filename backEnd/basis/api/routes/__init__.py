"""The ``/v1`` routers, in the order they are mounted."""

from __future__ import annotations

from fastapi import APIRouter

from basis.api.routes import (
    decisions,
    documents,
    evidence,
    objects,
    orgs,
    outputs,
    result,
    spend,
    studies,
    timeline,
    workflow,
)


def api_routers() -> list[APIRouter]:
    return [
        studies.reads,
        studies.writes,
        documents.reads,
        documents.writes,
        decisions.writes,
        evidence.reads,
        outputs.reads,
        result.reads,
        spend.reads,
        timeline.reads,
        objects.reads,
        objects.writes,
        workflow.writes,
        workflow.reads,
        orgs.reads,
        orgs.writes,
    ]
