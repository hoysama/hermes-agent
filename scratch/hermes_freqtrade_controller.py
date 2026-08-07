#!/usr/bin/env python3
"""
Hermes AI Freqtrade Controller - DeepSeek-V4-Pro Powered Brain
=============================================================
Hermes (DeepSeek-V4-Pro AI Agent) makes ALL decisions: BUY / SELL / NEUTRAL
Freqtrade = dumb execution engine only (via REST API)
"""

import json
import requests
import sys
import os
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# Configuration
CONFIG = {
    'exchange': 'okx',
    'stake_currency': 'USDT',
    'wallet_size': 1000,
    'max_stake_percent': 5,
    'max_open_trades': 10,
    'min_confidence': 80,
    'max_daily_loss': 50,
    'max_weekly_loss': 150,
    'trading_pairs': [
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'LINK/USDT', 'AVAX/USDT',
        'BNB/USDT', 'XRP/USDT', 'ADA/USDT', 'DOT/USDT',
        'UNI/USDT', 'ATOM/USDT', 'LTC/USDT', 'XLM/USDT', 'ALGO/USDT'
    ]
}

# Freqtrade API
FREQTRADE_API = {
    'url': 'http://127.0.0.1:8080',
    'username': 'hermes',
    'password': 'hermes123'
}

STATE_DIR = '/root/.hermes/profiles/trader/freqtrade' if os.path.exists('/root') and os.access('/root', os.W_OK) else os.path.expanduser('~/.hermes/profiles/trader/freqtrade')
STATE_FILE = os.path.join(STATE_DIR, 'hermes_state.json')
HCNSEC_API_KEY = os.environ.get('HCNSEC_API_KEY') or os.environ.get('OPENAI_API_KEY') or 'sk-kHjwjdZoQyLn8dePrUMT7b5hGUffhfl9K3r89pENTppcanDp'
HCNSEC_BASE_URL = os.environ.get('HCNSEC_BASE_URL') or 'https://api.hcnsec.cn/v1'
MODEL_NAME = 'DeepSeek-V4-Pro'

class HermesBrain:
    """Hermes - DeepSeek-V4-Pro Powered Autonomous Trading Brain"""

    def __init__(self):
        self.market_data = {}
        self.daily_pnl = 0
        self.weekly_pnl = 0
        self.open_trades_count = 0
        self.trade_log = []
        self.load_state()

    def load_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self.daily_pnl = state.get('daily_pnl', 0)
                    self.weekly_pnl = state.get('weekly_pnl', 0)
                    self.trade_log = state.get('trade_log', [])
        except:
            pass

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            state = {
                'daily_pnl': self.daily_pnl,
                'weekly_pnl': self.weekly_pnl,
                'trade_log': self.trade_log[-100:],
                'last_update': datetime.now().isoformat()
            }
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as exc:
            print(f"Warning saving state: {exc}")

    def fetch_market_data(self) -> Dict:
        try:
            import ccxt
            exchange = ccxt.okx({'enableRateLimit': True})
            tickers = exchange.fetch_tickers()

            self.market_data = {}
            for pair in CONFIG['trading_pairs']:
                if pair in tickers:
                    ticker = tickers[pair]
                    self.market_data[pair] = {
                        'price': float(ticker.get('last', 0)),
                        'change_24h': float(ticker.get('percentage', 0)),
                        'high_24h': float(ticker.get('high', 0)),
                        'low_24h': float(ticker.get('low', 0)),
                        'volume_24h': float(ticker.get('quoteVolume', 0)),
                        'bid': float(ticker.get('bid', 0)),
                        'ask': float(ticker.get('ask', 0)),
                        'timestamp': datetime.now().isoformat()
                    }
            return self.market_data
        except Exception as e:
            return {'error': str(e)}

    def analyze_pair_llm(self, pair: str) -> Dict:
        if pair not in self.market_data:
            return {'action': 'neutral', 'confidence': 0, 'reason': 'No market data'}

        data = self.market_data[pair]
        btc_data = self.market_data.get('BTC/USDT', {})

        prompt = f"""You are Hermes AI Trading Brain powered by DeepSeek-V4-Pro, an elite crypto trader analyzing OKX spot pairs.
Analyze market metrics for {pair} and decide whether to BUY, SELL, or stay NEUTRAL.

Market Data for {pair}:
- Current Price: ${data['price']}
- 24h Price Change: {data['change_24h']:.2f}%
- 24h High: ${data['high_24h']} | 24h Low: ${data['low_24h']}
- 24h Volume: ${data['volume_24h']:,.0f}
- BTC Trend Context: Price ${btc_data.get('price', 0)}, Change {btc_data.get('change_24h', 0):.2f}%

Rules:
1. Output ONLY valid JSON with keys: "action" ("buy"|"sell"|"neutral"), "confidence" (integer 0-100), "regime" ("trending_up"|"trending_down"|"high_volatility"|"range_bound"), "reason" (short string rationale).
2. Set action to "buy" ONLY if confidence >= 80 and technical momentum is strongly bullish.
3. Be prudent and risk-averse; default to "neutral" if market is uncertain or sideways.
"""

        try:
            url = f"{HCNSEC_BASE_URL.rstrip('/')}/chat/completions"
            headers = {
                'Authorization': f'Bearer {HCNSEC_API_KEY}',
                'Content-Type': 'application/json'
            }
            body = {
                'model': MODEL_NAME,
                'messages': [
                    {'role': 'system', 'content': 'You are Hermes AI Trading Brain. Output JSON only.'},
                    {'role': 'user', 'content': prompt}
                ],
                'temperature': 0.2
            }
            res = requests.post(url, json=body, headers=headers, timeout=25)
            if res.status_code == 200:
                raw_text = res.json()['choices'][0]['message']['content'].strip()
                if '```json' in raw_text:
                    raw_text = raw_text.split('```json')[1].split('```')[0].strip()
                elif '```' in raw_text:
                    raw_text = raw_text.split('```')[1].split('```')[0].strip()
                
                decision = json.loads(raw_text)
                decision['confidence'] = float(decision.get('confidence', 50))
                decision['price'] = data['price']
                decision['change_24h'] = data['change_24h']
                print(f"  🧠 LLM {pair}: {decision['action'].upper()} | Conf: {decision['confidence']}% | Reason: {decision.get('reason', '')[:50]}")
                return decision
        except Exception as e:
            print(f"  ⚠️ LLM call fallback for {pair}: {e}")
        
        return self._fallback_analyze_pair(pair)

    def _fallback_analyze_pair(self, pair: str) -> Dict:
        data = self.market_data[pair]
        price = data['price']
        change = data['change_24h']
        volume = data['volume_24h']
        high = data['high_24h']
        low = data['low_24h']

        volatility = ((high - low) / price * 100) if price > 0 else 0
        btc_change = self.market_data.get('BTC/USDT', {}).get('change_24h', 0)

        confidence = 50
        if btc_change > 2 and change > 0: confidence += 20
        if change > 3: confidence += 15
        elif change > 1: confidence += 10
        elif change < -3: confidence -= 10

        if volume > 10_000_000: confidence += 10
        if volatility > 8: confidence -= 15

        confidence = max(0, min(100, confidence))
        regime = 'trending_up' if btc_change > 2 else ('high_volatility' if volatility > 6 else 'range_bound')
        action = 'buy' if confidence >= 80 else 'neutral'

        return {
            'action': action,
            'confidence': confidence,
            'reason': f'Hermes Fallback: Confidence {confidence:.0f}%, Regime: {regime}',
            'regime': regime,
            'price': price,
            'change_24h': change
        }

    def analyze_pair(self, pair: str) -> Dict:
        can_trade, risk_reason = self.check_risk_limits()
        if not can_trade:
            return {
                'action': 'neutral',
                'confidence': 0,
                'reason': f'Risk limit: {risk_reason}',
                'regime': 'risk_halt',
                'price': self.market_data.get(pair, {}).get('price', 0),
                'change_24h': self.market_data.get(pair, {}).get('change_24h', 0)
            }
        return self.analyze_pair_llm(pair)

    def check_risk_limits(self) -> Tuple[bool, str]:
        if self.daily_pnl <= -CONFIG['max_daily_loss']:
            return False, f"Daily loss limit reached (${self.daily_pnl:.2f} / -${CONFIG['max_daily_loss']})"
        if self.weekly_pnl <= -CONFIG['max_weekly_loss']:
            return False, f"Weekly loss limit reached (${self.weekly_pnl:.2f} / -${CONFIG['max_weekly_loss']})"
        return True, "Risk check passed"

    def check_freqtrade_status(self) -> Dict:
        try:
            res = requests.get(f"{FREQTRADE_API['url']}/api/v1/ping", timeout=5)
            return {'status': 'online', 'ping': res.status_code == 200}
        except Exception as e:
            return {'status': 'offline', 'error': str(e)}

    def get_open_positions(self) -> List[Dict]:
        try:
            auth = (FREQTRADE_API['username'], FREQTRADE_API['password'])
            res = requests.get(f"{FREQTRADE_API['url']}/api/v1/status", auth=auth, timeout=5)
            if res.status_code == 200:
                return res.json()
            return []
        except Exception as e:
            print(f"Error getting positions: {e}")
            return []

    def execute_buy(self, pair: str, stake: float) -> bool:
        try:
            auth = (FREQTRADE_API['username'], FREQTRADE_API['password'])
            payload = {'pair': pair, 'stake_amount': stake}
            res = requests.post(f"{FREQTRADE_API['url']}/api/v1/forceenter", auth=auth, json=payload, timeout=10)
            if res.status_code == 200:
                print(f"🟢 Force-enter executed for {pair}: ${stake:.2f}")
                return True
            else:
                print(f"🔴 Force-enter failed for {pair}: {res.text}")
                return False
        except Exception as e:
            print(f"Error executing buy for {pair}: {e}")
            return False

    def execute_sell(self, trade_id: int, pair: str) -> bool:
        try:
            auth = (FREQTRADE_API['username'], FREQTRADE_API['password'])
            payload = {'tradeid': str(trade_id), 'ordertype': 'market'}
            res = requests.post(f"{FREQTRADE_API['url']}/api/v1/forcesell", auth=auth, json=payload, timeout=10)
            if res.status_code == 200:
                print(f"🔴 Force-sell executed for trade {trade_id} ({pair})")
                return True
            else:
                print(f"🔴 Force-sell failed for trade {trade_id}: {res.text}")
                return False
        except Exception as e:
            print(f"Error executing sell for trade {trade_id}: {e}")
            return False

    def run_cycle(self) -> Dict:
        print("=" * 70)
        print(f"🧠 HERMES AI TRADING CYCLE (DeepSeek-V4-Pro LLM) - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)

        # Step 1: Market Data
        print("\n📊 Phase 1: Hermes Market Analysis & DeepSeek AI Reasoning")
        print("-" * 55)
        self.fetch_market_data()

        decisions = {}
        buys = []
        neutrals = []

        for pair in CONFIG['trading_pairs']:
            decision = self.analyze_pair(pair)
            decisions[pair] = decision

            if decision['action'] == 'buy' and decision['confidence'] >= CONFIG['min_confidence']:
                base_size = CONFIG['wallet_size'] * (CONFIG['max_stake_percent'] / 100)
                conf_mult = 0.5 + (decision['confidence'] / 100)
                stake = min(base_size * conf_mult, CONFIG['wallet_size'] * 0.10)
                buys.append({'pair': pair, 'stake': stake, 'confidence': decision['confidence'], 'reason': decision.get('reason', '')})
            else:
                neutrals.append(pair)

        # Step 2: Freqtrade Status & Positions
        print("\n📈 Phase 2: Current Positions (Freqtrade)")
        print("-" * 55)
        positions = self.get_open_positions()
        if positions:
            for p in positions:
                print(f"  • Trade #{p.get('trade_id')}: {p.get('pair')} | Entry: ${p.get('open_rate')} | PnL: {p.get('profit_pct', 0):.2f}%")
        else:
            print("  No open positions")

        # Step 3: Execution
        print("\n⚡ Phase 3: Execution (Hermes decides -> Freqtrade executes)")
        print("-" * 55)
        executed_buys = 0
        executed_sells = 0

        for buy_op in buys:
            if self.open_trades_count < CONFIG['max_open_trades']:
                if self.execute_buy(buy_op['pair'], buy_op['stake']):
                    executed_buys += 1
                    self.open_trades_count += 1
                    self.save_state()

        # Summary
        print("\n📋 Phase 4: Cycle Summary")
        print("-" * 55)
        print(f"  🟢 Buys Executed:  {executed_buys}")
        print(f"  🔴 Sells Executed: {executed_sells}")
        print(f"  ⏸️  Held/Neutral:   {len(neutrals)}")

        self.save_state()
        print("\n" + "=" * 70)
        print("Cycle completed")
        return {'status': 'success', 'buys': executed_buys, 'sells': executed_sells, 'decisions': decisions}

if __name__ == '__main__':
    brain = HermesBrain()
    brain.run_cycle()
