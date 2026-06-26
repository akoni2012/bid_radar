"""Launch the monetized API (free cited.md teaser + x402-gated lead pack)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402


def main() -> None:
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "4021"))
    print(f"Serving Tender Opportunity Agent on http://{host}:{port}")
    print("  GET  /cited.md   (free)")
    print("  GET  /leadpack   (x402 paid)")
    print("  POST /refresh    (run pipeline)")
    uvicorn.run("server.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
