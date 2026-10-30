"""``python -m evals.conventions_replay``: the firm's conventions over a stored export, by tier.

What it measures (CLASSIFICATION_CONVENTIONS.md section 9.5, EVAL_REPORT section 46): how
many of a study's assets the firm's conventions would serve at each tier of the key
hierarchy, which assets on the firm's ruling labels would still lack a ruling, where the
rulings conflict, and what the rescored export looks like, without a paid run and without
the stack. For every asset of the export it builds the component view the pipeline built
(label, material, attachment, room type, the export's property class) and asks the
production router (:func:`basis.classify.router.route`) with the firm's rows indexed as
``process_assets`` indexes them; a convention that would serve replaces the asset's
classification (bucket, section, period, category, ``method`` ``cache``, the provenance
with ``matched_tier``); a conflict keeps the stored classification and adds the review
reason; everything else, and every quantity and dollar, is left alone, so
:mod:`evals.e2e_report` scores the new export against the engineer's rows.

Three policies are replayed so the design's guards are measured, not asserted:

* ``exact``: the exact key only (the pipeline before the hierarchy; its result must equal
  the stored export's ``cache`` assets, which is the replay's regression check);
* ``tiered``: production (section 9: the three tiers, a generic floor keeps its material,
  the catch-all label never generalizes, the label tier does not outrank an uncontested
  rule row, equal-weight disagreements fall to the rules table);
* ``plain``: the three tiers with no guards (the label tier outranks every rule row and the
  catch-all label generalizes).

The conventions come from a dump of ``classification_cache`` (``psql`` ``row_to_json``
rows: ``key``, ``result``, ``org_id``, ``study_id``, ``reviewer_id``, ``approved_at``,
``approvals``, ``rule_key``, ``agrees_with_rules``, ``source``) or, with
``--conventions-from-export``, are rebuilt from an approved study's export (its ``manual``
assets and its ``llm`` assets with citations, one approval each: what ``promote_conventions``
would have written). ``--as-of-study`` keeps the rows that study seeded and sets their
approvals to 1 (the state right after its approval, before later studies confirmed them).

Usage (from ``backEnd/``)::

    python -m evals.conventions_replay export/rev-1.json --conventions cache_dump.json \\
        --as-of-study <study A id> --out rev-1.tiered.json --report replay.md --json replay.json

The labels and the engineer's report default to ``$BASIS_EVAL_DATA_DIR/labels/`` as for
``evals.e2e_report``; ``--no-score`` skips the scoring (no labels needed).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from basis.classify.cache import (
    CATCH_ALL_LABELS,
    TIERS,
    CachedClassification,
    ConventionConflict,
    ConventionIndex,
    ConventionMatch,
    cache_key,
)
from basis.classify.router import (
    CACHE_CONFIDENCE,
    CONVENTION_CONFLICT,
    ClassifyContext,
    Route,
    convention_rationale,
    route,
)
from basis.classify.schemas import ComponentView
from basis.domain.rules import PROPERTY_CLASSES, RULES_VERSION, lookup, normalize_label
from basis.llm.provider import Budget
from evals.e2e_report import (
    REPORT_FILE,
    ROWS_FILE,
    E2EScore,
    default_labels_path,
    export_from_document,
    load_report,
    load_rows,
    score,
)
from evals.reclassify_export import EXPORT_FORMATS, ReclassifyError, property_class_of

Policy = Literal["exact", "tiered", "plain"]
POLICIES: Final[tuple[Policy, ...]] = ("exact", "tiered", "plain")


class ReplayError(ValueError):
    pass


# ------------------------------------------------------------------ conventions


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class _Row:
    """What :meth:`CachedClassification.from_entry` reads from a ``classification_cache``
    row, from a dump's JSON object."""

    key: str
    result: Any
    source: str
    org_id: uuid.UUID | None
    study_id: uuid.UUID | None
    reviewer_id: uuid.UUID | None
    approved_at: datetime | None
    approvals: int
    rule_key: str | None
    agrees_with_rules: bool | None


def load_conventions(
    rows: Sequence[Mapping[str, Any]],
    *,
    as_of_study: uuid.UUID | None = None,
    rules_version: str | None = RULES_VERSION,
) -> tuple[list[CachedClassification], frozenset[str]]:
    """The dump's rows as the router reads them (malformed rows skipped) and the firm's
    ruling labels: the labels with at least one engineer ruling (``result.method``
    ``manual``), whose assets the report counts as "still without a ruling". With
    ``as_of_study`` only the rows that study seeded, each with one approval. The rows are
    re-keyed to ``rules_version`` (the running table's, by default): in production a row
    made under an older table is never served, but the replay asks what the rulings would
    do for this export, so the report must say which versions it bridged (``None`` keeps
    the rows' own version)."""
    found: list[CachedClassification] = []
    ruling_labels: set[str] = set()
    for raw in rows:
        study_id = _uuid(raw.get("study_id"))
        if as_of_study is not None and study_id != as_of_study:
            continue
        key = str(raw.get("key", ""))
        parts = key.split("|")
        if len(parts) != 6:
            continue
        if rules_version is not None:
            key = "|".join((*parts[:5], rules_version))
        result = raw.get("result")
        if isinstance(result, dict) and result.get("method") == "manual":
            ruling_labels.add(normalize_label(parts[0]))
        entry = _Row(
            key=key,
            result=raw.get("result"),
            source=str(raw.get("source", "")),
            org_id=_uuid(raw.get("org_id")),
            study_id=study_id,
            reviewer_id=_uuid(raw.get("reviewer_id")),
            approved_at=_when(raw.get("approved_at")),
            approvals=1 if as_of_study is not None else int(raw.get("approvals") or 1),
            rule_key=raw.get("rule_key"),
            agrees_with_rules=raw.get("agrees_with_rules"),
        )
        cached = CachedClassification.from_entry(entry)  # type: ignore[arg-type]
        if cached is not None:
            found.append(cached)
    return found, frozenset(ruling_labels)


def conventions_from_export(
    document: Mapping[str, Any], property_class: str, org_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Dump-shaped rows rebuilt from an approved study's export: one per context key from
    its ``manual`` assets (the engineer's rulings, uncited) and its ``llm`` assets with at
    least one citation (engineer-approved model answers), the way ``promote_conventions``
    chooses when several assets share a key (manual first; a disagreement skips the key).
    Citations are kept as chunk ids and excerpts; the replay does not re-verify them."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for asset in document.get("assets", []):
        method = asset.get("method")
        citations = asset.get("citations") or []
        if method == "manual" or (method == "llm" and citations):
            key = cache_key(
                asset.get("label"),
                asset.get("material"),
                asset.get("attachment"),
                property_class,
                asset.get("room_type"),
            )
            groups.setdefault(key, []).append(asset)
    rows: list[dict[str, Any]] = []
    for key, assets in sorted(groups.items()):
        pool = [a for a in assets if a.get("method") == "manual"] or assets
        answers = {(a.get("bucket"), a.get("irs_section"), a.get("recovery_period")) for a in pool}
        if len(answers) > 1:
            continue
        chosen = min(pool, key=lambda a: str(a.get("id")))
        hit = lookup(
            chosen.get("label"),
            chosen.get("material"),
            chosen.get("attachment"),
            property_class,
            room_type=chosen.get("room_type"),
        )
        rows.append(
            {
                "org_id": str(org_id),
                "key": key,
                "result": {
                    "category": chosen.get("category"),
                    "section": chosen.get("irs_section"),
                    "bucket": chosen.get("bucket"),
                    "recovery_period": chosen.get("recovery_period"),
                    "asset_class": None,
                    "citations": [
                        {"chunk_id": c.get("chunk_id"), "excerpt": c.get("excerpt")}
                        for c in (chosen.get("citations") or [])
                        if isinstance(c, dict) and c.get("chunk_id") and c.get("excerpt")
                    ],
                    "rationale": "",
                    "method": chosen.get("method"),
                    "source_asset_id": chosen.get("id"),
                    "rule_bucket": None if hit is None else hit.bucket.value,
                },
                "source": f"approved_asset:{chosen.get('id')}",
                "study_id": str(document.get("study_id")),
                "reviewer_id": None,
                "approved_at": None,
                "approvals": 1,
                "rule_key": None if hit is None else hit.key,
                "agrees_with_rules": None
                if hit is None
                else hit.bucket.value == chosen.get("bucket"),
            }
        )
    return rows


# ------------------------------------------------------------------ the replay


@dataclass(slots=True)
class Decision:
    asset_id: str
    label: str
    room_type: str | None
    material: str | None
    stored_method: str
    stored_bucket: str
    served: Literal["exact", "label_room", "label", "conflict", "none"]
    bucket: str
    key: str | None = None  # the ruling's key when served
    conflict_tier: str | None = None
    blocked_by_rule: str | None = None  # a label-tier match an uncontested rule row outranked


@dataclass(slots=True)
class PolicyResult:
    policy: Policy
    export: dict[str, Any]
    decisions: list[Decision]
    served_by_tier: Counter[str] = field(default_factory=Counter)
    conflicts: Counter[str] = field(default_factory=Counter)  # "label@tier" -> assets
    blocked: Counter[str] = field(default_factory=Counter)  # label -> assets
    moved: list[Decision] = field(default_factory=list)  # bucket changed against the stored one
    newly_served: int = 0  # served now, not served in the stored export
    unruled: Counter[str] = field(default_factory=Counter)  # "label@room" on ruling labels
    score: E2EScore | None = None


def _view(asset: Mapping[str, Any], property_class: str) -> ComponentView:
    return ComponentView(
        component_id=str(asset.get("component_id") or asset.get("id") or uuid.uuid4()),
        label=str(asset.get("label") or ""),
        material=asset.get("material"),
        attachment=asset.get("attachment"),
        room_type=asset.get("room_type"),
        room_name=asset.get("room_name"),
        property_class=property_class,  # type: ignore[arg-type]
    )


def _route(view: ComponentView, ctx: ClassifyContext, policy: Policy) -> tuple[Route, str | None]:
    """The production route, or the plain hierarchy (no guards); ``(route, blocked)``
    where ``blocked`` names the rule row that outranked a label-tier match."""
    if policy == "plain":
        match = ctx.cache.match(
            cache_key(
                view.label, view.material, view.attachment, ctx.property_class, view.room_type
            ),
            org_id=ctx.org_id,
        )
        hit = lookup(
            view.label, view.material, view.attachment, ctx.property_class, room_type=view.room_type
        )
        if isinstance(match, ConventionMatch):
            return Route("cache", match), None
        conflict = match if isinstance(match, ConventionConflict) else None
        return (Route("rule", hit, conflict) if hit else Route("llm", None, conflict)), None
    decided = route(view, ctx)
    blocked = None
    if decided.method != "cache":
        match = ctx.cache.match(
            cache_key(
                view.label, view.material, view.attachment, ctx.property_class, view.room_type
            ),
            org_id=ctx.org_id,
        )
        if isinstance(match, ConventionMatch) and match.tier == "label" and decided.found:
            blocked = getattr(decided.found, "key", None)
    return decided, blocked


def _served_asset(asset: Mapping[str, Any], match: ConventionMatch) -> dict[str, Any]:
    cached = match.cached
    new = dict(asset)
    new.update(
        {
            "category": cached.category,
            "bucket": cached.bucket.value,
            "recovery_period": str(cached.period),
            "irs_section": cached.section,
            "method": "cache",
            "confidence": CACHE_CONFIDENCE,
            "needs_review": False,
            "review_reason": None,
            "convention": cached.provenance(match.tier),
            "replayed": {
                "stored_method": asset.get("method"),
                "stored_bucket": asset.get("bucket"),
                "rationale": convention_rationale(cached, match.tier),
            },
        }
    )
    return new


def _conflicted_asset(asset: Mapping[str, Any], conflict: ConventionConflict) -> dict[str, Any]:
    new = dict(asset)
    reason = f"{CONVENTION_CONFLICT}:{conflict.tier}"
    stored = asset.get("review_reason")
    new["needs_review"] = True
    new["review_reason"] = f"{stored}; {reason}" if stored else reason
    new["replayed"] = {
        "stored_method": asset.get("method"),
        "stored_bucket": asset.get("bucket"),
        "conflict": [r.key for r in conflict.rows],
    }
    return new


def replay(
    document: Mapping[str, Any],
    conventions: Sequence[CachedClassification],
    property_class: str,
    org_id: uuid.UUID,
    policy: Policy,
    ruling_labels: frozenset[str] = frozenset(),
) -> PolicyResult:
    """The export under ``policy`` (module doc), with the per-asset decisions;
    ``ruling_labels`` are the labels whose unserved assets count as still unruled."""
    catch_all = CATCH_ALL_LABELS if policy == "tiered" else frozenset()
    index = ConventionIndex(conventions, catch_all=catch_all)
    ctx = ClassifyContext(
        study_id=str(document.get("study_id", "replay")),
        property_class=property_class,  # type: ignore[arg-type]
        budget=Budget(remaining_usd=0.0),
        cache=index,
        org_id=org_id,
    )
    result = PolicyResult(policy, dict(document), [])
    assets: list[dict[str, Any]] = []
    for asset in document.get("assets", []):
        view = _view(asset, property_class)
        stored_method = str(asset.get("method") or "")
        stored_bucket = str(asset.get("bucket") or "")
        decision = Decision(
            asset_id=str(asset.get("id")),
            label=view.label,
            room_type=view.room_type,
            material=view.material,
            stored_method=stored_method,
            stored_bucket=stored_bucket,
            served="none",
            bucket=stored_bucket,
        )
        if policy == "exact":
            exact = index.get(
                cache_key(
                    view.label, view.material, view.attachment, property_class, view.room_type
                )
            )
            match = ConventionMatch(exact, "exact") if exact and exact.org_id == org_id else None
            decided = Route("cache", match) if match else Route("llm", None)
            blocked = None
        else:
            decided, blocked = _route(view, ctx, policy)
        if decided.method == "cache" and isinstance(decided.found, ConventionMatch):
            match = decided.found
            new = _served_asset(asset, match)
            decision.served = match.tier
            decision.bucket = new["bucket"]
            decision.key = match.cached.key
            result.served_by_tier[match.tier] += 1
            if stored_method != "cache":
                result.newly_served += 1
        elif decided.conflict is not None:
            new = _conflicted_asset(asset, decided.conflict)
            decision.served = "conflict"
            decision.conflict_tier = decided.conflict.tier
            result.conflicts[f"{view.label}@{decided.conflict.tier}"] += 1
        else:
            new = dict(asset)
            if blocked is not None:
                decision.blocked_by_rule = blocked
                result.blocked[view.label] += 1
        if decision.served not in ("exact", "label_room", "label"):
            if normalize_label(view.label) in ruling_labels:
                result.unruled[f"{view.label}@{view.room_type or '-'}"] += 1
        if decision.bucket != stored_bucket:
            result.moved.append(decision)
        result.decisions.append(decision)
        assets.append(new)
    result.export["assets"] = assets
    result.export["conventions_replay"] = {
        "policy": policy,
        "rules_version": RULES_VERSION,
        "property_class": property_class,
        "conventions": len(conventions),
        "assets": len(assets),
        "served": dict(result.served_by_tier),
        "newly_served": result.newly_served,
        "conflicts": sum(result.conflicts.values()),
        "blocked_by_rule": sum(result.blocked.values()),
        "buckets_moved": len(result.moved),
    }
    return result


def regression_against_stored(document: Mapping[str, Any], exact: PolicyResult) -> list[str]:
    """Where the exact-tier replay differs from what the stored export says the pipeline
    served (asset ids): empty when the replay reproduces the stored cache assets."""
    stored = {str(a.get("id")) for a in document.get("assets", []) if a.get("method") == "cache"}
    replayed = {d.asset_id for d in exact.decisions if d.served == "exact"}
    return sorted(stored ^ replayed)


# ------------------------------------------------------------------ report


def render(
    results: Mapping[Policy, PolicyResult],
    stored_score: E2EScore | None,
    regression: list[str],
    conventions: int,
    row_versions: Sequence[str] = (),
) -> str:
    versions = ", ".join(row_versions) or "unknown"
    lines = [
        "# Conventions replay: the key hierarchy over a stored export",
        "",
        f"Conventions: {conventions} rows (made under rules {versions}; replayed under "
        f"{RULES_VERSION}). Policies: {', '.join(results)}. "
        f"Exact-tier regression against the stored export: "
        f"{'reproduced' if not regression else f'{len(regression)} assets differ'}.",
        "",
        "## Served, by policy",
        "",
        "| policy | exact | label_room | label | conflicts | blocked by a rule row | "
        "newly served | buckets moved |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for policy, r in results.items():
        lines.append(
            f"| {policy} | {r.served_by_tier['exact']} | {r.served_by_tier['label_room']} | "
            f"{r.served_by_tier['label']} | {sum(r.conflicts.values())} | "
            f"{sum(r.blocked.values())} | {r.newly_served} | {len(r.moved)} |"
        )
    if stored_score is not None:
        lines += ["", "## Scored against the engineer's rows", ""]
        lines.append(
            "| export | bucket right (evidenced) | wrong rows | y5 / y7 / y15 / y39 share (%) | "
            "max bucket share error |"
        )
        lines.append("|---|---|---|---|---|")
        lines.append(_score_line("stored", stored_score))
        for policy, r in results.items():
            if r.score is not None:
                lines.append(_score_line(policy, r.score))
    for policy, r in results.items():
        lines += ["", f"## {policy}: what moved", ""]
        if not r.moved:
            lines.append("nothing")
        else:
            moved: Counter[tuple[str, str, str, str, str]] = Counter()
            for d in r.moved:
                moved[
                    (d.label, d.room_type or "-", d.stored_method, d.stored_bucket, d.bucket)
                ] += 1
            lines.append("| label | room | stored (method) | from | to | assets |")
            lines.append("|---|---|---|---|---|---|")
            for (label, room, method, before, after), n in sorted(moved.items()):
                lines.append(f"| {label} | {room} | {method} | {before} | {after} | {n} |")
        if r.conflicts:
            lines += [
                "",
                "conflicts (label@tier: assets): "
                + ", ".join(f"{k}: {v}" for k, v in sorted(r.conflicts.items())),
            ]
        if r.blocked:
            lines += [
                "",
                "label-tier matches an uncontested rule row outranked (label: assets): "
                + ", ".join(f"{k}: {v}" for k, v in sorted(r.blocked.items())),
            ]
        if r.unruled:
            lines += [
                "",
                "assets on the firm's ruling labels still without a ruling (label@room: assets): "
                + ", ".join(f"{k}: {v}" for k, v in sorted(r.unruled.items())),
            ]
    return "\n".join(lines) + "\n"


def _score_line(name: str, result: E2EScore) -> str:
    c, sh = result.summary["classification"], result.summary["shares"]
    shares = sh.get("pipeline_bucket_share_pct") or {}
    share_text = " / ".join(str(shares.get(b, "-")) for b in ("y5", "y7", "y15", "y39"))
    wrong = ", ".join(c.get("wrong_bucket_rows", [])) or "none"
    return (
        f"| {name} | {c['bucket_right_by_count']} of {c['evidenced']} | {wrong} | {share_text} | "
        f"{sh['max_bucket_share_error_pct']:.1f} |"
    )


def to_json(
    results: Mapping[Policy, PolicyResult], stored_score: E2EScore | None, regression: list[str]
) -> dict[str, Any]:
    out: dict[str, Any] = {"regression_differences": regression, "policies": {}}
    if stored_score is not None:
        out["stored_score"] = stored_score.summary
    for policy, r in results.items():
        out["policies"][policy] = {
            "meta": r.export["conventions_replay"],
            "served_by_tier": dict(r.served_by_tier),
            "conflicts": dict(r.conflicts),
            "blocked_by_rule": dict(r.blocked),
            "unruled": dict(r.unruled),
            "moved": [asdict(d) for d in r.moved],
            "decisions": [asdict(d) for d in r.decisions],
            "score": None if r.score is None else r.score.summary,
        }
    return out


# ------------------------------------------------------------------ CLI


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.conventions_replay", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("export", type=Path, help="rev-<n>.json (basis-export/2) of the study")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--conventions", type=Path, help="a JSON dump of classification_cache (row_to_json rows)"
    )
    source.add_argument(
        "--conventions-from-export",
        type=Path,
        help="rebuild the conventions from an approved study's export (manual and cited llm)",
    )
    parser.add_argument(
        "--as-of-study",
        type=uuid.UUID,
        default=None,
        help="keep the rows this study seeded, one approval each",
    )
    parser.add_argument("--org", type=uuid.UUID, default=None, help="the firm (default: the rows')")
    parser.add_argument("--property-class", default=None, choices=list(PROPERTY_CLASSES))
    parser.add_argument(
        "--policy",
        action="append",
        choices=list(POLICIES),
        default=None,
        help="replay this policy (repeatable; default all three)",
    )
    parser.add_argument("--out", type=Path, default=None, help="the tiered export (basis-export/2)")
    parser.add_argument("--report", type=Path, default=None, help="Markdown report")
    parser.add_argument("--json", type=Path, default=None, help="decisions and scores as JSON")
    parser.add_argument("--rows", type=Path, default=None, help=f"default labels/{ROWS_FILE}")
    parser.add_argument(
        "--labels-report", type=Path, default=None, help=f"default labels/{REPORT_FILE}"
    )
    parser.add_argument("--no-score", action="store_true", help="skip evals.e2e_report")
    parser.add_argument(
        "--keep-rules-version",
        action="store_true",
        help="do not re-key the rows to the running RULES_VERSION (rows made under another "
        "table then never serve, as in production)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        document = json.loads(args.export.read_text(encoding="utf-8"))
        if document.get("format") not in EXPORT_FORMATS:
            raise ReplayError(
                f"{args.export}: not a study export (format {document.get('format')!r})"
            )
        property_class = property_class_of(document, args.property_class)
        if args.conventions is not None:
            raw = json.loads(args.conventions.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ReplayError(f"{args.conventions}: expected a JSON list of rows")
        else:
            seed = json.loads(args.conventions_from_export.read_text(encoding="utf-8"))
            if seed.get("format") not in EXPORT_FORMATS:
                raise ReplayError(f"{args.conventions_from_export}: not a study export")
            raw = conventions_from_export(seed, property_class, args.org or uuid.uuid4())
        versions = sorted(
            {str(r.get("rules_version") or str(r.get("key", "")).rsplit("|", 1)[-1]) for r in raw}
        )
        conventions, ruling_labels = load_conventions(
            raw,
            as_of_study=args.as_of_study,
            rules_version=None if args.keep_rules_version else RULES_VERSION,
        )
        if not conventions:
            raise ReplayError("no conventions to replay (check --as-of-study)")
        orgs = {c.org_id for c in conventions if c.org_id is not None}
        if args.org is not None:
            org_id = args.org
        elif len(orgs) == 1:
            org_id = orgs.pop()
        else:
            raise ReplayError(f"the rows belong to {len(orgs)} firms; pass --org")
        policies: tuple[Policy, ...] = tuple(args.policy) if args.policy else POLICIES
        results = {
            p: replay(document, conventions, property_class, org_id, p, ruling_labels)
            for p in policies
        }
        exact = results.get("exact") or replay(
            document, conventions, property_class, org_id, "exact", ruling_labels
        )
        regression = regression_against_stored(document, exact)
        stored_score = None
        if not args.no_score:
            rows = load_rows(args.rows or default_labels_path(ROWS_FILE))
            report = load_report(args.labels_report or default_labels_path(REPORT_FILE))
            stored_score = score(export_from_document(document, "stored"), rows, report)
            for r in results.values():
                r.score = score(export_from_document(r.export, r.policy), rows, report)
    except (ReclassifyError, ReplayError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = render(results, stored_score, regression, len(conventions), versions)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    else:
        print(text)
    if args.out:
        chosen = results.get("tiered") or next(iter(results.values()))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(chosen.export, indent=1, ensure_ascii=False), encoding="utf-8"
        )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(to_json(results, stored_score, regression), indent=1, default=str) + "\n",
            encoding="utf-8",
        )
    for policy, r in results.items():
        print(
            f"{policy}: served {dict(r.served_by_tier)}, conflicts {sum(r.conflicts.values())}, "
            f"blocked {sum(r.blocked.values())}, moved {len(r.moved)}",
            file=sys.stderr,
        )
    if regression:
        print(
            f"exact-tier replay differs from the stored export on {len(regression)} assets",
            file=sys.stderr,
        )
    return 0


__all__ = [
    "POLICIES",
    "TIERS",
    "Decision",
    "PolicyResult",
    "conventions_from_export",
    "load_conventions",
    "regression_against_stored",
    "render",
    "replay",
    "to_json",
]


if __name__ == "__main__":
    raise SystemExit(main())
