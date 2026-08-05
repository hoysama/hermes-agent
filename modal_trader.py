import json
import os
import subprocess
import time
import requests
from typing import Dict, Any

import modal

# Modal Volume for persistence across container restarts
storage_vol = modal.Volume.from_name("hermes-storage", create_if_missing=True)

# Define Modal Image with Freqtrade and dependencies
trader_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "curl", "build-essential", "cmake")
    .pip_install(
        "freqtrade",
        "ccxt",
        "pandas",
        "numpy",
        "requests",
        "pyjwt",
        "fastapi",
    )
)

app = modal.App("hermes-trader", image=trader_image)

HERMES_STORAGE_MOUNT = "/data"
CONFIG_PATH = "/data/hermes_trader/config_spot.json"
STRATEGY_PATH = "/data/hermes_trader/strategies/HermesQuantStrategy.py"


def _verify_proxy_auth(request_headers: dict) -> bool:
    """Verifies proxy authentication headers if required."""
    token_id = os.environ.get("MODAL_PROXY_TOKEN_ID")
    token_secret = os.environ.get("MODAL_PROXY_TOKEN_SECRET")
    if not token_id or not token_secret:
        return True  # Internal open call if no proxy tokens set
    
    auth_token = request_headers.get("x-modal-proxy-token") or request_headers.get("authorization")
    if not auth_token:
        return False
    expected = f"Bearer {token_id}:{token_secret}"
    return auth_token == expected or auth_token == f"{token_id}:{token_secret}"


DEFAULT_CONFIG_JSON = """{
    "max_open_trades": 3,
    "stake_currency": "USDT",
    "stake_amount": "10%",
    "tradable_balance_ratio": 0.99,
    "fiat_display_currency": "USD",
    "dry_run": true,
    "dry_run_wallet": 1000,
    "cancel_open_orders_on_exit": false,
    "trading_mode": "spot",
    "margin_mode": "isolated",
    "entry_pricing": {
        "price_side": "same",
        "use_order_book": true,
        "order_book_top": 1,
        "price_last_balance": 0.0,
        "check_depth_of_target_buy": true
    },
    "exit_pricing": {
        "price_side": "same",
        "use_order_book": true,
        "order_book_top": 1
    },
    "exchange": {
        "name": "binance",
        "key": "",
        "secret": "",
        "pair_whitelist": [
            "BTC/USDT",
            "ETH/USDT",
            "SOL/USDT",
            "LINK/USDT",
            "AVAX/USDT"
        ],
        "pair_blacklist": [
            ".*/BNB",
            ".*/BUSD",
            ".*/EUR"
        ]
    },
    "pair_lists": [
        {
            "method": "StaticPairList"
        }
    ],
    "stoploss": -0.02,
    "trailing_stop": true,
    "trailing_stop_positive": 0.01,
    "trailing_stop_positive_offset": 0.02,
    "trailing_only_offset_is_reached": true,
    "use_exit_signal": true,
    "minimal_roi": {
        "60": 0.01,
        "30": 0.02,
        "0": 0.04
    },
    "timeframe": "5m",
    "process_only_new_candles": true,
    "api_server": {
        "enabled": true,
        "listen_ip_address": "0.0.0.0",
        "listen_port": 8080,
        "verbosity": "info",
        "jwt_secret_key": "hermes_trader_secret_jwt_key_2026",
        "username": "hermes",
        "password": "hermes_trader_secure_password_2026"
    },
    "bot_name": "HermesTrader",
    "initial_state": "running"
}"""

DEFAULT_STRATEGY_PY = """import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy

class HermesQuantStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '5m'
    minimal_roi = {"60": 0.01, "30": 0.02, "0": 0.04}
    stoploss = -0.02
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True
    process_only_new_candles = True
    startup_candle_count: int = 30

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['ema12'] = ta.EMA(dataframe, timeperiod=12)
        dataframe['ema26'] = ta.EMA(dataframe, timeperiod=26)
        macd = ta.MACD(dataframe)
        dataframe['macdhist'] = macd['macdhist']
        dataframe['volume_mean'] = dataframe['volume'].rolling(20).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe['ema12'] > dataframe['ema26']) &
                (dataframe['rsi'] > 35) &
                (dataframe['rsi'] < 65) &
                (dataframe['macdhist'] > 0) &
                (dataframe['volume'] > dataframe['volume_mean'])
            ),
            'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe['ema12'] < dataframe['ema26']) |
                (dataframe['rsi'] > 75)
            ),
            'exit_long'] = 1
        return dataframe
"""


def _ensure_trader_files():
    """Syncs embedded trader config & strategy to Modal Volume."""
    os.makedirs("/data/hermes_trader/strategies", exist_ok=True)
    os.makedirs("/data/hermes_trader/user_data", exist_ok=True)

    if not os.path.exists(CONFIG_PATH) or os.path.getsize(CONFIG_PATH) == 0:
        with open(CONFIG_PATH, "w") as f:
            f.write(DEFAULT_CONFIG_JSON)
        storage_vol.commit()

    if not os.path.exists(STRATEGY_PATH) or os.path.getsize(STRATEGY_PATH) == 0:
        with open(STRATEGY_PATH, "w") as f:
            f.write(DEFAULT_STRATEGY_PY)
        storage_vol.commit()


@app.function(
    volumes={HERMES_STORAGE_MOUNT: storage_vol},
    secrets=[
        modal.Secret.from_name("modal_proxy_tokens"),
        modal.Secret.from_name("searxng"),
    ],
    timeout=600,
)
@modal.fastapi_endpoint(method="GET")
def status() -> Dict[str, Any]:
    """Returns the current trading status and open positions."""
    storage_vol.reload()
    _ensure_trader_files()

    # Query internal Freqtrade API server if running, or read state
    api_url = "http://127.0.0.1:8080/api/v1/status"
    try:
        resp = requests.get(api_url, auth=("hermes", "hermes_trader_secure_password_2026"), timeout=3)
        if resp.status_code == 200:
            return {"status": "running", "freqtrade": resp.json()}
    except Exception:
        pass

    # Read config to get static summary if process is inactive
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
        except Exception:
            cfg = json.loads(DEFAULT_CONFIG_JSON)
    else:
        cfg = json.loads(DEFAULT_CONFIG_JSON)

    return {
        "status": "configured",
        "bot_name": cfg.get("bot_name", "HermesTrader"),
        "dry_run": cfg.get("dry_run", True),
        "dry_run_wallet": cfg.get("dry_run_wallet", 1000),
        "stake_currency": cfg.get("stake_currency", "USDT"),
        "stake_amount": cfg.get("stake_amount", "10%"),
        "stoploss": cfg.get("stoploss", -0.02),
        "pair_whitelist": cfg.get("exchange", {}).get("pair_whitelist", []),
        "timestamp": time.time(),
    }


@app.function(
    volumes={HERMES_STORAGE_MOUNT: storage_vol},
    secrets=[
        modal.Secret.from_name("modal_proxy_tokens"),
        modal.Secret.from_name("searxng"),
    ],
    timeout=600,
)
@modal.fastapi_endpoint(method="POST")
def run_backtest() -> Dict[str, Any]:
    """Runs a quick 30-day Freqtrade backtest on Sharia pairs."""
    _ensure_trader_files()
    storage_vol.reload()

    cmd = [
        "freqtrade",
        "backtesting",
        "--config", CONFIG_PATH,
        "--strategy", "HermesQuantStrategy",
        "--strategy-path", "/data/hermes_trader/strategies",
        "--timerange", "20260701-20260805",
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {
            "success": res.returncode == 0,
            "stdout": res.stdout[-2000:],
            "stderr": res.stderr[-500:],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
