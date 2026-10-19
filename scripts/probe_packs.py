"""Empirical context-pack budget probe.

Runs ~10 realistic keyword query sets against a mementic graph database and
renders packs at candidate TOTAL token budgets {400, 600, 800, 1200} (split
by the production formula :func:`mementic.context.split_pack_budget`, so the
probe cannot drift from CLI budget behavior). For every query and budget it
reports pack size (chars / approx tokens), items shown vs cut, and a
precision proxy:

- ``pct``: mean percentile of each shown item's IDF score within the
  post-gate matched-score distribution (100 = all shown items are top-ranked).
- ``below``: how many shown items score below the median matched score.

Usage:
    uv run python scripts/probe_packs.py [--db PATH]

The default DB path resolves from the repo root (``.mementic/graph.db``
under the Git toplevel, or the working directory outside repositories).
The database is opened
READ-ONLY (SQLite ``mode=ro``): the harness fails fast when the file does
not exist and structurally cannot create or write one, so it is safe to
point at a production database.
Output is ASCII-only for Windows cp1252 consoles.
"""
from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import TypedDict

from mementic.context import (
    build_context_pack,
    document_frequencies,
    drop_dead_path_entries,
    idf_weights,
    rank_keyword_section,
    split_pack_budget,
)
from mementic.text import estimate_tokens
from mementic.models import Constraint, Decision
from mementic.paths import graph_db_path, resolve_base_dir
from mementic.store import MementicStore

_DEFAULT_DB: str = str(graph_db_path(resolve_base_dir()))

# Realistic agent query sets: narrow, single-word technical
# keywords, as instructed by the enforcement template.
QUERY_SETS: dict[str, list[str]] = {
    "ruff-lint": ["ruff", "lint", "formatting", "quotes"],
    "pytest": ["pytest", "fixtures", "xdist", "parametrize"],
    "mypy-typing": ["mypy", "typing", "hints"],
    "pack-budget": ["context", "pack", "token", "budget", "truncation"],
    "graph-sqlite": ["sqlite", "graph", "tombstone", "liveness", "deprecation"],
    "cli-rich": ["cli", "typer", "rich", "console", "panel"],
    "metrics": ["metrics", "usage", "tokens", "honest"],
    "sessions": ["session", "extraction", "capture", "opencode", "claude"],
    "compact-ids": ["slugify", "id", "compact", "entities"],
    "uv-deps": ["uv", "dependency", "lock", "sync"],
}

# Candidate TOTAL token budgets to evaluate.
BUDGETS: list[int] = [400, 600, 800, 1200]


class ProbeRow(TypedDict):
    """One measurement cell of the query-by-budget grid."""

    query: str
    budget: int
    chars: int
    tokens: int
    shown: int
    cut: int
    mean_pct: float
    below_median: int
    matched: int


def _probe_query(
    name: str,
    keywords: list[str],
    constraints: list[Constraint],
    decisions: list[Decision],
    total_budget: int,
) -> ProbeRow:
    """Render one query at one budget and collect its measurements."""
    c_tokens, d_tokens = split_pack_budget(total_budget)

    # Recompute the post-gate matched set with scores for the precision proxy.
    live_c = drop_dead_path_entries(constraints, base_dir=resolve_base_dir())
    live_d = drop_dead_path_entries(decisions, base_dir=resolve_base_dir())
    n_docs = len(live_c) + len(live_d)
    doc_freq = document_frequencies([*live_c, *live_d], keywords)
    idf = idf_weights(n_docs, doc_freq)
    ranked_c = rank_keyword_section(live_c, keywords, idf)
    ranked_d = rank_keyword_section(live_d, keywords, idf)
    matched_scores = sorted(score for _, score in [*ranked_c, *ranked_d])

    result = build_context_pack(
        decisions, constraints,
        max_constraint_tokens=c_tokens,
        max_decision_tokens=d_tokens,
        keywords=keywords,
    )
    text = result.text
    stats = result.stats
    shown = stats.shown_constraints + stats.shown_decisions
    matched = stats.matched_constraints + stats.matched_decisions

    # Precision proxy inputs: scores of the rendered items within the
    # post-gate matched distribution.
    shown_ids_c = {c.id for c in result.constraints}
    shown_ids_d = {d.id for d in result.decisions}
    shown_scores = [
        score for item, score in ranked_c if item.id in shown_ids_c
    ] + [
        score for item, score in ranked_d if item.id in shown_ids_d
    ]
    if shown_scores and matched_scores:
        percentiles = [
            100.0 * sum(1 for s in matched_scores if s <= sc) / len(matched_scores)
            for sc in shown_scores
        ]
        mean_pct = statistics.mean(percentiles)
        med = statistics.median(matched_scores)
        below = sum(1 for sc in shown_scores if sc < med)
    else:
        mean_pct = 0.0
        below = 0

    return {
        "query": name,
        "budget": total_budget,
        "chars": len(text),
        "tokens": estimate_tokens(text),
        "shown": shown,
        "cut": matched - shown,
        "mean_pct": round(mean_pct, 1),
        "below_median": below,
        "matched": matched,
    }


def main() -> None:
    """Run the probe grid and print the measurement table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB,
        help="Path to the mementic graph database (default: repo-root graph.db)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        parser.error(
            f"database not found: {args.db} -- pass --db PATH pointing at an "
            "existing .mementic/graph.db; the probe opens it read-only and "
            "will never create one"
        )

    store = MementicStore(db_path=db_path, read_only=True)
    try:
        constraints = store.get_live_constraints()
        decisions = store.get_live_decisions()
    finally:
        store.close()

    print(f"DB: {args.db}")
    print(f"Live corpus: {len(constraints)} constraints, {len(decisions)} decisions")
    print(f"Budgets (total tokens): {BUDGETS}")
    print()

    rows: list[ProbeRow] = []
    for name, keywords in QUERY_SETS.items():
        for budget in BUDGETS:
            rows.append(_probe_query(
                name, keywords, constraints, decisions, budget,
            ))

    header = (
        f"{'query':<14} {'budget':>6} {'chars':>7} {'tok':>6} "
        f"{'shown':>5} {'cut':>5} {'pct':>6} {'below':>5}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['query']:<14} {row['budget']:>6} {row['chars']:>7} "
            f"{row['tokens']:>6} {row['shown']:>5} {row['cut']:>5} "
            f"{row['mean_pct']:>6} {row['below_median']:>5}"
        )

    print()
    print("Per-budget means across queries:")
    summary_header = (
        f"{'budget':>6} {'chars':>7} {'tok':>6} {'shown':>5} {'cut':>5} "
        f"{'pct':>6} {'below':>5}"
    )
    print(summary_header)
    print("-" * len(summary_header))
    for budget in BUDGETS:
        subset = [r for r in rows if r["budget"] == budget]
        print(
            f"{budget:>6} "
            f"{statistics.mean(r['chars'] for r in subset):>7.0f} "
            f"{statistics.mean(r['tokens'] for r in subset):>6.0f} "
            f"{statistics.mean(r['shown'] for r in subset):>5.1f} "
            f"{statistics.mean(r['cut'] for r in subset):>5.1f} "
            f"{statistics.mean(r['mean_pct'] for r in subset):>6.1f} "
            f"{statistics.mean(r['below_median'] for r in subset):>5.1f}"
        )


if __name__ == "__main__":
    main()
