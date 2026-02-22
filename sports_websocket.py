"""
Polymarket CLOB WebSocket - Live order book and price streaming.
Subscribes to asset_ids and maintains real-time best bid/ask.
Max 500 assets per connection.
"""
import json
import threading
import time

# Thread-safe live price store: asset_id -> {bid, ask, mid, updated}
_live_prices = {}
_lock = threading.Lock()
_ws = None
_ws_thread = None
_subscribed_ids = set()
_max_assets = 500
_ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def get_live_price(asset_id: str) -> dict | None:
    """Get live price for asset. Returns {bid, ask, mid} or None."""
    with _lock:
        return _live_prices.get(asset_id)


def get_all_live_prices() -> dict:
    """Get copy of all live prices."""
    with _lock:
        return dict(_live_prices)


def _update_price(asset_id: str, bid: float | None, ask: float | None):
    """Update price for asset. Use best_ask as display price for buy."""
    if not asset_id:
        return
    with _lock:
        if bid is None and ask is None:
            return
        prev = _live_prices.get(asset_id, {})
        bid = float(bid) if bid is not None else prev.get("bid")
        ask = float(ask) if ask is not None else prev.get("ask")
        if bid is None:
            bid = ask
        if ask is None:
            ask = bid
        mid = (bid + ask) / 2 if (bid and ask) else (bid or ask)
        _live_prices[asset_id] = {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "updated": time.time(),
        }


def _on_message(ws, message):
    try:
        raw = json.loads(message)
        # Handle batch (array of messages)
        items = raw if isinstance(raw, list) else [raw]
        for data in items:
            _process_message(data)
    except Exception:
        pass


def _process_message(data: dict):
    try:
        event_type = data.get("event_type")

        if event_type == "book":
            asset_id = data.get("asset_id")
            bids = data.get("bids", []) or data.get("buys", [])
            asks = data.get("asks", []) or data.get("sells", [])
            bid = float(bids[0]["price"]) if bids else None
            ask = float(asks[0]["price"]) if asks else None
            _update_price(asset_id, bid, ask)

        elif event_type == "price_change":
            for ch in data.get("price_changes", []):
                asset_id = ch.get("asset_id")
                bid = ch.get("best_bid")
                ask = ch.get("best_ask")
                if bid is not None:
                    bid = float(bid) if bid else None
                if ask is not None:
                    ask = float(ask) if ask else None
                _update_price(asset_id, bid, ask)

        elif event_type == "last_trade_price":
            asset_id = data.get("asset_id")
            price = data.get("price")
            if price is not None:
                p = float(price)
                _update_price(asset_id, p, p)
    except Exception:
        pass


def _on_error(ws, error):
    pass  # Reconnect will handle


def _on_close(ws, close_status_code, close_msg):
    pass


def _ping_loop(ws_ref):
    """Send PING every 10 seconds to keep connection alive."""
    while True:
        time.sleep(10)
        ws = ws_ref.get("ws")
        if ws and hasattr(ws, "send"):
            try:
                ws.send("PING")
            except Exception:
                break


def _run_ws(asset_ids: list):
    global _ws
    try:
        from websocket import WebSocketApp
    except ImportError:
        return
    ids = [str(a) for a in asset_ids[:_max_assets] if a]
    if not ids:
        return
    sub = {"assets_ids": ids, "type": "market"}
    backoff = 5
    max_backoff = 60
    while True:
        try:
            def on_open(ws):
                ws.send(json.dumps(sub))

            _ws = WebSocketApp(
                _ws_url,
                on_open=on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            _ws.run_forever()
        except Exception:
            pass
        time.sleep(min(backoff, max_backoff))
        backoff = min(backoff * 1.5, max_backoff)


def start_live_prices(asset_ids: list):
    """Start WebSocket in background thread. Call with list of token IDs."""
    global _ws_thread, _subscribed_ids
    ids = list(set(str(a) for a in asset_ids if a))[:_max_assets]
    if not ids:
        return
    _subscribed_ids = set(ids)
    if _ws_thread and _ws_thread.is_alive():
        # Could add resubscribe logic; for now just restart if ids changed
        return
    _ws_thread = threading.Thread(target=_run_ws, args=(ids,), daemon=True)
    _ws_thread.start()


def merge_live_into_markets(markets: list) -> list:
    """Merge live WebSocket prices into market buttons. In-place update."""
    for m in markets:
        for btn in m.get("buttons", []):
            tid = btn.get("token_id")
            if tid:
                lp = get_live_price(tid)
                if lp:
                    # Use best ask for buy (what you'd pay)
                    btn["price"] = lp.get("ask") or lp.get("mid") or btn["price"]
                    btn["live"] = True
    return markets
