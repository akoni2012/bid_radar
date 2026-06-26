"""Shared data models passed between pipeline stages."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Optional


def stable_id(url: str) -> str:
    """Deterministic short id derived from a URL (used as the tender primary key)."""
    return hashlib.sha1(url.strip().lower().encode("utf-8")).hexdigest()[:16]


def content_hash(*parts: str) -> str:
    """Hash of the meaningful content fields, used for change detection."""
    joined = "||".join((p or "").strip() for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


@dataclass
class Candidate:
    """A raw search hit before extraction/normalization."""

    url: str
    title: str
    snippet: str
    query: str
    published: str = ""
    raw_score: float = 0.0


@dataclass
class Tender:
    """A normalized tender opportunity with its supporting evidence."""

    id: str
    title: str
    buyer: str
    country: str
    sector: str
    value_band: str
    deadline: str          # ISO date or "" if unknown
    published: str         # ISO date or "" if unknown
    url: str
    evidence_snippet: str
    content_hash: str
    source_query: str = ""

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class ScoredTender:
    """A tender enriched with the Prometheux fit score and explanation."""

    tender: Tender
    fit_score: int
    confidence: int
    rationale: str
    signal_breakdown: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.tender.id
