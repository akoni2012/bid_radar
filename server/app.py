"""Stage 9 - Monetize (FastAPI + x402).

Serves two surfaces:

* ``GET /cited.md`` - FREE public teaser (the published brief, top opportunities).
* ``GET /leadpack`` - PAID full enriched pack, gated behind an x402 payment
  (USDC on Base Sepolia via the signup-free x402.org facilitator by default).

If the x402 package or a real receiving wallet is not configured, the premium
route still works but is served ungated with a warning, so the demo runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from config import CITED_MD_PATH, settings
from src.buyer import new_wallet, purchase_leadpack
from src.pipeline import LEADPACK_PATH, run_pipeline

app = FastAPI(
    title="Tender Opportunity Agent",
    description="Autonomous, cited tender opportunity briefs with x402-gated lead packs.",
    version="1.0.0",
)

ZERO_ADDR = "0x0000000000000000000000000000000000000000"
STATIC_DIR = Path(__file__).resolve().parent / "static"
FAUCET_URL = "https://portal.cdp.coinbase.com/products/faucet"

# Public JSON-RPC endpoints + native USDC asset per supported x402 network,
# used by the buyer console to display wallet funding status.
NETWORK_INFO = {
    "eip155:84532": {
        "label": "Base Sepolia",
        "rpc": "https://sepolia.base.org",
        "rpc_fallbacks": [
            "https://sepolia.base.org",
            "https://base-sepolia-rpc.publicnode.com",
            "https://base-sepolia.drpc.org",
        ],
        "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "explorer": "https://sepolia.basescan.org",
    },
    "eip155:8453": {
        "label": "Base Mainnet",
        "rpc": "https://mainnet.base.org",
        "rpc_fallbacks": [
            "https://mainnet.base.org",
            "https://base-rpc.publicnode.com",
            "https://base.drpc.org",
        ],
        "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "explorer": "https://basescan.org",
    },
}


def _network_info() -> dict[str, str]:
    return NETWORK_INFO.get(
        settings.x402_network,
        {
            "label": settings.x402_network,
            "rpc": "",
            "usdc": "",
            "explorer": "",
        },
    )


def _configure_x402() -> bool:
    """Attach x402 payment middleware to /leadpack. Returns True if gated."""
    if not settings.x402_receiving_address or settings.x402_receiving_address == ZERO_ADDR:
        print("[server] X402_RECEIVING_ADDRESS not set; /leadpack served UNGATED (demo mode).")
        return False
    try:
        from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
        from x402.http.middleware.fastapi import PaymentMiddlewareASGI
        from x402.http.types import RouteConfig
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
        from x402.server import x402ResourceServer

        facilitator = HTTPFacilitatorClient(
            FacilitatorConfig(url=settings.x402_facilitator_url)
        )
        resource_server = x402ResourceServer(facilitator)
        resource_server.register(settings.x402_network, ExactEvmServerScheme())

        routes = {
            "GET /leadpack": RouteConfig(
                accepts=[
                    PaymentOption(
                        scheme="exact",
                        pay_to=settings.x402_receiving_address,
                        price=settings.x402_leadpack_price,
                        network=settings.x402_network,
                    )
                ],
                mime_type="application/json",
                description="Full ranked tender lead pack with evidence and rationale.",
            )
        }
        app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=resource_server)
        print(
            f"[server] /leadpack gated via x402 "
            f"({settings.x402_leadpack_price} on {settings.x402_network})."
        )
        return True
    except Exception as exc:  # pragma: no cover - optional dependency path
        print(f"[server] x402 setup failed ({exc}); /leadpack served UNGATED (demo mode).")
        return False


GATED = _configure_x402()

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def ui() -> str:
    index = STATIC_DIR / "index.html"
    if index.exists():
        return index.read_text(encoding="utf-8")
    return "<h1>Tender Opportunity Agent</h1><p>UI not found. See /api/info.</p>"


@app.get("/buyer", response_class=HTMLResponse)
def buyer_ui() -> str:
    page = STATIC_DIR / "buyer.html"
    if page.exists():
        return page.read_text(encoding="utf-8")
    return "<h1>Buyer Console</h1><p>UI not found. See /api/info.</p>"


@app.get("/api/info")
def api_info() -> dict:
    return {
        "service": "Tender Opportunity Agent",
        "endpoints": {
            "GET /cited.md": "Free public opportunity brief (markdown).",
            "GET /leadpack": (
                f"Premium full lead pack (x402 {settings.x402_leadpack_price} "
                f"on {settings.x402_network})"
                if GATED
                else "Premium full lead pack (UNGATED demo mode)."
            ),
            "POST /api/buy": "Purchase the lead pack as a buyer agent.",
            "POST /refresh": "Trigger a fresh pipeline run.",
            "GET /healthz": "Liveness probe.",
        },
        "monetization_gated": GATED,
        "price": settings.x402_leadpack_price,
        "network": settings.x402_network,
        "network_label": _network_info()["label"],
        "facilitator": settings.x402_facilitator_url,
        "receiving_address": (
            settings.x402_receiving_address if GATED else None
        ),
        "buyer_wallet_configured": bool(settings.buyer_private_key),
        "config": settings.summary(),
        "cited_md_ready": CITED_MD_PATH.exists(),
        "leadpack_ready": Path(LEADPACK_PATH).exists(),
    }


@app.get("/api/payment-terms")
def payment_terms() -> dict:
    """What the buyer agent must pay (x402 terms) to unlock the lead pack."""
    info = _network_info()
    if not GATED:
        return {
            "gated": False,
            "message": "Demo mode: /leadpack is served without payment.",
            "price": settings.x402_leadpack_price,
            "network": settings.x402_network,
            "network_label": info["label"],
        }
    return {
        "gated": True,
        "scheme": "exact",
        "price": settings.x402_leadpack_price,
        "amount_atomic": "100000",
        "asset": "USDC",
        "asset_address": info["usdc"],
        "decimals": 6,
        "network": settings.x402_network,
        "network_label": info["label"],
        "pay_to": settings.x402_receiving_address,
        "facilitator": settings.x402_facilitator_url,
        "resource": "/leadpack",
    }


@app.post("/api/wallet/new")
def wallet_new() -> dict:
    """Generate a throwaway EVM test wallet for buy-side testing."""
    address, key = new_wallet()
    info = _network_info()
    return {
        "address": address,
        "private_key": key,
        "network": settings.x402_network,
        "network_label": info["label"],
        "faucet": FAUCET_URL,
        "explorer": info["explorer"],
        "note": (
            "Fund this address with test ETH (gas) + test USDC on "
            f"{info['label']} via the faucet, then use it to pay."
        ),
    }


@app.get("/api/wallet/balance")
async def wallet_balance(address: str) -> JSONResponse:
    """Best-effort ETH + USDC balance for a wallet on the configured network."""
    address = (address or "").strip()
    if not (address.startswith("0x") and len(address) == 42):
        return JSONResponse(
            status_code=400, content={"error": "Provide a valid 0x address."}
        )
    info = _network_info()
    rpcs = info.get("rpc_fallbacks") or ([info["rpc"]] if info.get("rpc") else [])
    if not rpcs:
        return JSONResponse(
            status_code=400,
            content={"error": f"No RPC configured for {settings.x402_network}."},
        )

    import httpx

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "tender-opportunity-agent/1.0",
        "Accept": "application/json",
    }

    async def _rpc(method: str, params: list) -> str:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        last_err: Exception | None = None
        async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
            for url in rpcs:
                try:
                    r = await client.post(url, json=payload)
                    r.raise_for_status()
                    body = r.json()
                    if "error" in body:
                        raise RuntimeError(body["error"])
                    return body["result"]
                except Exception as exc:  # try the next RPC endpoint
                    last_err = exc
                    continue
        raise last_err or RuntimeError("No RPC endpoint reachable.")

    try:
        eth_hex = await _rpc("eth_getBalance", [address, "latest"])
        eth = int(eth_hex, 16) / 1e18

        usdc = None
        if info["usdc"]:
            # balanceOf(address) selector 0x70a08231 + 32-byte padded address
            data = "0x70a08231" + address[2:].lower().rjust(64, "0")
            usdc_hex = await _rpc(
                "eth_call", [{"to": info["usdc"], "data": data}, "latest"]
            )
            usdc = int(usdc_hex, 16) / 1e6 if usdc_hex and usdc_hex != "0x" else 0.0
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": f"Balance lookup failed: {exc}", "rpc": rpcs[0]},
        )

    funded = eth > 0 and (usdc is None or usdc > 0)
    return JSONResponse(
        {
            "address": address,
            "eth": round(eth, 6),
            "usdc": round(usdc, 4) if usdc is not None else None,
            "funded": funded,
            "network": settings.x402_network,
            "network_label": info["label"],
            "explorer": f"{info['explorer']}/address/{address}" if info["explorer"] else "",
        }
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "gated": GATED}


@app.get("/cited.md", response_class=PlainTextResponse)
def cited_md() -> str:
    if not CITED_MD_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="cited.md not generated yet. Run the pipeline (POST /refresh).",
        )
    return CITED_MD_PATH.read_text(encoding="utf-8")


@app.get("/leadpack")
def leadpack() -> JSONResponse:
    """Full enriched lead pack. Payment is enforced by middleware when gated."""
    path = Path(LEADPACK_PATH)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Lead pack not generated yet. Run the pipeline (POST /refresh).",
        )
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(content=data)


@app.post("/api/buy")
async def api_buy(request: Request) -> JSONResponse:
    """One-click buyer flow used by the UI.

    - Ungated (demo) mode: returns the full pack directly (no payment needed).
    - Gated mode: pays the x402 paywall. The buyer wallet is taken from the
      request body ``{"private_key": "0x..."}`` when provided (buy-side
      testing from the UI), otherwise falls back to the server's configured
      ``BUYER_PRIVATE_KEY``.
    """
    path = Path(LEADPACK_PATH)
    if not path.exists():
        run_pipeline()

    if not GATED:
        data = json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse(
            {
                "mode": "ungated-demo",
                "paid": False,
                "price": settings.x402_leadpack_price,
                "network": settings.x402_network,
                "message": "Demo mode: lead pack served without payment.",
                "data": data,
            }
        )

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    private_key = (body.get("private_key") or "").strip() or settings.buyer_private_key

    if not private_key:
        return JSONResponse(
            status_code=400,
            content={
                "mode": "gated",
                "paid": False,
                "error": (
                    "Paywall is active but no buyer wallet was provided. Paste a "
                    "funded Base Sepolia private key in the buyer console, set "
                    "BUYER_PRIVATE_KEY, or use scripts/buy_leadpack.py."
                ),
            },
        )

    leadpack_url = str(request.base_url).rstrip("/") + "/leadpack"
    try:
        result = await purchase_leadpack(
            leadpack_url, private_key, settings.x402_network
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"mode": "gated", "paid": False, "error": f"Payment failed: {exc}"},
        )

    if not result.ok:
        return JSONResponse(
            status_code=502,
            content={
                "mode": "gated",
                "paid": False,
                "wallet": result.wallet,
                "error": result.error or f"Unexpected status {result.status}",
            },
        )

    return JSONResponse(
        {
            "mode": "gated",
            "paid": True,
            "settled": result.settled,
            "wallet": result.wallet,
            "price": settings.x402_leadpack_price,
            "network": settings.x402_network,
            "message": f"Paid {settings.x402_leadpack_price} USDC on {settings.x402_network}.",
            "data": result.data,
        }
    )


@app.post("/refresh")
def refresh() -> dict:
    result = run_pipeline()
    return {
        "run_id": result.run_id,
        "discovered": result.discovered,
        "qualified": result.qualified,
        "scored": len(result.scored),
        "change": {
            "new": result.change.new_ids,
            "updated": result.change.changed_ids,
            "expired": result.change.expired_ids,
        },
        "cited_md": result.cited_md_path,
    }
