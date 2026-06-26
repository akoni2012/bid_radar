"""Stage 1 - Discover.

Use Tavily Search across the opportunity query set to collect candidate tender
URLs from the live web. Falls back to a deterministic mock corpus when no
Tavily key is configured so the pipeline still runs end-to-end.
"""

from __future__ import annotations

from typing import List

from config import SEARCH_QUERIES, settings
from src.models import Candidate

# Domains that overwhelmingly host actual procurement / tender notices. We bias
# discovery toward these but never hard-restrict, to keep coverage of the open web.
TENDER_DOMAINS = [
    "ted.europa.eu",
    "find-tender.service.gov.uk",
    "contractsfinder.service.gov.uk",
    "sam.gov",
    "ungm.org",
    "tenders.gov.au",
    "ojs.eu",
    "worldbank.org",
]


def _mock_candidates() -> List[Candidate]:
    """Deterministic offline corpus used when Tavily is not configured."""
    samples = [
        Candidate(
            url="https://www.find-tender.service.gov.uk/Notice/ai-doc-intelligence-2026",
            title="AI-Powered Document Intelligence Platform - Home Office",
            snippet=(
                "The Home Office invites tenders for an AI-powered document "
                "intelligence and classification platform. Estimated value "
                "GBP 4,500,000. Closing date 2026-08-15."
            ),
            query=SEARCH_QUERIES[0],
            published="2026-06-20",
            raw_score=0.94,
        ),
        Candidate(
            url="https://ted.europa.eu/notice/cloud-migration-2026-eu",
            title="Cloud Migration and Modernisation Services - European Commission",
            snippet=(
                "DG DIGIT seeks a framework partner for cloud migration and "
                "application modernisation. Estimated value EUR 12,000,000. "
                "Submission deadline 2026-07-30."
            ),
            query=SEARCH_QUERIES[1],
            published="2026-06-18",
            raw_score=0.91,
        ),
        Candidate(
            url="https://sam.gov/opp/data-analytics-modernization-2026",
            title="Enterprise Data Analytics Modernization - US Dept of Commerce",
            snippet=(
                "Request for Proposal for an enterprise data analytics and "
                "warehouse modernization program. Estimated ceiling USD 8,200,000. "
                "Offers due 2026-09-01."
            ),
            query=SEARCH_QUERIES[2],
            published="2026-06-22",
            raw_score=0.88,
        ),
        Candidate(
            url="https://www.ungm.org/Public/Notice/ml-forecasting-2026",
            title="Machine Learning Demand Forecasting - World Food Programme",
            snippet=(
                "WFP requests proposals for a machine learning demand forecasting "
                "solution. Budget USD 1,300,000. Deadline 2026-07-10."
            ),
            query=SEARCH_QUERIES[3],
            published="2026-06-15",
            raw_score=0.83,
        ),
        Candidate(
            url="https://www.tenders.gov.au/digital-transformation-2026",
            title="Whole-of-Government Digital Transformation Framework - DTA",
            snippet=(
                "The Digital Transformation Agency seeks panel providers for a "
                "digital transformation framework agreement. Value AUD 20,000,000. "
                "Closes 2026-10-05."
            ),
            query=SEARCH_QUERIES[4],
            published="2026-06-12",
            raw_score=0.86,
        ),
        Candidate(
            url="https://ted.europa.eu/notice/data-platform-2026-de",
            title="Enterprise Data Platform Build - German Federal Statistical Office",
            snippet=(
                "Tender for design and build of an enterprise data platform and "
                "lakehouse. Estimated value EUR 6,400,000. Deadline 2026-08-28."
            ),
            query=SEARCH_QUERIES[5],
            published="2026-06-19",
            raw_score=0.81,
        ),
    ]
    return samples


def _tavily_candidates() -> List[Candidate]:
    from tavily import TavilyClient

    client = TavilyClient(api_key=settings.tavily_api_key)
    candidates: List[Candidate] = []
    seen: set[str] = set()

    for query in SEARCH_QUERIES:
        try:
            resp = client.search(
                query=query,
                topic="news",
                search_depth="advanced",
                days=settings.search_recency_days,
                max_results=settings.max_results_per_query,
                include_domains=TENDER_DOMAINS,
            )
        except Exception:
            # Recency / domain filters can be too strict for some queries; retry broad.
            resp = client.search(
                query=query,
                search_depth="advanced",
                max_results=settings.max_results_per_query,
            )

        for item in resp.get("results", []):
            url = item.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(
                Candidate(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("content", ""),
                    query=query,
                    published=item.get("published_date", "") or "",
                    raw_score=float(item.get("score", 0.0) or 0.0),
                )
            )

    return candidates


def discover() -> List[Candidate]:
    """Return candidate tenders discovered on the web (or the mock corpus)."""
    if settings.tavily_enabled:
        try:
            candidates = _tavily_candidates()
            if candidates:
                return candidates[: settings.max_tenders_per_run]
        except Exception as exc:  # pragma: no cover - network failure path
            print(f"[discover] Tavily search failed ({exc}); using mock corpus.")
    else:
        print("[discover] TAVILY_API_KEY not set; using mock corpus.")

    return _mock_candidates()[: settings.max_tenders_per_run]
