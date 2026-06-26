"""Stage 7 - Publish.

Renders the ranked, grounded opportunities into the public ``cited.md`` artifact
(free tier: top N with citations) and assembles the full enriched lead-pack
payload that the x402-gated premium endpoint serves.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from config import CITED_MD_PATH, settings
from src.ground import Grounding
from src.models import ScoredTender
from src.store import ChangeSet


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _badge(change: ChangeSet, tid: str) -> str:
    if tid in change.new_ids:
        return "NEW"
    if tid in change.changed_ids:
        return "UPDATED"
    return ""


def render_cited_md(
    scored: List[ScoredTender],
    grounding: Grounding,
    change: ChangeSet,
) -> str:
    limit = settings.free_tier_limit
    shown = scored[:limit]
    total = len(scored)

    lines: List[str] = []
    lines.append("# Tender Opportunity Brief")
    lines.append("")
    lines.append(
        "> Autonomous brief of fresh **AI / data / cloud / analytics / "
        "digital-transformation** tenders discovered on the open web, ranked by "
        "commercial fit and grounded in cited sources."
    )
    lines.append("")
    lines.append(f"**Generated:** {_now_str()}  ")
    lines.append(f"**Run:** `{change.run_id}`  ")
    lines.append(
        f"**Pipeline:** Tavily -> Prometheux (Vadalog) -> Senso ({grounding.grounded_by}) "
        f"-> ClickHouse  "
    )
    lines.append(
        f"**This run:** {len(change.new_ids)} new, {len(change.changed_ids)} updated, "
        f"{len(change.expired_ids)} expired."
    )
    lines.append("")

    lines.append("## Executive summary")
    lines.append("")
    lines.append(grounding.executive_summary)
    lines.append("")

    lines.append(f"## Top {len(shown)} opportunities")
    lines.append("")
    lines.append("| # | Opportunity | Buyer | Country | Value | Deadline | Fit | Conf. |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(shown, start=1):
        t = s.tender
        badge = _badge(change, t.id)
        title = f"[{t.title}]({t.url})"
        if badge:
            title = f"**{badge}** {title}"
        lines.append(
            f"| {i} | {title} | {t.buyer} | {t.country} | "
            f"{t.value_band or '-'} | {t.deadline or '-'} | "
            f"{s.fit_score}/100 | {s.confidence}% |"
        )
    lines.append("")

    lines.append("## Opportunity detail")
    lines.append("")
    for i, s in enumerate(shown, start=1):
        t = s.tender
        lines.append(f"### {i}. {t.title}")
        lines.append("")
        lines.append(f"- **Buyer:** {t.buyer} ({t.country})")
        lines.append(f"- **Value band:** {t.value_band or 'unknown'}")
        lines.append(f"- **Deadline:** {t.deadline or 'unknown'} | **Published:** {t.published or 'unknown'}")
        lines.append(f"- **Sector signals:** {t.sector}")
        lines.append(f"- **Fit score:** {s.fit_score}/100 | **Confidence:** {s.confidence}%")
        lines.append(f"- **Why it ranks:** {s.rationale}")
        lines.append(f"- **Evidence:** {t.evidence_snippet}")
        lines.append(f"- **Source:** <{t.url}>")
        lines.append("")

    if total > len(shown):
        lines.append("## Premium lead pack")
        lines.append("")
        lines.append(
            f"This free brief shows the top {len(shown)} of **{total}** qualifying "
            "opportunities. The full ranked pack - every opportunity, complete "
            "evidence, scoring rationale, buyer detail and the change-history "
            "watchlist - is available via the agent payment-gated endpoint:"
        )
        lines.append("")
        lines.append(
            f"```\nGET /leadpack   (x402: {settings.x402_leadpack_price} USDC on "
            f"{settings.x402_network})\n```"
        )
        lines.append("")

    lines.append("## Citations")
    lines.append("")
    for i, s in enumerate(shown, start=1):
        lines.append(f"{i}. [{s.tender.title}]({s.tender.url})")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Methodology: opportunities are discovered via live Tavily web search, "
        "evidence is extracted and validated (must carry a value or deadline), "
        "commercial fit is scored by Prometheux Vadalog rules (sector, value band, "
        "deadline window, recency and keyword signals; max 100), grounded against a "
        "Senso knowledge base, and tracked for changes in ClickHouse across runs._"
    )
    lines.append("")

    return "\n".join(lines)


def write_cited_md(content: str) -> str:
    CITED_MD_PATH.write_text(content, encoding="utf-8")
    return str(CITED_MD_PATH)


def build_leadpack(
    scored: List[ScoredTender],
    grounding: Grounding,
    change: ChangeSet,
    first_seen: Dict[str, str],
) -> dict:
    """Full enriched payload served behind the x402 paywall."""
    items = []
    for rank, s in enumerate(scored, start=1):
        t = s.tender
        items.append(
            {
                "rank": rank,
                "id": t.id,
                "title": t.title,
                "buyer": t.buyer,
                "country": t.country,
                "sector": t.sector,
                "value_band": t.value_band,
                "deadline": t.deadline,
                "published": t.published,
                "url": t.url,
                "evidence": t.evidence_snippet,
                "fit_score": s.fit_score,
                "confidence": s.confidence,
                "rationale": s.rationale,
                "signal_breakdown": s.signal_breakdown,
                "status": _badge(change, t.id) or "STABLE",
                "first_seen": first_seen.get(t.id, ""),
                "senso_doc_id": grounding.kb_doc_ids.get(t.id, ""),
                "citations": grounding.citations.get(t.id, [t.url]),
            }
        )
    return {
        "generated": _now_str(),
        "run_id": change.run_id,
        "grounded_by": grounding.grounded_by,
        "executive_summary": grounding.executive_summary,
        "total_opportunities": len(scored),
        "watchlist": {
            "new": change.new_ids,
            "updated": change.changed_ids,
            "expired": change.expired_ids,
        },
        "opportunities": items,
    }
