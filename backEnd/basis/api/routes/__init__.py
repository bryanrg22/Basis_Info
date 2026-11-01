"""The ``/v1`` routers, in the order they are mounted."""

from __future__ import annotations

from fastapi import APIRouter

from basis.api.routes import (
    activity,
    cost_indices,
    decisions,
    documents,
    drafts,
    evidence,
    objects,
    orgs,
    outputs,
    report,
    reprice,
    result,
    revisions,
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
        drafts.reads,
        drafts.writes,
        reprice.reads,
        reprice.writes,
        evidence.reads,
        outputs.reads,
        result.reads,
        report.reads,
        report.writes,
        spend.reads,
        timeline.reads,
        revisions.reads,
        revisions.writes,
        activity.reads,
        objects.reads,
        objects.writes,
        workflow.writes,
        workflow.reads,
        orgs.reads,
        orgs.writes,
        cost_indices.reads,
        cost_indices.writes,
    ]
