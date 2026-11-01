"""``rule_fingerprint``: what a firm convention was decided under (CLASSIFICATION_CONVENTIONS.md
section 10.3). It must name the row :func:`lookup` uses for the label, change when that row
(or the path to it) changes, and stay put when anything else in the table changes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

import pytest

from basis.db.enums import Bucket
from basis.domain.rules import (
    ALIASES,
    GENERIC_FLOOR_LABELS,
    MATERIAL_RULES,
    RULE_FINGERPRINT_SCHEME,
    RULES,
    STRUCTURAL_ATTACHMENTS,
    Rule,
    RuleCitation,
    Variant,
    When,
    lookup,
    resolve_fingerprint,
    row_digests,
    rule_digest,
    rule_fingerprint,
)

NONE = f"r{RULE_FINGERPRINT_SCHEME}-none"


def resolve(
    label: str,
    material: str | None = None,
    *,
    digests: dict[str, str] | None = None,
    aliases: dict[str, str] | None = None,
    materials: dict[str, str] | None = None,
) -> str:
    return resolve_fingerprint(
        label,
        material,
        row_digests=row_digests() if digests is None else digests,
        aliases=ALIASES if aliases is None else aliases,
        material_rules=MATERIAL_RULES if materials is None else materials,
        generic_floors=GENERIC_FLOOR_LABELS,
    )


def every_label() -> list[tuple[str, str | None]]:
    """Every way into the table: each row key, alias, their plural forms, each generic
    floor with each mapped material and with none, labels and materials no row has."""
    labels: list[tuple[str, str | None]] = []
    for name in sorted(set(RULES) | set(ALIASES)):
        labels += [(name, None), (f"{name}s", None), (name.upper().replace("_", " "), None)]
    for floor in sorted(GENERIC_FLOOR_LABELS):
        labels += [(floor, m) for m in sorted(MATERIAL_RULES)] + [(floor, None), (floor, "cork")]
    labels += [("other", None), ("mirrorz", None), ("", None), ("unknown_widget", "wood")]
    return labels


def test_the_fingerprint_names_the_row_lookup_uses_for_every_label() -> None:
    checked = 0
    for label, material in every_label():
        hit = lookup(label, material)
        fp = rule_fingerprint(label, material)
        assert fp == (NONE if hit is None else row_digests()[hit.key]), (label, material)
        checked += 1
    assert checked > 400


def test_a_row_is_fingerprinted_with_its_variants_and_nothing_else_of_the_table() -> None:
    assert len(set(row_digests().values())) == len(RULES)  # one digest per row, no clashes
    assert all(
        d.startswith(f"r{RULE_FINGERPRINT_SCHEME}-") and len(d) == 19
        for d in row_digests().values()
    )
    # The material decides a generic floor, not the label.
    assert rule_fingerprint("flooring", "Carpet") == row_digests()["carpet"]
    assert rule_fingerprint("floor", "hardwood") == row_digests()["hardwood_floor"]
    assert rule_fingerprint("flooring", None) == NONE == rule_fingerprint("flooring", "cork")
    # A non-floor label ignores the material.
    assert rule_fingerprint("fireplace", "wood") == rule_fingerprint("fireplace", "stone")


def test_adding_an_unrelated_row_or_alias_keeps_every_other_fingerprint() -> None:
    new = Rule(
        key="zz_new_row",
        category="New",
        section="1250",
        bucket=Bucket.Y39,
        asset_class=None,
        irs_note="n",
        citations=(RuleCitation("DOC", 1, "an excerpt of the page"),),
    )
    digests = {**row_digests(), new.key: rule_digest(new)}
    aliases = {**ALIASES, "zz_alias": new.key}
    for label, material in every_label():
        assert resolve(label, material, digests=digests, aliases=aliases) == rule_fingerprint(
            label, material
        )
    assert resolve("zz_alias", digests=digests, aliases=aliases) == rule_digest(new)


def test_changing_the_row_a_variant_or_the_path_to_it_changes_the_fingerprint() -> None:
    sink = RULES["sink"]
    before = rule_digest(sink)
    assert rule_digest(replace(sink, bucket=Bucket.Y5)) != before
    assert rule_digest(replace(sink, contested="practitioners differ")) != before
    extra = Variant(When(rooms=frozenset({"garage"})), replace(sink, bucket=Bucket.Y5))
    assert rule_digest(replace(sink, variants=(*sink.variants, extra))) != before
    varied = RULES[next(k for k in sorted(RULES) if RULES[k].variants)]
    first = varied.variants[0]
    moved = replace(first, when=replace(first.when, rooms=first.when.rooms | {"attic"}))
    assert rule_digest(replace(varied, variants=(moved, *varied.variants[1:]))) != (
        rule_digest(varied)
    )
    retreated = replace(first, rule=replace(first.rule, bucket=Bucket.Y15))
    assert rule_digest(replace(varied, variants=(retreated, *varied.variants[1:]))) != (
        rule_digest(varied)
    )
    # An alias retargeted, a material remapped: only the labels on that path move.
    alias, target = next(iter(sorted(ALIASES.items())))
    other = next(k for k in sorted(RULES) if k != target)
    assert resolve(alias, aliases={**ALIASES, alias: other}) == row_digests()[other]
    assert (
        resolve("flooring", "vinyl", materials={**MATERIAL_RULES, "vinyl": "carpet"})
        == (row_digests()["carpet"])
    )
    assert resolve("flooring", "tile", materials={**MATERIAL_RULES, "vinyl": "carpet"}) == (
        rule_fingerprint("flooring", "tile")
    )


def test_rewording_recitation_or_renaming_a_row_changes_no_fingerprint() -> None:
    """The note, the citations and the category explain the answer; they are not it (the
    cache's ``answer_of`` leaves the category and citations out the same way)."""
    for key in sorted(RULES):
        rule = RULES[key]
        reworded = replace(
            rule,
            category=rule.category + " (renamed)",
            irs_note=rule.irs_note + " reworded",
            citations=(*rule.citations, RuleCitation("OTHER", 9, "another passage")),
            variants=tuple(
                replace(v, rule=replace(v.rule, irs_note="x", citations=())) for v in rule.variants
            ),
        )
        assert rule_digest(reworded) == rule_digest(rule), key
    sink = RULES["sink"]
    assert rule_digest(replace(sink, asset_class="00.11")) != rule_digest(sink)
    assert rule_digest(replace(sink, building=not sink.building)) != rule_digest(sink)


def test_a_new_field_at_its_default_changes_no_fingerprint() -> None:
    @dataclass(frozen=True, slots=True)
    class RuleWithMore(Rule):
        new_field: str | None = None

    with_variants = next(k for k in sorted(RULES) if RULES[k].variants)
    for key in ("sink", "carpet", with_variants):
        rule = RULES[key]
        values = {f: getattr(rule, f) for f in rule.__dataclass_fields__}
        assert rule_digest(RuleWithMore(**values)) == rule_digest(rule)
        assert rule_digest(RuleWithMore(**values, new_field="set")) != rule_digest(rule)


def test_the_structural_set_counts_only_for_a_section_1245_row() -> None:
    carpet = RULES["carpet"]
    building = replace(carpet, section="1250", bucket=Bucket.Y27_5, variants=())
    personal = replace(carpet, section="1245", bucket=Bucket.Y5, variants=())
    wider = STRUCTURAL_ATTACHMENTS | {"load_bearing"}
    assert rule_digest(building, wider) == rule_digest(building)
    assert rule_digest(personal, wider) != rule_digest(personal)


def test_the_canonical_encoding_is_pinned() -> None:
    """A change to the encoding (field order, JSON separators, the hash) changes every
    fingerprint and silently retires every convention: this pins it on a synthetic row."""
    rule = Rule(
        key="pinned",
        category="Pinned",
        section="1245",
        bucket=Bucket.Y5,
        asset_class="57.0",
        irs_note="note",
        citations=(RuleCitation("DOC", 3, "excerpt text"),),
        variants=(
            Variant(
                When(rooms=frozenset({"office", "bathroom"})),
                Rule(
                    key="pinned",
                    category="Pinned (office)",
                    section="1250",
                    bucket=Bucket.Y27_5,
                    asset_class=None,
                    irs_note="v",
                    citations=(),
                    building=True,
                ),
            ),
        ),
    )
    text = (
        '{"rule":{"asset_class":"57.0","bucket":"y5","key":"pinned","section":"1245",'
        '"variants":[{"rule":{"asset_class":null,"bucket":"y27_5","building":true,'
        '"key":"pinned","section":"1250"},"when":{"rooms":["bathroom","office"]}}]},'
        '"structural":["structural"]}'
    )
    assert rule_digest(rule) == "r1-" + hashlib.sha256(text.encode()).hexdigest()[:16]
    assert json.loads(text)["rule"]["variants"][0]["when"] == {"rooms": ["bathroom", "office"]}


@pytest.mark.parametrize("label", ["", None, "   "])
def test_an_empty_label_has_no_row(label: str | None) -> None:
    assert rule_fingerprint(label) == NONE
