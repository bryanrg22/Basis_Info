"""What the public-geo workflow and its activities pass each other (pydantic, carried by the
pydantic data converter like :mod:`basis.capture.contracts`)."""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict

GEO_WORKFLOW: Final = "PublicGeoWorkflow"
GATHER_ACTIVITY: Final = "gather_public_geo"
FAIL_ACTIVITY: Final = "fail_public_geo"
WORKFLOW_ID_PREFIX: Final = "public-geo-"


def geo_workflow_id(gather_id: object) -> str:
    """``public-geo-{gather id}``: one execution per gather."""
    return f"{WORKFLOW_ID_PREFIX}{gather_id}"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GatherInput(_Frozen):
    gather_id: str


class GatherResult(_Frozen):
    status: str


class FailInput(_Frozen):
    gather_id: str
    code: str
    detail: str
