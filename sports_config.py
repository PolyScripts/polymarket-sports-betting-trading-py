"""
Configuration for Sports Manual Trading Bot.
Manual click-to-bet: no automated decisions, only fast execution.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    load_dotenv(env_path)
except ImportError:
    pass

def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def _get_float(key: str, default: float) -> float:
    v = _get(key)
    return float(v) if v else default

def _get_int(key: str, default: int) -> int:
    v = _get(key)
    return int(v) if v else default

def _get_bool(key: str, default: bool) -> bool:
    v = _get(key).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")

# Required
FUNDER_ADDRESS = _get("FUNDER_ADDRESS")
PRIVATE_KEY = _get("PRIVATE_KEY")
SIGNATURE_TYPE = _get_int("SIGNATURE_TYPE", 0)

# Bet amount per click (USD)
BET_AMOUNT_USD = _get_float("SPORTS_BET_AMOUNT", 10.0)

# Order type: market (fastest) or limit
USE_MARKET_ORDER = _get_bool("SPORTS_USE_MARKET_ORDER", True)
PRICE_SLIPPAGE = _get_float("PRICE_SLIPPAGE", 0.01)

# Limits
MAX_ORDER_SIZE_USD = _get_float("MAX_ORDER_SIZE_USD", 100.0)
MIN_ORDER_SIZE_USD = _get_float("MIN_ORDER_SIZE_USD", 1.0)

# Server
SPORTS_SERVER_PORT = _get_int("SPORTS_SERVER_PORT", 5050)
SPORTS_POLL_INTERVAL_SEC = _get_float("SPORTS_POLL_INTERVAL", 0.5)

# Polymarket
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
POLYGON_RPC_URL = _get(
    "POLYGON_RPC_URL",
    "https://polygon-mainnet.infura.io/v3/667cef823e774b1a9d5da31cf340db57"
)

# Esports sport codes to exclude (from Polymarket /sports metadata)
ESPORTS_SPORT_CODES = frozenset({
    "dota2", "lol", "val", "cs2", "mlbb", "ow", "codmw", "fifa",
    "pubg", "r6siege", "rl", "hok", "wildrift", "sc2", "sc",
})

# Only show live (in-progress) games; no upcoming/scheduled
LIVE_ONLY = _get_bool("SPORTS_LIVE_ONLY", True)

# Sports to monitor: football, basketball, hockey, tennis (fallback if /sports fails)
SPORTS_TAG_IDS = {
    "football": [82, 306, 780, 450, 100351, 1494, 100350, 100100, 100639, 100977, 1234],
    "basketball": [745, 100254, 100149, 28, 101178],
    "hockey": [899, 100088],
    "tennis": [864, 101232, 102123],
}

# Flatten for API requests (used as fallback)
ALL_SPORTS_TAG_IDS = list(set(
    tid for tags in SPORTS_TAG_IDS.values() for tid in tags
))
