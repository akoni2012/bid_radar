"""Stage 8 - Monitor.

Runs the pipeline on a fixed cadence, surfacing new / updated / expired tenders
each cycle (via ClickHouse snapshot diffing) and regenerating ``cited.md``.
Includes simple exponential backoff so transient failures don't kill the loop.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from src.pipeline import run_pipeline

DEFAULT_INTERVAL_MIN = int(os.getenv("MONITOR_INTERVAL_MINUTES", "360"))  # 6 hours
MAX_BACKOFF_SEC = 600


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[monitor {ts}] {msg}")


def run_forever(interval_minutes: int = DEFAULT_INTERVAL_MIN) -> None:
    interval = interval_minutes * 60
    backoff = 30
    _log(f"starting monitor loop; interval = {interval_minutes} min")

    while True:
        try:
            result = run_pipeline()
            c = result.change
            _log(
                f"cycle ok: {result.qualified} qualified | "
                f"NEW={len(c.new_ids)} UPDATED={len(c.changed_ids)} "
                f"EXPIRED={len(c.expired_ids)}"
            )
            if c.new_ids:
                _log(f"new opportunities: {', '.join(c.new_ids[:10])}")
            backoff = 30  # reset after a healthy cycle
            sleep_for = interval
        except Exception as exc:  # pragma: no cover - resilience path
            _log(f"cycle FAILED ({exc}); backing off {backoff}s")
            sleep_for = backoff
            backoff = min(backoff * 2, MAX_BACKOFF_SEC)

        time.sleep(sleep_for)


def run_once() -> None:
    """Single cycle - useful for cron-style scheduling instead of a long loop."""
    result = run_pipeline()
    _log(f"single cycle complete: {result.change.summary()}")


if __name__ == "__main__":
    run_forever()
