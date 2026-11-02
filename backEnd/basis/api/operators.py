"""The platform-operator check shared by the ops routes (``GET /v1/ops/health``,
``GET /v1/ops/client-errors``): a verified token whose uid is in ``OPS_OPERATOR_UIDS``
(docs/api/OPS_HEALTH.md section 1). A firm role, owner included, never grants it; 403
``not-operator`` (``E.API_NOT_OPERATOR``) carries no diagnostics. Its own module so a route
package module can depend on it without importing ``basis.api.routes.ops``, which imports
``basis.api.health``, which imports the routes package (a cycle)."""

from __future__ import annotations

import logging
from typing import Final

from fastapi import Request

from basis.api.auth import CurrentPrincipal
from basis.api.errors import Forbidden
from basis.auth.firebase import Principal
from basis.config import Settings
from basis.errors.codes import E

DETAIL_NOT_OPERATOR: Final = "This account is not a platform operator."

logger = logging.getLogger(__name__)


async def require_operator(request: Request, principal: CurrentPrincipal) -> Principal:
    settings: Settings = request.app.state.settings
    if principal.uid not in settings.ops_operator_uids:
        logger.info("operator route refused: not an operator", extra={"path": request.url.path})
        raise Forbidden(DETAIL_NOT_OPERATOR, type_suffix="not-operator", code=E.API_NOT_OPERATOR)
    return principal
