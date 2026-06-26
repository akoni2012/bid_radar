"""Stage 5 - Reason / Rank (Prometheux / Vadalog).

Derives the per-tender commercial-fit signals, then computes a fit score using
the Vadalog rules in ``vadalog/scoring.vada``. When Prometheux is configured the
rules + facts are evaluated on the engine; otherwise an identical local
implementation of the same weights is used so results are deterministic and the
demo runs offline.

The weights here MUST stay in sync with ``vadalog/scoring.vada``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from config import VADALOG_DIR, settings
from src.models import ScoredTender, Tender

# --- weight tables (mirror of scoring.vada) --------------------------------

SECTOR_WEIGHTS = {
    "ai": 30, "analytics": 25, "data": 22, "cloud": 20,
    "digital_transformation": 18, "other": 5,
}
VALUE_WEIGHTS = {"mega": 30, "large": 24, "mid": 16, "small": 8, "unknown": 4}

# Sentinels emitted to the Vadalog program when a field is unknown. They are
# chosen so the engine rules resolve to the SAME neutral weight as the local
# fallback below: an unknown deadline -> the ``D < 0`` rule (6); an unknown
# recency -> the ``A >= 91`` rule (2). Keep these in sync with scoring.vada.
UNKNOWN_DAYS = -1
UNKNOWN_AGE = 9999
NEUTRAL_DEADLINE_W = 6
NEUTRAL_RECENCY_W = 2


def _days_to_deadline(deadline: str) -> Optional[int]:
    if not deadline:
        return None
    try:
        return (datetime.fromisoformat(deadline).date() - date.today()).days
    except ValueError:
        return None


def _age_days(published: str) -> Optional[int]:
    if not published:
        return None
    try:
        return (date.today() - datetime.fromisoformat(published[:10]).date()).days
    except ValueError:
        return None


def _primary_sector(sector: str) -> str:
    first = (sector or "other").split(",")[0].strip()
    return first if first in SECTOR_WEIGHTS else "other"


def _value_band(value_band: str) -> str:
    for band in ("mega", "large", "mid", "small"):
        if f"({band})" in value_band:
            return band
    return "unknown"


def _signal_count(t: Tender) -> int:
    sectors = [s for s in (t.sector or "").split(",") if s and s != "other"]
    return len(set(sectors))


def _deadline_weight(days: Optional[int]) -> int:
    if days is None:
        return NEUTRAL_DEADLINE_W  # unknown -> neutral; mirrors the D<0 vada rule
    if 14 <= days <= 90:
        return 20
    if 91 <= days <= 180:
        return 14
    if 7 <= days <= 13:
        return 10
    if days >= 181:
        return 8
    if 0 <= days <= 6:
        return 4
    return NEUTRAL_DEADLINE_W  # days < 0


def _recency_weight(age: Optional[int]) -> int:
    if age is None:
        return NEUTRAL_RECENCY_W
    if 0 <= age <= 7:
        return 12
    if 8 <= age <= 30:
        return 8
    if 31 <= age <= 90:
        return 4
    return NEUTRAL_RECENCY_W


def _signal_weight(count: int) -> int:
    return min(count, 4) * 2


def _confidence(t: Tender) -> int:
    """Evidence-completeness confidence (0-100), independent of fit score."""
    score = 0
    if t.value_band:
        score += 25
    if t.deadline:
        score += 25
    if t.published:
        score += 15
    if t.buyer and t.buyer != "Unknown":
        score += 20
    if _primary_sector(t.sector) != "other":
        score += 15
    return min(score, 100)


def _signals(t: Tender) -> Dict[str, object]:
    sector = _primary_sector(t.sector)
    band = _value_band(t.value_band)
    days = _days_to_deadline(t.deadline)
    age = _age_days(t.published)
    count = _signal_count(t)
    return {
        "sector": sector,
        "value_band": band,
        "days_to_deadline": days,
        "age_days": age,
        "signal_count": count,
        "sector_w": SECTOR_WEIGHTS[sector],
        "value_w": VALUE_WEIGHTS[band],
        "deadline_w": _deadline_weight(days),
        "recency_w": _recency_weight(age),
        "signal_w": _signal_weight(count),
    }


def _local_score(t: Tender) -> Tuple[int, Dict[str, object]]:
    s = _signals(t)
    total = int(s["sector_w"] + s["value_w"] + s["deadline_w"] + s["recency_w"] + s["signal_w"])
    return total, s


def _rationale(t: Tender, s: Dict[str, object], score: int) -> str:
    parts = []
    parts.append(f"{s['sector']} focus")
    if s["value_band"] != "unknown":
        parts.append(f"{s['value_band']} value band")
    if s["days_to_deadline"] is not None:
        parts.append(f"{s['days_to_deadline']}d to deadline")
    if s["age_days"] is not None:
        parts.append(f"published {s['age_days']}d ago")
    return f"Fit {score}/100 - " + ", ".join(parts) + "."


# --- Prometheux engine path ------------------------------------------------

def _build_vadalog_program(tenders: List[Tender]) -> str:
    """Combine the scoring rules with this run's facts into one program."""
    rules = (VADALOG_DIR / "scoring.vada").read_text()
    facts: List[str] = []
    for t in tenders:
        s = _signals(t)
        days = s["days_to_deadline"] if s["days_to_deadline"] is not None else UNKNOWN_DAYS
        age = s["age_days"] if s["age_days"] is not None else UNKNOWN_AGE
        facts.append(f'tender_sector("{t.id}", "{s["sector"]}").')
        facts.append(f'tender_value_band("{t.id}", "{s["value_band"]}").')
        facts.append(f'tender_days("{t.id}", {days}).')
        facts.append(f'tender_recency("{t.id}", {age}).')
        facts.append(f'tender_signals("{t.id}", {s["signal_count"]}).')
    return rules + "\n\n% --- run facts ---\n" + "\n".join(facts)


def _prometheux_scores(tenders: List[Tender]) -> Optional[Dict[str, int]]:
    """Evaluate the Vadalog program on Prometheux; return {id: score} or None."""
    try:
        import prometheux_chain as px

        px.config.set("PMTX_TOKEN", settings.pmtx_token)
        px.config.set("JARVISPY_URL", settings.jarvispy_url)

        program = _build_vadalog_program(tenders)
        project_id = "tender_opportunity_agent"
        px.save_concept(project_id=project_id, code=program)
        result = px.run_concept(project_id=project_id, concept_name="opportunity")

        scores = _parse_px_result(result)
        if scores:
            print(f"[reason] Prometheux scored {len(scores)} opportunities.")
            return scores
    except Exception as exc:  # pragma: no cover - network/SDK path
        print(f"[reason] Prometheux scoring unavailable ({exc}); using local rules.")
    return None


def _parse_px_result(result) -> Dict[str, int]:
    """Best-effort extraction of (id, score) rows from a Prometheux response."""
    scores: Dict[str, int] = {}
    rows = None
    if isinstance(result, dict):
        rows = result.get("data") or result.get("results") or result.get("rows")
    elif isinstance(result, list):
        rows = result
    else:
        for attr in ("data", "results", "rows", "tuples"):
            rows = getattr(result, attr, None)
            if rows:
                break
    if not rows:
        return scores
    for row in rows:
        try:
            if isinstance(row, dict):
                vals = list(row.values())
            else:
                vals = list(row)
            tid = str(vals[0])
            scores[tid] = int(float(vals[1]))
        except (IndexError, ValueError, TypeError):
            continue
    return scores


# --- public API ------------------------------------------------------------

def rank(tenders: List[Tender]) -> List[ScoredTender]:
    """Score and rank tenders by commercial fit (highest first)."""
    if not tenders:
        return []

    engine_scores: Dict[str, int] = {}
    if settings.prometheux_enabled:
        engine_scores = _prometheux_scores(tenders) or {}
    else:
        print("[reason] Prometheux not configured; using local Vadalog-mirrored rules.")

    scored: List[ScoredTender] = []
    for t in tenders:
        local_total, signals = _local_score(t)
        score = engine_scores.get(t.id, local_total)
        scored.append(
            ScoredTender(
                tender=t,
                fit_score=score,
                confidence=_confidence(t),
                rationale=_rationale(t, signals, score),
                signal_breakdown={
                    k: signals[k]
                    for k in ("sector_w", "value_w", "deadline_w", "recency_w", "signal_w")
                },
            )
        )

    # Deterministic ranking: fit desc, then deadline asc, value desc, published desc.
    scored.sort(key=_sort_key)
    return scored


def _sort_key(s: ScoredTender):
    t = s.tender
    days = _days_to_deadline(t.deadline)
    deadline_key = days if days is not None else 10_000
    value_rank = {"mega": 4, "large": 3, "mid": 2, "small": 1, "unknown": 0}[_value_band(t.value_band)]
    pub = t.published or "0000-00-00"
    return (-s.fit_score, deadline_key, -value_rank, _neg_date(pub))


def _neg_date(pub: str) -> str:
    # Sort published descending by inverting the string ordering deterministically.
    return "".join(chr(255 - ord(c)) if c.isdigit() else c for c in pub)
