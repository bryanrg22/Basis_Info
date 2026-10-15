"""``ensure_search_attributes``: idempotent registration of StudyId, Stage and OrgId."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesResponse,
)
from temporalio.service import RPCError, RPCStatusCode

from basis.worker.search_attributes import (
    STUDY_SEARCH_ATTRIBUTES,
    SearchAttributeConflict,
    ensure_search_attributes,
)

KEYWORD = IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
TEXT = IndexedValueType.INDEXED_VALUE_TYPE_TEXT


@dataclass
class FakeOperator:
    existing: dict[str, Any] = field(default_factory=dict)
    list_error: RPCError | None = None
    add_error: RPCError | None = None
    added: list[AddSearchAttributesRequest] = field(default_factory=list)

    async def list_search_attributes(self, request: Any) -> ListSearchAttributesResponse:
        if self.list_error:
            raise self.list_error
        return ListSearchAttributesResponse(custom_attributes=self.existing)

    async def add_search_attributes(self, request: AddSearchAttributesRequest) -> None:
        self.added.append(request)
        if self.add_error:
            raise self.add_error


@dataclass
class FakeClient:
    operator_service: FakeOperator
    namespace: str = "default"


def test_the_study_attributes_are_keywords() -> None:
    assert STUDY_SEARCH_ATTRIBUTES == {"StudyId": KEYWORD, "Stage": KEYWORD, "OrgId": KEYWORD}


async def test_adds_only_the_missing_attributes() -> None:
    operator = FakeOperator(existing={"StudyId": KEYWORD})
    added = await ensure_search_attributes(FakeClient(operator))  # type: ignore[arg-type]
    assert added == ["OrgId", "Stage"]
    [request] = operator.added
    assert dict(request.search_attributes) == {"Stage": KEYWORD, "OrgId": KEYWORD}
    assert request.namespace == "default"


async def test_nothing_to_do_when_all_exist() -> None:
    operator = FakeOperator(existing=dict(STUDY_SEARCH_ATTRIBUTES))
    assert await ensure_search_attributes(FakeClient(operator)) == []  # type: ignore[arg-type]
    assert operator.added == []


async def test_a_type_conflict_is_reported_not_overwritten() -> None:
    operator = FakeOperator(existing={"Stage": TEXT})
    with pytest.raises(SearchAttributeConflict, match="Stage"):
        await ensure_search_attributes(FakeClient(operator))  # type: ignore[arg-type]
    assert operator.added == []


async def test_concurrent_registration_is_tolerated() -> None:
    operator = FakeOperator(add_error=RPCError("exists", RPCStatusCode.ALREADY_EXISTS, b""))
    added = await ensure_search_attributes(FakeClient(operator))  # type: ignore[arg-type]
    assert added == ["OrgId", "Stage", "StudyId"]


async def test_servers_without_list_get_everything_added() -> None:
    operator = FakeOperator(list_error=RPCError("no", RPCStatusCode.UNIMPLEMENTED, b""))
    assert len(await ensure_search_attributes(FakeClient(operator))) == 3  # type: ignore[arg-type]


@pytest.mark.parametrize("where", ["list", "add"])
async def test_other_errors_propagate(where: str) -> None:
    error = RPCError("denied", RPCStatusCode.PERMISSION_DENIED, b"")
    operator = FakeOperator(**{f"{where}_error": error})
    with pytest.raises(RPCError):
        await ensure_search_attributes(FakeClient(operator))  # type: ignore[arg-type]
