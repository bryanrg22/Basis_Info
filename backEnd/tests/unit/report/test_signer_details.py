"""Signer details without a database (EVAL_REPORT 91).

* :class:`SignerDetails`: the 55 NCEES licensing jurisdictions and nothing else, a license
  number only with its state, values trimmed, ``matches`` on the four fields only.
* The report's preparer: a signature's engineer, else the reader's saved details, else the
  firm's deprecated engineer; a license number with no state prints no "(None)".
* :func:`basis.api.routes.report._signer`: which details a signing prints, and its refusals.
* ``changed_since_signing`` compares the signer's details by value.
* ``REPORT_SIGNER_DETAILS_REQUIRED``: false by default, read from the environment.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from basis.api.errors import Conflict
from basis.api.routes.report import Signer, _signer
from basis.config import load_settings, reset_settings
from basis.db.models import ReportSignature, SignerDetailsRow
from basis.db.repositories.report import ReportRepository
from basis.errors.codes import E
from basis.report.inputs import (
    US_LICENSING_JURISDICTIONS,
    Engineer,
    FirmProfile,
    ReportInputs,
    Signature,
    SignerDetails,
)
from basis.report.model import build_report
from basis.report.render import view
from tests.unit.report.support import export

DETAILS: dict[str, Any] = {
    "name": "Alice Licensee",
    "credentials": "PE",
    "license_number": "PE-012345",
    "license_state": "NY",
}
USPS_STATES_AND_TERRITORIES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC GU MP PR VI AS"
).split()


# --------------------------------------------------------------------- the model


def test_the_jurisdictions_are_the_55_ncees_boards() -> None:
    assert len(US_LICENSING_JURISDICTIONS) == 55
    assert set(USPS_STATES_AND_TERRITORIES) - US_LICENSING_JURISDICTIONS == {"AS"}


@pytest.mark.parametrize("code", sorted(US_LICENSING_JURISDICTIONS))
def test_every_jurisdiction_is_accepted(code: str) -> None:
    assert SignerDetails(**{**DETAILS, "license_state": code}).license_state == code


@pytest.mark.parametrize("code", ["AS", "XX", "ny", "Ny", "N", "NYC", "", "  ", "N1"])
def test_anything_else_is_refused(code: str) -> None:
    with pytest.raises(ValidationError):
        SignerDetails(**{**DETAILS, "license_state": code})


@pytest.mark.parametrize(
    "license", [{"license_number": None}, {"license_state": None}, {"license_number": "  "}]
)
def test_a_number_and_a_state_come_together(license: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SignerDetails(**{**DETAILS, **license})


def test_no_license_at_all_is_allowed_and_values_are_trimmed() -> None:
    details = SignerDetails(name=" Alice ", credentials=" CCSP")
    assert (details.name, details.credentials, details.license_number) == ("Alice", "CCSP", None)
    assert SignerDetails(**{**DETAILS, "license_state": " NY "}).license_state == "NY"


def test_matches_compares_the_four_fields_trimmed_and_ignores_email() -> None:
    details = SignerDetails(**DETAILS)
    assert details.matches(Engineer(**DETAILS))
    assert details.matches(Engineer(**{**DETAILS, "name": " Alice Licensee ", "email": "a@x"}))
    assert details.matches({**DETAILS, "email": None})
    for key, other in (
        ("name", "Bob"),
        ("credentials", "PE, CCSP"),
        ("license_number", "1"),
        ("license_state", "NJ"),
    ):
        assert not details.matches(Engineer(**{**DETAILS, key: other})), key
    assert not details.matches({"name": "Alice Licensee", "credentials": "PE"})
    assert details.engineer() == Engineer(**DETAILS)


def test_the_firm_profiles_engineer_is_deprecated_in_the_schema() -> None:
    schema = FirmProfile.model_json_schema()
    assert schema["properties"]["engineer"]["deprecated"] is True


# --------------------------------------------------------------------- the report's preparer


def _engineer_view(inputs: ReportInputs) -> dict[str, str]:
    engineer: dict[str, str] = view(build_report(export(), inputs))["engineer"]
    return engineer


FIRM = FirmProfile(name="Firm", engineer=Engineer(name="Firm Engineer", credentials="PE"))


def test_the_preparer_is_the_signer_then_the_reader_then_the_firms_engineer() -> None:
    reader = SignerDetails(**DETAILS).engineer()
    signer = Engineer(name="Signed Engineer", credentials="PE")
    signature = Signature(engineer=signer, signed_at=datetime(2026, 11, 5, tzinfo=UTC))
    assert _engineer_view(ReportInputs(firm=FIRM))["name"] == "Firm Engineer"
    assert _engineer_view(ReportInputs(firm=FIRM, preparer=reader))["name"] == "Alice Licensee"
    signed = ReportInputs(firm=FIRM, preparer=reader, signature=signature)
    assert _engineer_view(signed)["name"] == "Signed Engineer"
    assert _engineer_view(ReportInputs())["name"] == "[engineer name]"


def test_a_license_number_without_a_state_prints_no_none() -> None:
    typed = Engineer(name="A", credentials="PE", license_number="123")
    preparer = ReportInputs(preparer=typed)
    assert _engineer_view(preparer)["license"] == "License 123"
    full = ReportInputs(preparer=SignerDetails(**DETAILS).engineer())
    assert _engineer_view(full)["license"] == "License PE-012345 (NY)"


# --------------------------------------------------------------------- which details sign


def _saved(details: dict[str, Any] = DETAILS, version: int = 3) -> SignerDetailsRow:
    return SignerDetailsRow(user_id=uuid.uuid4(), version=version, **details)


def test_saved_details_are_printed_with_their_version() -> None:
    signer = _signer(_saved(), None, required=False)
    assert signer.engineer == Engineer(**DETAILS) and signer.version == 3
    assert _signer(_saved(), Engineer(**DETAILS), required=True).version == 3


def test_a_different_sent_engineer_is_refused_when_details_are_saved() -> None:
    with pytest.raises(Conflict) as refused:
        _signer(_saved(), Engineer(name="Someone Else", credentials="PE"), required=False)
    assert refused.value.code == E.REPORT_SIGNER_DETAILS_MISMATCH


def test_without_saved_details_the_sent_engineer_unless_required() -> None:
    sent = Engineer(name="Typed", credentials="PE")
    assert _signer(None, sent, required=False) == Signer(engineer=sent, version=None)
    for sent_or_none, required in ((sent, True), (None, False), (None, True)):
        with pytest.raises(Conflict) as refused:
            _signer(None, sent_or_none, required=required)
        assert refused.value.code == E.REPORT_SIGNER_DETAILS_MISSING


# --------------------------------------------------------------------- changed since signing


def _signature(engineer: dict[str, Any]) -> ReportSignature:
    return cast(
        ReportSignature,
        SimpleNamespace(firm_profile_version=1, report_answers_version=1, engineer=engineer),
    )


def test_signer_is_a_change_by_value() -> None:
    signed = _signature({**DETAILS, "email": None})
    same = SignerDetails(**DETAILS)
    other = SignerDetails(**{**DETAILS, "license_number": "1"})

    def changed(now: SignerDetails | None, firm: int = 1) -> list[str] | None:
        return ReportRepository.changed_since_signing(
            signed, firm_profile_version=firm, report_answers_version=1, signer_now=now
        )

    assert changed(same) == [] and changed(None) == []
    assert changed(other) == ["signer"] and changed(other, firm=2) == ["firm", "signer"]
    unknown = cast(ReportSignature, SimpleNamespace(firm_profile_version=None, engineer={}))
    assert (
        ReportRepository.changed_since_signing(
            unknown, firm_profile_version=1, report_answers_version=1, signer_now=other
        )
        is None
    )


# --------------------------------------------------------------------- the setting


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.delenv("REPORT_SIGNER_DETAILS_REQUIRED", raising=False)
    monkeypatch.setenv("BASIS_ENV", "test")
    monkeypatch.chdir(tmp_path)
    reset_settings()
    yield monkeypatch
    reset_settings()


def test_saved_details_are_not_required_by_default(env: pytest.MonkeyPatch) -> None:
    assert load_settings().report_signer_details_required is False


def test_the_deployment_may_require_them(env: pytest.MonkeyPatch) -> None:
    env.setenv("REPORT_SIGNER_DETAILS_REQUIRED", "true")
    assert load_settings().report_signer_details_required is True


def test_a_bad_value_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("REPORT_SIGNER_DETAILS_REQUIRED", "sometimes")
    with pytest.raises(ValidationError):
        load_settings()
