"""
Autonomous Trading Loop Script for Hermes Trader
Powered by DeepSeek-V4-Pro on custom:hcnsec
"""

import os
import sys
import json
import logging
import urllib.request

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("autonomous_trader")

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
os.environ["HERMES_HOME"] = HERMES_HOME

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from run_agent import AIAgent

HCNSEC_API_KEY = os.environ.get("HCNSEC_API_KEY", "")
HCNSEC_BASE_URL = "https://api.hcnsec.cn/v1"
MODEL_NAME = "DeepSeek-V4-Pro"
PROVIDER_NAME = "custom:hcnsec"

TRADER_STATUS_ENDPOINT = os.environ.get(
    "FREQTRADE_STATUS_ENDPOINT", "http://127.0.0.1:8080/api/v1/status"
)
FREQTRADE_USERNAME = os.environ.get("FREQTRADE_API_USERNAME", "hermes")
FREQTRADE_PASSWORD = os.environ.get("FREQTRADE_API_PASSWORD", "")


def get_market_status() -> dict:
    """Fetch current Freqtrade market status & indicators."""
    try:
        credentials = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        credentials.add_password(None, TRADER_STATUS_ENDPOINT, FREQTRADE_USERNAME, FREQTRADE_PASSWORD)
        auth_handler = urllib.request.HTTPBasicAuthHandler(credentials)
        opener = urllib.request.build_opener(auth_handler)
        req = urllib.request.Request(TRADER_STATUS_ENDPOINT, headers={"User-Agent": "HermesAutonomousTrader/1.0"})
        with opener.open(req, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        logger.error("Could not reach Freqtrade endpoint: %s", e)
        return {"status": "unavailable", "llm_unavailable": True}


def run_autonomous_evaluation():
    """Run an autonomous trading decision turn powered by DeepSeek-V4-Pro."""
    logger.info(f"Starting autonomous evaluation using {MODEL_NAME} via {PROVIDER_NAME}...")

    if not HCNSEC_API_KEY:
        logger.error("HCNSEC_API_KEY is not configured; refusing to evaluate or trade")
        return None
    
    market_data = get_market_status()
    if market_data.get("llm_unavailable") or market_data.get("status") == "unavailable":
        logger.error("Freqtrade unavailable; execution blocked")
        return None
    
    prompt = (
        f"أنت المحلل المالي والاستراتيجي لهرمس المتداول المربوط بـ OKX.\n"
        f"نمط التداول الحالي: تجريبي ورقي (dry_run: True) بـ رصيد 1000$ USDT.\n"
        f"بيانات السوق الحالية:\n{json.dumps(market_data, ensure_ascii=False, indent=2)}\n\n"
        f"المطلوب:\n"
        f"1. قم بتحليل اتجاه السوق الحالي للـ 15 عملة الحلال (BTC, ETH, SOL, LINK, AVAX, BNB, XRP, ADA, DOT, UNI, ATOM, LTC, XLM, ALGO).\n"
        f"2. اتخذ قرار الشراء أو البيع التلقائي للعملات الصاعدة بقوة (مثل ADA أو SOL) مع إدراج نسبة الثقة والهدف الستراتيجي.\n"
        f"3. نسق تقريراً تداولياً احترافياً ومباشراً للنمط المستقل ليتم بثه لحساب المستخدم بـ التلغرام."
    )

    try:
        agent = AIAgent(
            base_url=HCNSEC_BASE_URL,
            api_key=HCNSEC_API_KEY,
            provider=PROVIDER_NAME,
            model=MODEL_NAME,
            quiet_mode=True,
        )
        decision = agent.chat(prompt)
        logger.info("Autonomous decision generated successfully!")
        return decision
    except Exception as e:
        logger.error(f"Error executing DeepSeek-V4-Pro autonomous evaluation: {e}")
        return None


if __name__ == "__main__":
    result = run_autonomous_evaluation()
    if result:
        print("=== AUTONOMOUS DECISION OUTPUT ===")
        print(result)
