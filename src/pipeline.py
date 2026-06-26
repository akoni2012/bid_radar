"""End-to-end orchestration of the Tender Opportunity Agent.

Runs the full chain once: discover -> extract -> normalize -> store ->
reason -> ground -> publish, and persists both ``cited.md`` (free artifact) and
``data/leadpack.json`` (the premium payload the x402 endpoint serves).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List

from config import DATA_DIR, settings
from src import discover, extract, ground, normalize, publish, reason
from src.models import ScoredTender
from src.store import ChangeSet, get_store

LEADPACK_PATH = DATA_DIR / "leadpack.json"


@dataclass
class PipelineResult:
    run_id: str
    discovered: int
    qualified: int
    scored: List[ScoredTender]
    change: ChangeSet
    cited_md_path: str
    leadpack_path: str


def run_pipeline() -> PipelineResult:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[pipeline] config -> {settings.summary()}")

    candidates = discover.discover()
    print(f"[pipeline] discovered {len(candidates)} candidates")

    evidence = extract.extract(candidates)
    print(f"[pipeline] extracted evidence for {len(evidence)} candidates")

    tenders = normalize.normalize(evidence)
    print(f"[pipeline] {len(tenders)} tenders passed the quality gate")

    store = get_store()
    change = store.record_run(tenders)
    print(f"[pipeline] change detection -> {change.summary()}")

    scored = reason.rank(tenders)
    store.save_scores(scored, change.run_id)
    print(f"[pipeline] scored & ranked {len(scored)} opportunities")

    grounding = ground.ground(scored, evidence)
    print(f"[pipeline] grounded via '{grounding.grounded_by}'")

    cited_md = publish.render_cited_md(scored, grounding, change)
    cited_path = publish.write_cited_md(cited_md)

    leadpack = publish.build_leadpack(scored, grounding, change, store.first_seen_map())
    LEADPACK_PATH.write_text(json.dumps(leadpack, indent=2, default=str), encoding="utf-8")

    print(f"[pipeline] published -> {cited_path}")
    print(f"[pipeline] lead pack -> {LEADPACK_PATH}")

    return PipelineResult(
        run_id=change.run_id,
        discovered=len(candidates),
        qualified=len(tenders),
        scored=scored,
        change=change,
        cited_md_path=cited_path,
        leadpack_path=str(LEADPACK_PATH),
    )


if __name__ == "__main__":
    run_pipeline()
