"""Stage 3 - Normalize (+ quality gate).

Parse extracted evidence into structured :class:`Tender` records and apply a
light validation gate so only credible opportunities flow downstream. A record
must have a title, a source URL, and at least one hard commercial signal
(a value or a deadline) to pass.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from config import SECTOR_KEYWORDS
from src.extract import Evidence
from src.models import Tender, content_hash, stable_id

# --- value parsing ---------------------------------------------------------

_CURRENCY = {
    "£": "GBP", "gbp": "GBP",
    "€": "EUR", "eur": "EUR",
    "$": "USD", "usd": "USD",
    "aud": "AUD", "a$": "AUD",
}

_VALUE_RE = re.compile(
    r"(?P<sym>£|€|\$|GBP|EUR|USD|AUD|A\$)\s?"
    r"(?P<amount>\d[\d,\.]{2,})",
    re.IGNORECASE,
)

# --- deadline parsing ------------------------------------------------------

_ISO_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_LONG_DATE_RE = re.compile(
    r"(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(20\d{2})",
    re.IGNORECASE,
)
_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}
_DEADLINE_CONTEXT = ("deadline", "closing", "closes", "close", "due", "submission", "submit", "offers due", "expires")
# Words that mark a date as NOT a deadline (publication / posting dates).
_NEGATIVE_CONTEXT = ("published", "posted", "issued", "released", "updated", "retrieved")
# How far back to look for a context keyword (kept tight so a neighbouring
# sentence's keyword does not bleed in).
_CONTEXT_WINDOW = 24

# Token-boundary AI detection (matches "AI", "A.I.", "AI-powered" but not
# substrings like "maintenance" or "available").
_AI_RE = re.compile(r"\b(?:a\.?i\.?|artificial intelligence|machine learning|generative)\b", re.IGNORECASE)

# --- country inference -----------------------------------------------------

_TLD_COUNTRY = {
    "gov.uk": "United Kingdom",
    "europa.eu": "European Union",
    "ojs.eu": "European Union",
    "sam.gov": "United States",
    "gov.au": "Australia",
    "ungm.org": "Global (UN)",
    "worldbank.org": "Global (World Bank)",
}


def _parse_value(text: str) -> Tuple[str, Optional[float]]:
    best_amount: Optional[float] = None
    best_currency = ""
    for m in _VALUE_RE.finditer(text):
        sym = m.group("sym").lower()
        currency = _CURRENCY.get(sym, "")
        try:
            amount = float(m.group("amount").replace(",", ""))
        except ValueError:
            continue
        if amount < 1000:  # ignore stray small numbers
            continue
        if best_amount is None or amount > best_amount:
            best_amount = amount
            best_currency = currency
    if best_amount is None:
        return "", None
    band = _value_band(best_amount)
    return f"{best_currency} {best_amount:,.0f} ({band})".strip(), best_amount


def _value_band(amount: float) -> str:
    if amount >= 10_000_000:
        return "mega"
    if amount >= 3_000_000:
        return "large"
    if amount >= 500_000:
        return "mid"
    return "small"


def _parse_deadline(text: str) -> str:
    lowered = text.lower()
    # Only dates anchored by a deadline keyword (and not by a publication
    # keyword) are treated as deadlines. This avoids mistaking the notice's
    # published date for its closing date.
    candidates: List[Tuple[int, date]] = []

    for m in _ISO_DATE_RE.finditer(text):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        score = _context_score(lowered, m.start())
        if score > 0:
            candidates.append((score, d))

    for m in _LONG_DATE_RE.finditer(text):
        month = _MONTHS.get(m.group(2).lower())
        if not month:
            continue
        try:
            d = date(int(m.group(3)), month, int(m.group(1)))
        except ValueError:
            continue
        score = _context_score(lowered, m.start())
        if score > 0:
            candidates.append((score, d))

    if not candidates:
        return ""

    # Highest context score wins; ties broken by earliest date.
    candidates.sort(key=lambda c: (-c[0], c[1]))
    return candidates[0][1].isoformat()


def _context_score(lowered: str, pos: int) -> int:
    window = lowered[max(0, pos - _CONTEXT_WINDOW) : pos]
    if any(neg in window for neg in _NEGATIVE_CONTEXT):
        return 0
    return sum(1 for kw in _DEADLINE_CONTEXT if kw in window)


def _infer_country(url: str, text: str) -> str:
    host = urlparse(url).netloc.lower()
    for tld, country in _TLD_COUNTRY.items():
        if tld in host:
            return country
    return "Unknown"


def _infer_sector(text: str) -> str:
    lowered = f" {text.lower()} "
    hits: List[str] = []
    # Token-boundary AI detection first so it ranks as the primary sector.
    if _AI_RE.search(text) and "ai" not in hits:
        hits.append("ai")
    for keyword, sector in SECTOR_KEYWORDS.items():
        if keyword in lowered and sector not in hits:
            hits.append(sector)
    if not hits:
        return "other"
    # Primary sector is the first matched; keep order stable for determinism.
    return ",".join(hits)


def _infer_buyer(title: str, text: str) -> str:
    # Evidence often formats as "<Opportunity> - <Buyer>".
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    m = re.search(r"(?:buyer|authority|contracting authority|client)\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    if m:
        return m.group(1).split("\n")[0].strip()[:120]
    return "Unknown"


def _clean_title(title: str, content: str) -> str:
    if title:
        return title.strip()
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    return first_line.lstrip("# ").strip() or "Untitled opportunity"


def normalize(evidence_list: List[Evidence]) -> List[Tender]:
    """Convert evidence into validated Tender records."""
    tenders: List[Tender] = []
    seen_ids: set[str] = set()

    for ev in evidence_list:
        c = ev.candidate
        content = ev.content or ""
        combined = f"{c.title}\n{content}"

        title = _clean_title(c.title, content)
        value_band, _amount = _parse_value(combined)
        deadline = _parse_deadline(combined)
        sector = _infer_sector(combined)
        country = _infer_country(c.url, combined)
        buyer = _infer_buyer(title, content)
        snippet = (c.snippet or content).strip().replace("\n", " ")[:400]

        tid = stable_id(c.url)
        if tid in seen_ids:
            continue

        tender = Tender(
            id=tid,
            title=title,
            buyer=buyer,
            country=country,
            sector=sector,
            value_band=value_band,
            deadline=deadline,
            published=c.published or "",
            url=c.url,
            evidence_snippet=snippet,
            content_hash=content_hash(title, value_band, deadline, sector),
            source_query=c.query,
        )

        if not _passes_quality_gate(tender):
            continue

        seen_ids.add(tid)
        tenders.append(tender)

    return tenders


def _passes_quality_gate(t: Tender) -> bool:
    """Require a title, a URL, and at least one hard commercial signal."""
    if not t.url or not t.title:
        return False
    has_signal = bool(t.value_band) or bool(t.deadline)
    if not has_signal:
        return False
    # Drop opportunities whose deadline has already passed.
    if t.deadline:
        try:
            if datetime.fromisoformat(t.deadline).date() < date.today():
                return False
        except ValueError:
            pass
    return True
