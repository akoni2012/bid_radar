"""Stage 4 - Store + change monitoring (ClickHouse).

Persists tenders, append-only per-run snapshots, and fit scores in ClickHouse,
and diffs each run against prior state to surface NEW / CHANGED / EXPIRED
opportunities. If ClickHouse is unreachable it transparently falls back to a
local JSON store so the pipeline still runs and still detects changes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from config import DATA_DIR, settings
from src.models import ScoredTender, Tender


@dataclass
class ChangeSet:
    run_id: str
    new_ids: List[str] = field(default_factory=list)
    changed_ids: List[str] = field(default_factory=list)
    unchanged_ids: List[str] = field(default_factory=list)
    expired_ids: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"run={self.run_id} new={len(self.new_ids)} "
            f"changed={len(self.changed_ids)} unchanged={len(self.unchanged_ids)} "
            f"expired={len(self.expired_ids)}"
        )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _run_id() -> str:
    return _now().strftime("%Y%m%dT%H%M%S")


# ---------------------------------------------------------------------------
# ClickHouse backend
# ---------------------------------------------------------------------------

class ClickHouseStore:
    def __init__(self) -> None:
        import clickhouse_connect

        # Connect without a database first so we can create it if needed.
        admin = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
        )
        admin.command(f"CREATE DATABASE IF NOT EXISTS {settings.clickhouse_database}")
        admin.close()

        self.client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
        )
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.client.command(
            """
            CREATE TABLE IF NOT EXISTS tenders (
                id String,
                title String,
                buyer String,
                country String,
                sector String,
                value_band String,
                deadline String,
                published String,
                url String,
                evidence_snippet String,
                content_hash String,
                source_query String,
                first_seen DateTime,
                updated_at DateTime
            ) ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY id
            """
        )
        self.client.command(
            """
            CREATE TABLE IF NOT EXISTS tender_snapshots (
                run_id String,
                id String,
                content_hash String,
                seen_at DateTime
            ) ENGINE = MergeTree
            ORDER BY (id, seen_at)
            """
        )
        self.client.command(
            """
            CREATE TABLE IF NOT EXISTS scores (
                run_id String,
                id String,
                fit_score Int32,
                confidence Int32,
                rationale String,
                signal_breakdown String,
                scored_at DateTime
            ) ENGINE = ReplacingMergeTree(scored_at)
            ORDER BY id
            """
        )

    def _existing_hashes(self) -> Dict[str, str]:
        rows = self.client.query(
            "SELECT id, argMax(content_hash, updated_at) FROM tenders GROUP BY id"
        ).result_rows
        return {r[0]: r[1] for r in rows}

    def record_run(self, tenders: List[Tender]) -> ChangeSet:
        run_id = _run_id()
        now = _now()
        existing = self._existing_hashes()
        change = ChangeSet(run_id=run_id)

        current_ids = {t.id for t in tenders}
        tender_rows = []
        snapshot_rows = []

        for t in tenders:
            prior = existing.get(t.id)
            if prior is None:
                change.new_ids.append(t.id)
                first_seen = now
            elif prior != t.content_hash:
                change.changed_ids.append(t.id)
                first_seen = now  # ReplacingMergeTree keeps the latest row
            else:
                change.unchanged_ids.append(t.id)
                first_seen = now

            tender_rows.append(
                [
                    t.id, t.title, t.buyer, t.country, t.sector, t.value_band,
                    t.deadline, t.published, t.url, t.evidence_snippet,
                    t.content_hash, t.source_query, first_seen, now,
                ]
            )
            snapshot_rows.append([run_id, t.id, t.content_hash, now])

        # Anything previously known but not seen this run is considered expired.
        change.expired_ids = [tid for tid in existing if tid not in current_ids]

        if tender_rows:
            self.client.insert(
                "tenders",
                tender_rows,
                column_names=[
                    "id", "title", "buyer", "country", "sector", "value_band",
                    "deadline", "published", "url", "evidence_snippet",
                    "content_hash", "source_query", "first_seen", "updated_at",
                ],
            )
            self.client.insert(
                "tender_snapshots",
                snapshot_rows,
                column_names=["run_id", "id", "content_hash", "seen_at"],
            )
        return change

    def save_scores(self, scored: List[ScoredTender], run_id: str) -> None:
        if not scored:
            return
        now = _now()
        rows = [
            [
                run_id, s.id, int(s.fit_score), int(s.confidence), s.rationale,
                json.dumps(s.signal_breakdown), now,
            ]
            for s in scored
        ]
        self.client.insert(
            "scores",
            rows,
            column_names=[
                "run_id", "id", "fit_score", "confidence", "rationale",
                "signal_breakdown", "scored_at",
            ],
        )

    def first_seen_map(self) -> Dict[str, str]:
        rows = self.client.query(
            "SELECT id, min(first_seen) FROM tenders GROUP BY id"
        ).result_rows
        return {r[0]: r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]) for r in rows}


# ---------------------------------------------------------------------------
# JSON fallback backend (used when ClickHouse is unreachable)
# ---------------------------------------------------------------------------

class FileStore:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.state_path: Path = DATA_DIR / "state.json"
        self.state = self._load()

    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except json.JSONDecodeError:
                pass
        return {"tenders": {}, "snapshots": [], "scores": {}}

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2, default=str))

    def record_run(self, tenders: List[Tender]) -> ChangeSet:
        run_id = _run_id()
        now = _now().isoformat()
        change = ChangeSet(run_id=run_id)
        known = self.state["tenders"]
        current_ids = {t.id for t in tenders}

        for t in tenders:
            prior = known.get(t.id)
            if prior is None:
                change.new_ids.append(t.id)
                first_seen = now
            elif prior.get("content_hash") != t.content_hash:
                change.changed_ids.append(t.id)
                first_seen = prior.get("first_seen", now)
            else:
                change.unchanged_ids.append(t.id)
                first_seen = prior.get("first_seen", now)

            row = t.to_row()
            row["first_seen"] = first_seen
            row["updated_at"] = now
            known[t.id] = row
            self.state["snapshots"].append(
                {"run_id": run_id, "id": t.id, "content_hash": t.content_hash, "seen_at": now}
            )

        change.expired_ids = [tid for tid in known if tid not in current_ids and tid not in {*change.new_ids, *change.changed_ids, *change.unchanged_ids}]
        self._save()
        return change

    def save_scores(self, scored: List[ScoredTender], run_id: str) -> None:
        for s in scored:
            self.state["scores"][s.id] = {
                "run_id": run_id,
                "fit_score": s.fit_score,
                "confidence": s.confidence,
                "rationale": s.rationale,
                "signal_breakdown": s.signal_breakdown,
            }
        self._save()

    def first_seen_map(self) -> Dict[str, str]:
        return {tid: row.get("first_seen", "") for tid, row in self.state["tenders"].items()}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_store():
    """Return a ClickHouse-backed store, or a JSON fallback if unavailable."""
    try:
        store = ClickHouseStore()
        print(f"[store] Using ClickHouse at {settings.clickhouse_host}:{settings.clickhouse_port}")
        return store
    except Exception as exc:
        print(f"[store] ClickHouse unavailable ({exc}); using local JSON store.")
        return FileStore()
