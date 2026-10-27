"""The GSE D-1 truth set built from the real samples (private data only).

Runs only where ``BASIS_EVAL_DATA_DIR/appraisal/gse_d1/`` holds the twelve UAD 3.6 sample
scenarios (Fannie Mae / Freddie Mac, fabricated data, kept out of the repository); skipped
with the reason otherwise. Marked ``eval_data`` so CI deselects it. Builds the set in memory
(the truth file is not rewritten) and checks it validates and carries the two site-value
cases the research found: SF3 (site value developed without the cost approach) and MH1
(full cost approach).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import pytest

from evals.appraisal import gse_d1
from evals.appraisal.truth import TruthSet

ENV_VAR: Final = "BASIS_EVAL_DATA_DIR"


def _appraisal() -> Path | None:
    value = os.environ.get(ENV_VAR)
    return Path(value).expanduser() / "appraisal" if value else None


def _skip_reason() -> str | None:
    appraisal = _appraisal()
    if appraisal is None:
        return f"{ENV_VAR} is not set (private eval data; this test never runs in CI)"
    base = appraisal / gse_d1.SET_NAME
    if not base.is_dir() or not {"SF3", "MH1"} <= set(gse_d1.scenarios(base)):
        return f"{ENV_VAR} lacks appraisal/gse_d1/<scenario>/appraisal.{{xml,pdf}}"
    return None


SKIP_REASON: Final = _skip_reason()

pytestmark = [
    pytest.mark.eval_data,
    pytest.mark.skipif(SKIP_REASON is not None, reason=SKIP_REASON or ""),
]


@pytest.fixture(scope="module")
def truth() -> TruthSet:
    appraisal = _appraisal()
    assert appraisal is not None
    return gse_d1.build_set(appraisal)


def test_the_set_validates(truth: TruthSet) -> None:
    revalidated = TruthSet.model_validate(truth.model_dump(mode="json"))
    assert len(revalidated.documents) == 12
    assert all(d.form == "uad36_urar" and d.source == "gse_d1" for d in truth.documents)


def test_sf3_site_value_and_mh1_cost_approach(truth: TruthSet) -> None:
    docs = {d.document: d for d in truth.documents}
    site = docs["SF3"].field("cost_approach.site_value")
    assert site.status == "present" and site.verified and site.page is not None
    assert docs["SF3"].field("cost_approach.indicated_value").status == "not_developed"
    indicated = docs["MH1"].field("cost_approach.indicated_value")
    assert indicated.status == "present" and indicated.verified and indicated.page is not None
    assert docs["MH1"].field("cost_approach.site_value").status == "present"


def test_cost_seg_critical_keys_are_verified_where_present(truth: TruthSet) -> None:
    critical = (
        "cost_approach.site_value",
        "listing_and_contract.contract_price",
        "listing_and_contract.contract_date",
        "reconciliation.final_market_value",
        "reconciliation.effective_date_of_appraisal",
        "subject.property_address",
    )
    for doc in truth.documents:
        for key in critical:
            field = doc.field(key)
            if field.status == "present":
                assert field.verified and field.page is not None, (doc.document, key)
