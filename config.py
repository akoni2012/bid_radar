"""Central configuration for the Tender Opportunity Agent.

Reads settings from environment variables (optionally from a local ``.env``).
Each external integration exposes an ``*_enabled`` flag; when credentials are
absent the corresponding pipeline stage falls back to a deterministic MOCK
implementation so the full flow still runs for a demo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
VADALOG_DIR = ROOT_DIR / "vadalog"
CITED_MD_PATH = ROOT_DIR / "cited.md"


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # Tavily
    tavily_api_key: str = field(default_factory=lambda: _get("TAVILY_API_KEY"))

    # Prometheux
    pmtx_token: str = field(default_factory=lambda: _get("PMTX_TOKEN"))
    jarvispy_url: str = field(default_factory=lambda: _get("JARVISPY_URL"))

    # Senso
    senso_api_key: str = field(default_factory=lambda: _get("SENSO_API_KEY"))
    senso_base_url: str = field(
        default_factory=lambda: _get("SENSO_BASE_URL", "https://api.senso.ai/v1")
    )

    # ClickHouse
    clickhouse_host: str = field(default_factory=lambda: _get("CLICKHOUSE_HOST", "localhost"))
    clickhouse_port: int = field(default_factory=lambda: _get_int("CLICKHOUSE_PORT", 8123))
    clickhouse_user: str = field(default_factory=lambda: _get("CLICKHOUSE_USER", "default"))
    clickhouse_password: str = field(default_factory=lambda: _get("CLICKHOUSE_PASSWORD"))
    clickhouse_database: str = field(
        default_factory=lambda: _get("CLICKHOUSE_DATABASE", "tenders")
    )

    # x402
    x402_receiving_address: str = field(
        default_factory=lambda: _get(
            "X402_RECEIVING_ADDRESS", "0x0000000000000000000000000000000000000000"
        )
    )
    x402_facilitator_url: str = field(
        default_factory=lambda: _get("X402_FACILITATOR_URL", "https://x402.org/facilitator")
    )
    x402_network: str = field(default_factory=lambda: _get("X402_NETWORK", "eip155:84532"))
    x402_leadpack_price: str = field(
        default_factory=lambda: _get("X402_LEADPACK_PRICE", "$0.10")
    )
    buyer_private_key: str = field(default_factory=lambda: _get("BUYER_PRIVATE_KEY"))

    # Pipeline tuning
    max_results_per_query: int = field(
        default_factory=lambda: _get_int("MAX_RESULTS_PER_QUERY", 5)
    )
    max_tenders_per_run: int = field(
        default_factory=lambda: _get_int("MAX_TENDERS_PER_RUN", 40)
    )
    free_tier_limit: int = field(default_factory=lambda: _get_int("FREE_TIER_LIMIT", 10))
    search_recency_days: int = field(default_factory=lambda: _get_int("SEARCH_RECENCY_DAYS", 30))

    @property
    def tavily_enabled(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def prometheux_enabled(self) -> bool:
        return bool(self.pmtx_token and self.jarvispy_url)

    @property
    def senso_enabled(self) -> bool:
        return bool(self.senso_api_key)

    @property
    def clickhouse_enabled(self) -> bool:
        # ClickHouse has no API key; we attempt a connection and fall back if it fails.
        return bool(self.clickhouse_host)

    def summary(self) -> str:
        def mark(flag: bool) -> str:
            return "LIVE" if flag else "MOCK"

        return (
            f"Tavily: {mark(self.tavily_enabled)} | "
            f"Prometheux: {mark(self.prometheux_enabled)} | "
            f"Senso: {mark(self.senso_enabled)} | "
            f"ClickHouse: {self.clickhouse_host}:{self.clickhouse_port} | "
            f"x402: {self.x402_network} @ {self.x402_facilitator_url}"
        )


# The search query set that defines what "fresh opportunity" means for the agent.
SEARCH_QUERIES = [
    "new RFP artificial intelligence services tender",
    "government cloud migration tender request for proposal",
    "data analytics procurement public sector tender",
    "machine learning consulting contract opportunity",
    "digital transformation framework agreement tender",
    "enterprise data platform RFP",
]

# Sector keyword -> weight used by the scoring rules (Prometheux/Vadalog).
SECTOR_KEYWORDS = {
    "artificial intelligence": "ai",
    " ai ": "ai",
    "machine learning": "ai",
    "generative": "ai",
    "data analytics": "analytics",
    "analytics": "analytics",
    "data platform": "data",
    "data warehouse": "data",
    "cloud": "cloud",
    "migration": "cloud",
    "digital transformation": "digital_transformation",
    "modernisation": "digital_transformation",
    "modernization": "digital_transformation",
}


settings = Settings()
