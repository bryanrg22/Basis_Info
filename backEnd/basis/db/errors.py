"""Domain exceptions raised by the repositories.

They carry no HTTP knowledge; the API maps them (suggested: ``NotVisibleError`` -> 404,
``ConflictError`` and subclasses -> 409, ``InvalidCursorError`` -> 422,
``EmailUnverifiedError`` -> 403).
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


class RepositoryError(Exception):
    """Base class for every error a repository raises on purpose."""


class NotVisibleError(RepositoryError):
    """The row does not exist, or exists but is not visible to the caller.

    The two cases are deliberately indistinguishable (S1_SPEC §4: 404, not 403).
    """


class ConflictError(RepositoryError):
    """The write conflicts with the current state of the database."""


class StaleStateError(ConflictError):
    """A compare-and-set lost: the row is no longer in the expected state.

    Raised by ``StudyRepository.transition`` when the study is not at ``from_stage`` and
    ``expected_revision`` (or is archived), and by ``ReviewRepository.record_decision``
    when the stage/revision was already decided.
    """


class GateNotOpenError(StaleStateError):
    """The study is at a review stage but the workflow has not opened its gate yet (the
    work the gate reviews is still running, or a stage failed): no decision is accepted."""


class DuplicateDocumentError(ConflictError):
    """A document with the same sha256 (or storage URI) already exists in the study."""


class DuplicateKeyError(ConflictError):
    """An idempotency key was already used for a different write."""


class LastOwnerError(ConflictError):
    """The change would leave a firm without an owner (demoting or removing the last one)."""


class AlreadyMemberError(ConflictError):
    """An invitation names an email that already belongs to a member of the firm."""


class InvitationPendingError(ConflictError):
    """The firm already has a pending invitation for this email."""


class InvitationClosedError(ConflictError):
    """The invitation can no longer be answered: it was accepted, declined, revoked or it
    expired. ``status`` is that derived status (``basis.db.repositories.invitation``)."""

    def __init__(self, message: str, *, status: str) -> None:
        super().__init__(message)
        self.status = status


class PreconditionFailedError(RepositoryError):
    """A conditional write's condition is false for the row as it is (the intake's
    ``If-Match``); nothing was written. ``version`` is the row's current version. Not a
    ``ConflictError``: the route answers 412, not 409."""

    def __init__(self, message: str, *, version: int) -> None:
        super().__init__(message)
        self.version = version


class EmailUnverifiedError(RepositoryError):
    """The caller's token does not assert a verified email, which an invitation needs."""


class DatabaseUnavailableError(Exception):
    """A new database connection could not be established (the server is unreachable).

    Raised by the engine (``basis.db.engine``) only while opening a connection, for
    network failures and the server's own "cannot accept connections now" answers;
    ``__cause__`` is the driver's error. Errors on an established connection (a failed
    query, a connection dropped mid-transaction) are never translated. Not a
    ``RepositoryError``: repositories do not raise it on purpose. The API answers 503.
    The message never contains the database URL or credentials.
    """


class InvalidCursorError(RepositoryError, ValueError):
    """A pagination cursor could not be decoded."""


def constraint_name(exc: IntegrityError) -> str | None:
    """Name of the violated constraint, as reported by the driver, or None.

    SQLAlchemy's asyncpg adapter wraps the asyncpg exception: the adapted error is
    ``exc.orig`` and the original asyncpg error (which has ``constraint_name``) is its
    ``__cause__``.
    """
    candidates: list[object] = [exc.orig]
    if exc.orig is not None:
        candidates.append(exc.orig.__cause__)
    for candidate in candidates:
        name = getattr(candidate, "constraint_name", None)
        if isinstance(name, str) and name:
            return name
    return None
