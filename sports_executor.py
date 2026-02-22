"""
Fast execution for manual sports bets.
Uses market orders (FAK) for minimal latency - no decision automation.
"""
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from sports_config import (
    FUNDER_ADDRESS,
    PRIVATE_KEY,
    SIGNATURE_TYPE,
    CLOB_API,
    BET_AMOUNT_USD,
    USE_MARKET_ORDER,
    PRICE_SLIPPAGE,
    MAX_ORDER_SIZE_USD,
    MIN_ORDER_SIZE_USD,
)

MIN_ORDER_SIZE = 5.0  # Polymarket minimum shares

_client = None


def get_clob_client() -> ClobClient:
    """Get or create CLOB client (cached for speed)."""
    global _client
    if _client is None:
        _client = ClobClient(
            CLOB_API,
            key=PRIVATE_KEY,
            chain_id=137,
            signature_type=SIGNATURE_TYPE,
            funder=FUNDER_ADDRESS,
        )
        creds = _client.derive_api_key()
        _client.set_api_creds(creds)
    return _client


def place_bet_market(token_id: str, amount_usd: float) -> dict:
    """
    Place market order (FAK) - fastest execution.
    amount_usd: USD to spend.
    Returns {"ok": True, "message": "..."} or {"ok": False, "error": "..."}
    """
    amount_usd = max(MIN_ORDER_SIZE_USD, min(amount_usd, MAX_ORDER_SIZE_USD))
    try:
        client = get_clob_client()
        order = MarketOrderArgs(
            token_id=token_id,
            amount=amount_usd,
            side=BUY,
            order_type=OrderType.FAK,
        )
        signed = client.create_market_order(order)
        client.post_order(signed, OrderType.FAK)
        return {"ok": True, "message": f"Order placed: ${amount_usd:.2f}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def place_bet_limit(token_id: str, amount_usd: float, price: float) -> dict:
    """
    Place limit order at price - fills when crossing book.
    price: 0-1 (e.g. 0.65 = 65¢)
    """
    price = float(price or 0)
    if price <= 0 or price >= 1:
        return {"ok": False, "error": f"Invalid price {price}; must be 0 < price < 1"}
    price = min(0.99, price + PRICE_SLIPPAGE)
    size = amount_usd / price
    size = max(MIN_ORDER_SIZE, round(size, 2))
    amount_usd = max(MIN_ORDER_SIZE_USD, min(amount_usd, MAX_ORDER_SIZE_USD))
    try:
        client = get_clob_client()
        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=size,
            side=BUY,
        )
        signed = client.create_order(order_args)
        client.post_order(signed, OrderType.GTC)
        return {"ok": True, "message": f"Limit order: ${amount_usd:.2f} @ {price*100:.0f}¢"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def execute_bet(token_id: str, amount_usd: float | None = None, price: float | None = None) -> dict:
    """
    Execute bet - market (fast) or limit.
    If price is provided and valid, use limit; else market.
    """
    try:
        amt = float(amount_usd) if amount_usd is not None else BET_AMOUNT_USD
    except (TypeError, ValueError):
        amt = BET_AMOUNT_USD
    try:
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None
    if price_f is not None and 0 < price_f < 1 and not USE_MARKET_ORDER:
        return place_bet_limit(token_id, amt, price_f)
    return place_bet_market(token_id, amt)
