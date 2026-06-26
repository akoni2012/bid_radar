"""Stage 2 - Extract.

Use Tavily Extract to pull clean, query-focused evidence (markdown) from each
candidate URL. The extracted text is the grounding material that downstream
normalization, scoring, and Senso citation all rely on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from config import settings
from src.models import Candidate

EXTRACT_QUERY = (
    "tender opportunity: buyer, estimated value, submission deadline, "
    "scope of AI data cloud analytics work"
)


@dataclass
class Evidence:
    """Extracted content for a single candidate."""

    candidate: Candidate
    content: str

    @property
    def url(self) -> str:
        return self.candidate.url


def _mock_evidence(candidates: List[Candidate]) -> List[Evidence]:
    # In mock mode the search snippet already carries the salient facts, so we
    # treat an enriched snippet as the extracted evidence body.
    out: List[Evidence] = []
    for c in candidates:
        body = (
            f"# {c.title}\n\n{c.snippet}\n\n"
            f"Source published: {c.published or 'unknown'}.\n"
            f"Procurement notice retrieved from {c.url}."
        )
        out.append(Evidence(candidate=c, content=body))
    return out


def _tavily_evidence(candidates: List[Candidate]) -> List[Evidence]:
    from tavily import TavilyClient

    client = TavilyClient(api_key=settings.tavily_api_key)
    by_url: Dict[str, Candidate] = {c.url: c for c in candidates}
    urls = list(by_url.keys())
    results: Dict[str, str] = {}

    # Tavily Extract accepts up to 20 URLs per call.
    for i in range(0, len(urls), 20):
        batch = urls[i : i + 20]
        try:
            resp = client.extract(
                urls=batch,
                extract_depth="advanced",
                format="markdown",
                query=EXTRACT_QUERY,
                chunks_per_source=3,
            )
        except Exception as exc:  # pragma: no cover - network failure path
            print(f"[extract] Tavily extract failed for batch ({exc}).")
            continue

        for r in resp.get("results", []):
            url = r.get("url", "")
            raw = r.get("raw_content") or r.get("content") or ""
            if url and raw:
                results[url] = raw

    evidence: List[Evidence] = []
    for url, candidate in by_url.items():
        content = results.get(url)
        if not content:
            # Extraction failed for this URL; fall back to the search snippet so
            # the candidate is not silently dropped.
            content = f"# {candidate.title}\n\n{candidate.snippet}"
        evidence.append(Evidence(candidate=candidate, content=content))
    return evidence


def extract(candidates: List[Candidate]) -> List[Evidence]:
    if not candidates:
        return []
    if settings.tavily_enabled:
        try:
            return _tavily_evidence(candidates)
        except Exception as exc:  # pragma: no cover
            print(f"[extract] falling back to mock evidence ({exc}).")
    return _mock_evidence(candidates)
