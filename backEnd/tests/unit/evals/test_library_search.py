"""``evals.library_search`` without a database (EVAL_REPORT 92): the synthetic library is
deterministic and synthetic, the oracle means what the search promises (every word in some
searched value, case ignored, archived hidden, the searcher's firm only), every labelled
query has the needles it names, and the candidates' SQL keeps LIKE wildcards literal."""

from __future__ import annotations

import uuid

import pytest

from evals import library_search as ls


@pytest.fixture(scope="module")
def library() -> ls.Library:
    return ls.build_library(400)


def test_the_library_is_deterministic(library: ls.Library) -> None:
    again = ls.build_library(400)
    assert [s.id for s in again.studies] == [s.id for s in library.studies]
    assert [s.property_name for s in again.studies] == [s.property_name for s in library.studies]


def test_the_searcher_holds_a_quarter_and_the_firms_are_eight(library: ls.Library) -> None:
    own = [s for s in library.studies if s.firm == library.searcher.firm]
    assert len(library.firms) == ls.FIRMS
    # 100 generated + the planted studies of firm 0.
    assert 100 <= len(own) <= 110
    assert {s.firm for s in library.studies} == set(range(ls.FIRMS))


def test_only_synthetic_domains(library: ls.Library) -> None:
    assert all(u.email is None or u.email.endswith(".example") for u in library.users.values())


def test_every_labelled_query_names_real_needles(library: ls.Library) -> None:
    for labelled in ls.LABELLED:
        for name in labelled.must + labelled.must_not:
            assert name in library.needles, (labelled.id, name)


@pytest.mark.parametrize("labelled", ls.LABELLED, ids=lambda lab: lab.id)
def test_the_oracle_agrees_with_every_label(library: ls.Library, labelled: ls.Labelled) -> None:
    got = ls.oracle(library, labelled.q, accents=True)
    assert all(library.needles[n] in got for n in labelled.must)
    assert not any(library.needles[n] in got for n in labelled.must_not)


def test_the_oracle_is_every_word_in_some_value(library: ls.Library) -> None:
    harbor = library.needles["harbor"]
    assert harbor in ls.oracle(library, "harbor oxnard", accents=False)  # name + city
    assert harbor in ls.oracle(library, "  OXNARD   quillon ", accents=False)  # any order, case
    assert harbor not in ls.oracle(library, "harbor fresno", accents=False)
    # A blank query is every visible study of the searcher's firm (archived hidden).
    visible = ls.oracle(library, "", accents=False)
    own = [s for s in library.studies if s.firm == 0 and s.status != "archived"]
    assert visible == {s.id for s in own}
    assert library.needles["archived"] in ls.oracle(
        library, "zephyrine", accents=False, include_archived=True
    )


def test_accents_fold_only_when_asked(library: ls.Library) -> None:
    cafe = library.needles["cafe"]
    assert cafe in ls.oracle(library, "cafe", accents=True)
    assert cafe not in ls.oracle(library, "cafe", accents=False)
    assert cafe in ls.oracle(library, "CAFÉ", accents=False)


def test_like_wildcards_are_escaped() -> None:
    assert ls.escape_like(r"100%_\x") == r"100\%\_\\x"


def test_tsquery_is_prefix_terms_of_the_alphanumeric_parts() -> None:
    assert ls.tsquery("Harbor 118-34") == "'harbor':* & '118':* & '34':*"
    assert ls.tsquery("% _") == ""


@pytest.mark.parametrize("candidate", ls.CANDIDATES, ids=lambda c: c.name)
def test_every_candidate_binds_the_user_first_and_hides_archived(
    candidate: ls.Candidate,
) -> None:
    user = uuid.uuid4()
    count, page, ids, params = ls.listing_sql(candidate, "harbor 100%", user)
    assert params[0] == user
    for sql in (count, page, ids):
        assert "m.user_id = $1" in sql and "s.status <> 'archived'" in sql
    assert "harbor 100%" not in " ".join(str(p) for p in params[1:])  # bound, never inlined
    blank = ls.listing_sql(candidate, "   ", user)
    assert blank[3] == [user] and "TRUE" in blank[0]


def test_the_word_candidates_bind_one_pattern_per_word() -> None:
    joined = next(c for c in ls.CANDIDATES if c.name == "joined_trgm")
    _, _, _, params = ls.listing_sql(joined, "unit_7  100%", uuid.uuid4())
    assert params[1:] == [r"%unit\_7%", r"%100\%%"]
