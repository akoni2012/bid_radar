"""Stage 6 - Ground (Senso.ai).

Ingests the extracted tender evidence into a Senso knowledge base so the
published brief is grounded in verified, queryable sources, then asks Senso for
a grounded executive summary with citations. Falls back to a deterministic,
source-cited summary built from the evidence when Senso is not configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

from config import settings
from src.extract import Evidence
from src.models import ScoredTender


@dataclass
class Grounding:
    executive_summary: str
    kb_doc_ids: Dict[str, str] = field(default_factory=dict)  # tender id -> Senso doc id
    citations: Dict[str, List[str]] = field(default_factory=dict)  # tender id -> source urls
    grounded_by: str = "local"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.senso_api_key}",
        "X-API-Key": settings.senso_api_key,
        "Content-Type": "application/json",
    }


def _ingest_to_senso(evidence_list: List[Evidence]) -> Dict[str, str]:
    doc_ids: Dict[str, str] = {}
    url = f"{settings.senso_base_url.rstrip('/')}/org/kb/raw"
    for ev in evidence_list:
        payload = {
            "title": ev.candidate.title or ev.candidate.url,
            "content": f"Source: {ev.candidate.url}\n\n{ev.content}",
            "summary": ev.candidate.snippet[:280],
        }
        try:
            resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
            resp.raise_for_status()
            data = resp.json()
            doc_id = (
                data.get("kb_node_id")
                or data.get("id")
                or data.get("document_id")
                or ""
            )
            doc_ids[ev.candidate.url] = str(doc_id)
        except Exception as exc:  # pragma: no cover - network path
            print(f"[ground] Senso ingest failed for {ev.candidate.url} ({exc}).")
    return doc_ids


def _senso_summary(scored: List[ScoredTender]) -> Optional[str]:
    url = f"{settings.senso_base_url.rstrip('/')}/org/search"
    top = scored[: settings.free_tier_limit]
    question = (
        "Summarize the most commercially attractive AI, data, cloud and analytics "
        "tenders currently open, citing the buyer, value and deadline for each."
    )
    try:
        resp = requests.post(
            url,
            json={"query": question, "max_results": len(top) or 5},
            headers=_headers(),
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
        return (
            data.get("answer")
            or data.get("response")
            or data.get("summary")
        )
    except Exception as exc:  # pragma: no cover - network path
        print(f"[ground] Senso grounded query failed ({exc}).")
        return None


def _local_summary(scored: List[ScoredTender]) -> str:
    if not scored:
        return "No qualifying tenders were found in the latest scan."
    top = scored[: settings.free_tier_limit]
    sectors = sorted({s.tender.sector.split(",")[0] for s in top})
    lead = top[0]
    lines = [
        f"The latest scan surfaced {len(scored)} qualifying opportunities across "
        f"{', '.join(sectors)}.",
        f"The strongest fit is \"{lead.tender.title}\" ({lead.tender.buyer}, "
        f"{lead.tender.country}) at fit {lead.fit_score}/100"
        + (f", value {lead.tender.value_band}" if lead.tender.value_band else "")
        + (f", closing {lead.tender.deadline}" if lead.tender.deadline else "")
        + ".",
        "All figures below are grounded in the cited source notices.",
    ]
    return " ".join(lines)


def ground(scored: List[ScoredTender], evidence_list: List[Evidence]) -> Grounding:
    citations = {s.id: [s.tender.url] for s in scored}

    if settings.senso_enabled:
        doc_ids = _ingest_to_senso(evidence_list)
        # Map evidence-by-url back onto tender ids for the doc id index.
        url_to_id = {s.tender.url: s.id for s in scored}
        tender_doc_ids = {
            url_to_id[url]: did for url, did in doc_ids.items() if url in url_to_id
        }
        summary = _senso_summary(scored)
        if summary:
            return Grounding(
                executive_summary=summary,
                kb_doc_ids=tender_doc_ids,
                citations=citations,
                grounded_by="senso",
            )
        # Ingest worked but query did not; still return Senso doc ids.
        return Grounding(
            executive_summary=_local_summary(scored),
            kb_doc_ids=tender_doc_ids,
            citations=citations,
            grounded_by="senso-ingest",
        )

    print("[ground] SENSO_API_KEY not set; generating local grounded summary.")
    return Grounding(
        executive_summary=_local_summary(scored),
        citations=citations,
        grounded_by="local",
    )
