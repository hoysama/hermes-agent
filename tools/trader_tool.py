"""
Hermes Trader Tool - Quantitative Trading Operations & Portfolio Reporting

Interacts with the hermes-trader microservice on Modal (Freqtrade Spot).
"""

import os
import requests
from typing import Dict, Any

from tools.registry import registry
from tools.tool_backend_helpers import get_modal_auth_headers

MODAL_TRADER_STATUS_ENDPOINT = "https://hoysama--hermes-trader-status.modal.run"
MODAL_TRADER_BACKTEST_ENDPOINT = "https://hoysama--hermes-trader-run-backtest.modal.run"


TRADER_STATUS_SCHEMA = {
    "name": "trader_status",
    "description": "Get current Hermes Trader status, active positions, wallet balance, and trading performance metrics on Binance Spot.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "backtest"],
                "description": "Action to perform: 'status' (get live status and balance) or 'backtest' (run 30-day historical strategy backtest)",
            }
        },
        "required": [],
    },
}


def trader_status_tool(args: Dict[str, Any] = None, **kwargs) -> Dict[str, Any]:
    """Handler for trader_status tool."""
    args = args or {}
    action = args.get("action", "status")
    headers = get_modal_auth_headers()

    if action == "backtest":
        try:
            resp = requests.post(MODAL_TRADER_BACKTEST_ENDPOINT, headers=headers, json={}, timeout=130)
            if resp.status_code == 200:
                return {"status": "success", "result": resp.json()}
            return {"status": "error", "code": resp.status_code, "detail": resp.text}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # Default action = status
    try:
        resp = requests.get(MODAL_TRADER_STATUS_ENDPOINT, headers=headers, timeout=10)
        if resp.status_code == 200:
            return {"status": "success", "data": resp.json()}
        return {"status": "error", "code": resp.status_code, "detail": resp.text}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Register tool using official registry.register(name=..., schema=..., handler=...)
registry.register(
    name="trader_status",
    toolset="trading",
    schema=TRADER_STATUS_SCHEMA,
    handler=lambda args, **kw: trader_status_tool(args, **kw),
    emoji="📈",
)
