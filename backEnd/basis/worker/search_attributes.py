"""Register the StudyWorkflow's custom search attributes (``StudyId``, ``Stage``, ``OrgId``).

The workflow upserts them on every stage change; a namespace that does not define them
fails those workflow tasks ("search attribute Stage is not defined"), so the worker
registers them at startup on the dev server and in tests. The call is idempotent: names
that already exist with the right type are skipped, and an "already exists" race with
another worker is tolerated.

Temporal Cloud namespaces are configured outside the worker (``tcld`` or the Cloud UI,
see the S2 runbook): the operator API there needs an admin key the worker does not have.
"""

from __future__ import annotations

import logging
from typing import Final

from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesRequest,
)
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

logger = logging.getLogger(__name__)

KEYWORD: Final = IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
STUDY_SEARCH_ATTRIBUTES: Final[dict[str, IndexedValueType.ValueType]] = {
    "StudyId": KEYWORD,
    "Stage": KEYWORD,
    "OrgId": KEYWORD,
}


class SearchAttributeConflict(RuntimeError):
    """A search attribute exists with a different type; fixing it needs an operator."""


async def ensure_search_attributes(
    client: Client,
    attributes: dict[str, IndexedValueType.ValueType] | None = None,
) -> list[str]:
    """Add the missing attributes; return the names added (empty when all existed)."""
    wanted = dict(STUDY_SEARCH_ATTRIBUTES if attributes is None else attributes)
    namespace = client.namespace
    try:
        existing = await client.operator_service.list_search_attributes(
            ListSearchAttributesRequest(namespace=namespace)
        )
        defined = {**existing.system_attributes, **existing.custom_attributes}
    except RPCError as exc:
        # The time-skipping test server implements Add but not List; add everything
        # and rely on "already exists" being tolerated below.
        if exc.status != RPCStatusCode.UNIMPLEMENTED:
            raise
        defined = {}
    missing: dict[str, IndexedValueType.ValueType] = {}
    for name, value_type in wanted.items():
        if name not in defined:
            missing[name] = value_type
        elif defined[name] != value_type:
            found = IndexedValueType.Name(defined[name])
            expected = IndexedValueType.Name(value_type)
            raise SearchAttributeConflict(
                f"search attribute {name} exists in {namespace} with type {found}, "
                f"expected {expected}"
            )
    if not missing:
        return []
    try:
        await client.operator_service.add_search_attributes(
            AddSearchAttributesRequest(namespace=namespace, search_attributes=missing)
        )
    except RPCError as exc:
        if exc.status != RPCStatusCode.ALREADY_EXISTS:
            raise
        logger.info("search attributes were added concurrently: %s", ", ".join(missing))
    logger.info("registered search attributes in %s: %s", namespace, ", ".join(sorted(missing)))
    return sorted(missing)
