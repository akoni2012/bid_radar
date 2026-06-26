"""Buyer-side demo: pay the x402 paywall and fetch the premium lead pack.

Simulates an autonomous buyer agent that:
  1. requests the gated /leadpack endpoint,
  2. receives HTTP 402 with x402 payment terms,
  3. signs a USDC (EIP-3009) authorization with its wallet,
  4. retries with the payment signature, and
  5. receives the full lead pack once the facilitator settles payment.

Usage:
    # Generate a fresh testnet wallet to fund:
    python scripts/buy_leadpack.py --new-wallet

    # Pay and fetch (needs a funded Base Sepolia wallet with test USDC + ETH):
    BUYER_PRIVATE_KEY=0x... python scripts/buy_leadpack.py
    BUYER_PRIVATE_KEY=0x... python scripts/buy_leadpack.py --url http://localhost:4021/leadpack

Funding (Base Sepolia testnet):
    - Test ETH (gas) + test USDC: https://portal.cdp.coinbase.com/products/faucet
    - USDC asset on Base Sepolia: 0x036CbD53842c5426634e7929541eC2318f3dCF7e
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402

DEFAULT_URL = f"http://localhost:{os.getenv('SERVER_PORT', '4021')}/leadpack"


def make_wallet() -> None:
    from src.buyer import new_wallet

    address, key = new_wallet()
    print("New Base Sepolia test wallet created:")
    print(f"  Address:     {address}")
    print(f"  Private key: {key}")
    print()
    print("Next steps:")
    print("  1. Fund this address with test ETH + test USDC on Base Sepolia:")
    print("       https://portal.cdp.coinbase.com/products/faucet")
    print(f"  2. Set the server's receiving wallet, then run the buyer:")
    print(f"       BUYER_PRIVATE_KEY={key} python scripts/buy_leadpack.py")


async def buy(url: str) -> int:
    private_key = os.getenv("BUYER_PRIVATE_KEY", "").strip()
    if not private_key:
        print("BUYER_PRIVATE_KEY not set.")
        print("Generate a wallet with:  python scripts/buy_leadpack.py --new-wallet")
        return 2

    from src.buyer import purchase_leadpack

    print(f"[buyer] target: {url}")
    print(f"[buyer] network: {settings.x402_network}")

    result = await purchase_leadpack(url, private_key, settings.x402_network)
    print(f"[buyer] wallet: {result.wallet}")
    print(f"[buyer] final status: {result.status}")
    if result.settled:
        print("[buyer] settlement proof header present.")

    if not result.ok:
        print(f"[buyer] request did not succeed:\n{result.error}")
        return 1

    data = result.data
    print("\n=== PAID LEAD PACK RECEIVED ===")
    print(f"Run id:            {data.get('run_id')}")
    print(f"Total opportunities: {data.get('total_opportunities')}")
    print(f"Grounded by:       {data.get('grounded_by')}")
    print("Top opportunities:")
    for opp in data.get("opportunities", [])[:3]:
        print(
            f"  #{opp['rank']} [{opp['fit_score']}/100] {opp['title']} "
            f"-> {opp['url']}"
        )
    out = Path("data") / "purchased_leadpack.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"\nFull pack saved to {out}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Pay the x402 paywall and fetch the lead pack.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Lead pack endpoint URL.")
    parser.add_argument("--new-wallet", action="store_true", help="Generate a fresh test wallet and exit.")
    args = parser.parse_args()

    if args.new_wallet:
        make_wallet()
        return

    rc = asyncio.run(buy(args.url))
    sys.exit(rc)


if __name__ == "__main__":
    main()
