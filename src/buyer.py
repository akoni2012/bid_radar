"""Reusable buyer-side x402 payment logic.

Shared by the CLI (`scripts/buy_leadpack.py`) and the web UI (`POST /api/buy`).
Performs the full 402 -> sign USDC (EIP-3009) -> retry -> receive loop against a
gated endpoint, using an EVM wallet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PurchaseResult:
    status: int
    wallet: str
    settled: bool
    data: Optional[dict] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.data is not None


def new_wallet() -> tuple[str, str]:
    """Create a fresh EVM wallet. Returns (address, private_key_hex)."""
    from eth_account import Account

    acct = Account.create()
    return acct.address, acct.key.hex()


async def purchase_leadpack(url: str, private_key: str, network: str) -> PurchaseResult:
    """Pay the x402 paywall at ``url`` and return the resulting payload."""
    from eth_account import Account
    from x402 import SchemeRegistration, x402ClientConfig
    from x402.http.clients import wrapHttpxWithPaymentFromConfig
    from x402.mechanisms.evm.exact.client import ExactEvmScheme

    account = Account.from_key(private_key)
    config = x402ClientConfig(
        schemes=[
            SchemeRegistration(
                network=network,
                client=ExactEvmScheme(signer=account),
            )
        ]
    )

    async with wrapHttpxWithPaymentFromConfig(config, timeout=60.0) as client:
        resp = await client.get(url)

    settle = resp.headers.get("x-payment-response") or resp.headers.get("payment-response")
    if resp.status_code == 200:
        return PurchaseResult(
            status=200,
            wallet=account.address,
            settled=bool(settle),
            data=resp.json(),
        )
    return PurchaseResult(
        status=resp.status_code,
        wallet=account.address,
        settled=bool(settle),
        error=resp.text[:500],
    )
