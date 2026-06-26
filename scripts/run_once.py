"""Run the full Tender Opportunity Agent pipeline once and write cited.md."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/run_once.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import run_pipeline  # noqa: E402


def main() -> None:
    result = run_pipeline()
    print("\n=== Run complete ===")
    print(f"Run id:       {result.run_id}")
    print(f"Discovered:   {result.discovered}")
    print(f"Qualified:    {result.qualified}")
    print(f"Ranked:       {len(result.scored)}")
    print(f"Change:       {result.change.summary()}")
    print(f"Published:    {result.cited_md_path}")
    print(f"Lead pack:    {result.leadpack_path}")
    if result.scored:
        top = result.scored[0]
        print(f"Top lead:     {top.tender.title} (fit {top.fit_score}/100)")


if __name__ == "__main__":
    main()
