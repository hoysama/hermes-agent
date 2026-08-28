#!/usr/bin/env python3
"""Hermes AI Freqtrade Controller v3.0 — AGGRESSIVE MODE
Stake: 30% per trade | TP: 10% | SL: 3%
Provider: nararouter (ox-alpha-bynara analysis + agnes-2.5-flash decisions)
"""
import json
import requests
import sys
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_strategy_engine import StrategyEngine

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

CONFIG = {
    'exchange': 'okx',
    'stake_currency': 'USDT',
    'wallet_size': 1000,
    'max_stake_percent': 30,
    'max_open_trades': 8,
    'stake_amount': 20,
    # Positions are evaluated continuously by each cycle. A long-held
    # position that is not profitable is exited rather than held forever.
    'time_stop_hours': 3,
    'max_daily_loss': 20,
    'max_weekly_loss': 150,
    'trading_pairs': [
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT',
        'BNB/USDT', 'LINK/USDT', 'ADA/USDT', 'AVAX/USDT'
    ],
    # Breakeven Lock: protect profits once they reach a threshold
    'breakeven_trigger_pct': 1.5,    # activate lock when PnL reaches +1.5%
    'breakeven_lock_pct': 0.3,       # lock exit at +0.3% (guaranteed small profit)
    # Regime-Aware Position Sizing: scale stake by market regime
    'regime_stake_multiplier': {
        'trending_up': 1.0,        # 100% — bullish
        'breakout': 1.0,           # 100% — breakout
        'accumulation': 0.85,      # 85%  — accumulation
        'recovery': 0.70,          # 70%  — recovery
        'range_bound': 0.60,       # 60%  — sideways
        'high_volatility': 0.50,   # 50%  — volatile
        'trending_down': 0.40,     # 40%  — bearish
        'distribution': 0.30,      # 30%  — distribution
        'crash': 0.0,              # 0%   — crash = no trade
    },
    # Staged Take Profit: partial exits at intermediate profit levels
    'staged_tp_levels': [
        {'pnl_pct': 3.0, 'sell_fraction': 0.50},  # at +3% → sell 50%
        {'pnl_pct': 6.0, 'sell_fraction': 0.50},  # at +6% → sell 50% of remaining
    ],
}

FREQTRADE_CONFIG_PATH = '/root/.hermes/profiles/trader/freqtrade/config/config.json'
if os.path.exists(FREQTRADE_CONFIG_PATH):
    try:
        with open(FREQTRADE_CONFIG_PATH, 'r') as _cfg_f:
            _ft_cfg = json.load(_cfg_f)
            _wl = _ft_cfg.get('exchange', {}).get('pair_whitelist', [])
            if _wl:
                CONFIG['trading_pairs'] = list(_wl)
                CONFIG['max_open_trades'] = len(_wl)
            if 'stake_amount' in _ft_cfg and isinstance(_ft_cfg['stake_amount'], (int, float)):
                CONFIG['stake_amount'] = float(_ft_cfg['stake_amount'])
    except Exception:
        pass

FREQTRADE_API = {'url': 'http://127.0.0.1:8080', 'username': 'hermes', 'password': 'hermes123'}
STATE_DIR = '/root/.hermes/profiles/trader/freqtrade'
STATE_FILE = os.path.join(STATE_DIR, 'hermes_state.json')

# NaraRouter 4-tier model fallback chains (1 Primary + 3 Fallbacks)
NARAROUTER_BASE_URL = os.environ.get('NARAROUTER_BASE_URL', 'https://router.bynara.id/v1')
NARAROUTER_API_KEY = os.environ.get('NARAROUTER_API_KEY', '')

ANALYSIS_MODELS = [
    'deepseek-v4-flash',    # Primary: 2.7s ultra-fast deep macro analysis
    'agnes-2.5-flash',      # Fallback 1: precise & economic
    'agnes-2.0-flash',      # Fallback 2: fast & low cost
    'minimax-m3-free',      # Fallback 3: free safety net
]

DECISION_MODELS = [
    'agnes-2.5-flash',      # Primary: 0.3x precise pair decisions & exit reviews
    'deepseek-v4-flash',    # Fallback 1: deep reasoning fallback
    'agnes-2.0-flash',      # Fallback 2: fast & low cost
    'minimax-m3-free',      # Fallback 3: free safety net
]

ANALYSIS_MODEL = ANALYSIS_MODELS[0]
DECISION_MODEL = DECISION_MODELS[0]

_engine = None

def get_engine() -> StrategyEngine:
    global _engine
    if _engine is None:
        _engine = StrategyEngine(
            llm_url=NARAROUTER_BASE_URL,
            llm_key=NARAROUTER_API_KEY,
            model=DECISION_MODEL,
            analysis_model=ANALYSIS_MODEL,
            analysis_fallback=ANALYSIS_MODELS[1],
            decision_model=DECISION_MODEL,
            analysis_models=ANALYSIS_MODELS,
            decision_models=DECISION_MODELS,
        )
    return _engine


class HermesBrain:
    def __init__(self):
        self.engine = get_engine()
        self.market_data = {}
        self.daily_pnl = 0
        self.weekly_pnl = 0
        self.open_trades_count = 0
        self.trade_log = []
        self.last_decisions = {}
        self.last_update = None
        self.daily_rotations = []
        self.peak_pnl_tracker = {}    # {trade_id: max_pnl_pct}
        self.staged_exits_done = {}   # {trade_id: [completed_level_indices]}
        self.load_state()

    def load_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self.daily_pnl = state.get('daily_pnl', 0)
                    self.weekly_pnl = state.get('weekly_pnl', 0)
                    self.trade_log = state.get('trade_log', [])
                    self.last_decisions = state.get('last_decisions', {})
                    self.last_update = state.get('last_update')
                    self.daily_rotations = state.get('daily_rotations', [])
                    self.peak_pnl_tracker = {str(k): v for k, v in state.get('peak_pnl_tracker', {}).items()}
                    self.staged_exits_done = {str(k): v for k, v in state.get('staged_exits_done', {}).items()}
        except:
            pass

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            state = {
                'daily_pnl': self.daily_pnl,
                'weekly_pnl': self.weekly_pnl,
                'trade_log': self.trade_log[-100:],
                'last_decisions': self.last_decisions,
                'strategy_summary': self.engine.store.get_summary() if self.engine else {},
                'last_update': datetime.now().isoformat(),
                'daily_rotations': self.daily_rotations[-20:],
                'peak_pnl_tracker': self.peak_pnl_tracker,
                'staged_exits_done': self.staged_exits_done,
            }
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as exc:
            print(f"Warning saving state: {exc}")

    @staticmethod
    def _format_candles_compact(candles: list, tf_label: str) -> str:
        """Format OHLCV candles into a compact string for LLM prompts."""
        if not candles:
            return f"  {tf_label}: no data"
        lines = []
        for c in candles[-6:]:  # last 6 candles for prompt brevity
            ts, o, h, l, close, vol = c[0], c[1], c[2], c[3], c[4], c[5]
            t_str = datetime.utcfromtimestamp(ts / 1000).strftime('%H:%M') if ts > 1e9 else '??:??'
            direction = '▲' if close >= o else '▼'
            lines.append(f"    {t_str} {direction} {o:.2f}→{close:.2f} (H{h:.2f}/L{l:.2f})")
        return f"  {tf_label} (last {len(lines)}):\n" + '\n'.join(lines)

    @staticmethod
    def _candle_summary(candles_1h: list) -> Dict:
        """Compute lightweight technical indicators from 1h candles."""
        if not candles_1h or len(candles_1h) < 5:
            return {}
        closes = [c[4] for c in candles_1h]
        highs = [c[2] for c in candles_1h]
        lows = [c[3] for c in candles_1h]
        volumes = [c[5] for c in candles_1h]
        # EMA-12 approximation
        ema = closes[0]
        k = 2 / (min(12, len(closes)) + 1)
        for p in closes[1:]:
            ema = p * k + ema * (1 - k)
        # RSI-14 approximation
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-14:]) / min(14, len(gains)) if gains else 0
        avg_loss = sum(losses[-14:]) / min(14, len(losses)) if losses else 0.001
        rsi = 100 - (100 / (1 + avg_gain / max(avg_loss, 0.001)))
        # ATR-14 approximation
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        atr = sum(trs[-14:]) / min(14, len(trs)) if trs else 0
        
        return {
            'ema12': round(ema, 4),
            'rsi14': round(rsi, 1),
            'atr14': round(atr, 4),
            'high_24h_candle': round(max(highs[-24:]), 4),
            'low_24h_candle': round(min(lows[-24:]), 4),
            'avg_volume': round(sum(volumes[-24:]) / min(24, len(volumes)), 0),
            'trend': 'up' if closes[-1] > ema else 'down',
        }

    def fetch_market_data(self, extra_pairs: Optional[List[str]] = None) -> Dict:
        try:
            import ccxt
            # Request configured symbols plus any active open trade pairs
            exchange = ccxt.okx({
                'enableRateLimit': True,
                'timeout': 15000,
            })
            symbols = list(dict.fromkeys(list(CONFIG['trading_pairs']) + [p for p in (extra_pairs or []) if p]))
            print(f"  📡 Fetching live OKX tickers ({len(symbols)} pairs)...", flush=True)
            tickers = exchange.fetch_tickers(symbols)
            print(f"  ✅ Received {len(tickers)} OKX tickers", flush=True)
            self.market_data = {}
            for pair in symbols:
                if pair in tickers:
                    ticker = tickers[pair]
                    self.market_data[pair] = {
                        'price': float(ticker.get('last', 0)),
                        'change_24h': float(ticker.get('percentage', 0)),
                        'high_24h': float(ticker.get('high', 0)),
                        'low_24h': float(ticker.get('low', 0)),
                        'volume_24h': float(ticker.get('quoteVolume', 0)),
                    }

            # Fetch OHLCV candles: 24×1h + 12×4h per pair
            print(f"  📡 Fetching OHLCV candles ({len(symbols)} pairs × 2 timeframes)...", flush=True)
            candle_errors = 0
            for pair in symbols:
                if pair not in self.market_data:
                    continue
                try:
                    candles_1h = exchange.fetch_ohlcv(pair, '1h', limit=24)
                    candles_4h = exchange.fetch_ohlcv(pair, '4h', limit=12)
                    self.market_data[pair]['candles_1h'] = candles_1h
                    self.market_data[pair]['candles_4h'] = candles_4h
                    self.market_data[pair]['indicators'] = self._candle_summary(candles_1h)
                except Exception:
                    candle_errors += 1
                    self.market_data[pair]['candles_1h'] = []
                    self.market_data[pair]['candles_4h'] = []
                    self.market_data[pair]['indicators'] = {}
            if candle_errors:
                print(f"  ⚠️ Candle fetch errors: {candle_errors}/{len(symbols)} pairs", flush=True)
            else:
                print(f"  ✅ Candles loaded for {len(symbols)} pairs (24×1h + 12×4h)", flush=True)

            print(f"  📡 Fetching Order Books ({len(symbols)} pairs)...", flush=True)
            for pair in symbols:
                if pair not in self.market_data:
                    continue
                try:
                    ob = exchange.fetch_order_book(pair, limit=20)
                    bids = sum([b[1] for b in ob.get('bids', [])])
                    asks = sum([a[1] for a in ob.get('asks', [])])
                    total = bids + asks
                    bid_ratio = (bids / total * 100) if total > 0 else 50
                    self.market_data[pair]['orderbook'] = {
                        'bid_ratio': round(bid_ratio, 1),
                        'ask_ratio': round(100 - bid_ratio, 1)
                    }
                except Exception:
                    self.market_data[pair]['orderbook'] = {'bid_ratio': 50, 'ask_ratio': 50}

            if not self.market_data:
                raise RuntimeError("OKX returned no configured tickers")
            return self.market_data
        except Exception as e:
            print(f"  ❌ OKX market data unavailable: {e}", flush=True)
            return {'error': str(e)}

    def check_risk_limits(self) -> Tuple[bool, str]:
        if self.daily_pnl <= -CONFIG['max_daily_loss']:
            return False, f"Daily loss limit reached (${self.daily_pnl:.2f})"
        if self.weekly_pnl <= -CONFIG['max_weekly_loss']:
            return False, f"Weekly loss limit reached (${self.weekly_pnl:.2f})"
        return True, "Risk check passed"

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

    def get_available_balance(self) -> Optional[float]:
        """Read free USDT from Freqtrade before sizing any new entry."""
        try:
            res = requests.get(
                f"{FREQTRADE_API['url']}/api/v1/balance",
                auth=(FREQTRADE_API['username'], FREQTRADE_API['password']),
                timeout=5,
            )
            res.raise_for_status()
            payload = res.json()
            for currency in payload.get('currencies', []):
                if currency.get('currency') == CONFIG['stake_currency']:
                    return float(currency.get('free', 0) or 0)
            for currency in payload.get('balances', []):
                if currency.get('currency') == CONFIG['stake_currency']:
                    return float(currency.get('free', 0) or 0)
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            print(f"  ⚠️ Balance unavailable; blocking new entries: {exc}")
        return None
    def execute_buy(self, pair: str, stake: float = None) -> Optional[int]:
        try:
            auth = (FREQTRADE_API['username'], FREQTRADE_API['password'])
            payload = {'pair': pair}
            res = requests.post(f"{FREQTRADE_API['url']}/api/v1/forceenter", auth=auth, json=payload, timeout=30)
            if res.status_code == 200:
                result = res.json()
                trade_id = result.get('trade_id')
                actual_stake = result.get('stake_amount', stake or 0)
                print(f"  🟢 BUY {pair}: ${actual_stake:.2f} (Trade #{trade_id})")
                return trade_id
            elif "lower than stake amount" in res.text or "Available balance" in res.text:
                print(f"  ⏭️ BUY skipped {pair}: insufficient balance in Freqtrade wallet")
                return None
            else:
                print(f"  ❌ BUY failed {pair}: {res.text[:100]}")
            return None
        except Exception as e:
            print(f"  ❌ Error buying {pair}: {e}")
            return None

    def execute_sell(self, trade_id: int, pair: str) -> bool:
        try:
            auth = (FREQTRADE_API['username'], FREQTRADE_API['password'])
            payload = {'tradeid': str(trade_id), 'ordertype': 'market'}
            res = requests.post(f"{FREQTRADE_API['url']}/api/v1/forcesell", auth=auth, json=payload, timeout=10)
            if res.status_code == 200:
                print(f"  🔴 SELL Trade #{trade_id} ({pair})")
                # Clean up trackers for fully closed trades
                self.peak_pnl_tracker.pop(str(trade_id), None)
                self.staged_exits_done.pop(str(trade_id), None)
                return True
            else:
                print(f"  ❌ SELL failed #{trade_id}: {res.text[:100]}")
            return False
        except Exception as e:
            print(f"  ❌ Error selling trade #{trade_id}: {e}")
            return False

    def execute_partial_sell(self, trade_id: int, pair: str, amount: float) -> bool:
        """Sell a partial amount of an open trade for staged take profit."""
        try:
            auth = (FREQTRADE_API['username'], FREQTRADE_API['password'])
            payload = {'tradeid': str(trade_id), 'ordertype': 'market', 'amount': amount}
            res = requests.post(f"{FREQTRADE_API['url']}/api/v1/forcesell", auth=auth, json=payload, timeout=10)
            if res.status_code == 200:
                print(f"  🟡 PARTIAL SELL Trade #{trade_id} ({pair}): {amount:.6f} units")
                return True
            else:
                print(f"  ❌ PARTIAL SELL failed #{trade_id}: {res.text[:100]}")
            return False
        except Exception as e:
            print(f"  ❌ Error partial selling trade #{trade_id}: {e}")
            return False

    def run_exit_cycle(self) -> Dict:
        """Review open positions frequently without opening new positions."""
        print("=" * 70)
        print(f"🔍 HERMES EXIT REVIEW | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Provider: nararouter ({DECISION_MODEL} exit analysis + confirmation)")
        print("=" * 70)

        positions = self.get_open_positions()
        if not positions:
            print("  No open positions; exit review skipped")
            return {'status': 'exit_review_complete', 'buys': 0, 'sells': 0}

        open_pairs = [p.get('pair') for p in positions if isinstance(p, dict) and p.get('pair')]
        market_data = self.fetch_market_data(extra_pairs=open_pairs)
        if 'error' in market_data:
            print("  ⛔ Market data unavailable; exit review blocked")
            return {'status': 'market_data_unavailable', 'buys': 0, 'sells': 0}

        review = self.engine.analyze_open_positions(market_data, positions)
        decisions = review['decisions']
        regime = review['regime']
        executed_sells = 0
        rotation = self._rotation_candidate(positions, decisions, market_data)
        rotated_trade_id = None
        if rotation:
            rotation_result = self._execute_rotation(rotation, positions)
            if rotation_result.get('status') == 'completed':
                executed_sells += 1
                rotated_trade_id = rotation.get('from_trade_id')
        for position in positions:
            if position.get('trade_id') == rotated_trade_id:
                continue
            pair = position.get('pair')
            trade_id = position.get('trade_id')
            if not pair or not trade_id:
                continue
            pair_market = market_data.get(pair)
            if not pair_market:
                continue
            decision = decisions.get(pair, {})
            action = decision.get('action', 'neutral')
            confidence = decision.get('confidence', 0)
            pnl_pct = position.get('profit_pct', 0)
            levels = self._dynamic_exit_levels(position, pair_market, decision)
            take_profit = levels['take_profit_pct']
            stop_loss = levels['stop_loss_pct']
            age_hours = self._position_age_hours(position)

            # Track peak PnL for breakeven lock
            tid = str(trade_id)
            self.peak_pnl_tracker[tid] = max(self.peak_pnl_tracker.get(tid, 0), pnl_pct)
            peak_pnl = self.peak_pnl_tracker[tid]

            # Staged Take Profit: partial exits at intermediate levels
            done_stages = self.staged_exits_done.get(tid, [])
            for level_idx, level in enumerate(CONFIG.get('staged_tp_levels', [])):
                if level_idx in done_stages:
                    continue
                if pnl_pct >= level['pnl_pct']:
                    current_amount = position.get('amount', 0)
                    sell_amount = current_amount * level['sell_fraction']
                    if sell_amount > 0 and self.execute_partial_sell(trade_id, pair, sell_amount):
                        if tid not in self.staged_exits_done:
                            self.staged_exits_done[tid] = []
                        self.staged_exits_done[tid].append(level_idx)
                        self.trade_log.append({
                            'timestamp': datetime.now().isoformat(), 'pair': pair,
                            'action': 'partial_sell', 'pnl_pct': pnl_pct,
                            'level_pct': level['pnl_pct'], 'fraction': level['sell_fraction'],
                            'trade_id': trade_id,
                        })
                        self.save_state()

            should_sell = False
            reason = ''
            
            indicators = self.market_data.get(pair, {}).get('indicators', {})
            current_price = self.market_data.get(pair, {}).get('price', 0)
            ema12 = indicators.get('ema12', 0)
            atr14 = indicators.get('atr14', 0)
            trailing_stop_price = ema12 - atr14

            if pnl_pct >= take_profit:
                should_sell, reason = True, f"TAKE PROFIT {pnl_pct:.1f}% (target {take_profit:.1f}%)"
            elif pnl_pct <= stop_loss:
                should_sell, reason = True, f"STOP LOSS {pnl_pct:.1f}% (limit {stop_loss:.1f}%)"
            elif ema12 > 0 and atr14 > 0 and peak_pnl > 1.0 and current_price < trailing_stop_price:
                should_sell, reason = True, f"TRAILING STOP (price ${current_price:.4f} < ${trailing_stop_price:.4f})"
            elif peak_pnl >= CONFIG['breakeven_trigger_pct'] and pnl_pct <= CONFIG['breakeven_lock_pct']:
                should_sell, reason = True, f"BREAKEVEN LOCK (peak {peak_pnl:.1f}% → now {pnl_pct:.1f}%)"
            elif age_hours >= CONFIG['time_stop_hours']:
                should_sell, reason = True, f"TIME STOP {age_hours:.1f}h (limit {CONFIG['time_stop_hours']}h, pnl {pnl_pct:+.2f}%)"
            elif pnl_pct < -4.0 and self.check_news_disaster(pair):
                should_sell, reason = True, f"PANIC SELL (NEWS DISASTER DETECTED)"
            elif action == 'sell' and confidence >= 70:
                confirmed, confirm_reason = self.engine.confirm_trade_signal(
                    pair, decision, pair_market, regime.get('primary_regime', 'range_bound'), position
                )
                if confirmed:
                    should_sell, reason = True, f"EXIT REVIEW SELL ({confirm_reason})"
                else:
                    print(f"  ⏭️ SELL skipped {pair}: confirmation rejected ({confirm_reason})")
            if should_sell and self.execute_sell(trade_id, pair):
                executed_sells += 1
                pnl_dollar = (pnl_pct / 100) * position.get('stake_amount', 0)
                strategy_id = decision.get('strategy_id', 'none')
                if strategy_id != 'none':
                    self.engine.record_trade_result(strategy_id, pnl_dollar, pnl_pct, age_hours)
                self.trade_log.append({
                    'timestamp': datetime.now().isoformat(), 'pair': pair,
                    'action': 'sell', 'confidence': confidence,
                    'pnl': pnl_dollar, 'pnl_pct': pnl_pct,
                    'strategy_id': strategy_id, 'trade_id': trade_id,
                    'sell_reason': reason, 'regime': regime.get('primary_regime', 'exit_review'),
                })
                self.save_state()
        print(f"Exit review complete: SELL executed={executed_sells}")
        return {'status': 'exit_review_complete', 'buys': 0, 'sells': executed_sells}

    def check_news_disaster(self, pair: str) -> bool:
        try:
            import os, requests
            searxng_token = os.environ.get("MODAL_PROXY_TOKEN_SEARXNG")
            extractor_token = os.environ.get("MODAL_PROXY_TOKEN_WEB_EXTRACTOR")
            if not searxng_token or not extractor_token:
                return False
                
            print(f"  🗞️ Fetching news for {pair}...", flush=True)
            url = "https://hoysama--hermes-searxng-search.modal.run/search"
            headers_s = {"Authorization": f"Bearer {searxng_token}", "Content-Type": "application/json"}
            res = requests.post(url, json={"query": f"{pair} crypto news", "engines": "google,bing,duckduckgo", "format": "json"}, headers=headers_s, timeout=7)
            if not res.ok: return False
            
            results = res.json().get('results', [])
            if not results: return False
            
            top_url = results[0].get('url')
            if not top_url: return False
            
            print(f"  🕷️ Extracting article: {top_url[:40]}...", flush=True)
            ext_url = "https://hoysama--hermes-web-extractor-extract.modal.run/extract"
            headers_e = {"Authorization": f"Bearer {extractor_token}", "Content-Type": "application/json"}
            ext_res = requests.post(ext_url, json={"url": top_url}, headers=headers_e, timeout=12)
            
            article_text = ""
            if ext_res.ok:
                article_text = ext_res.json().get('markdown', '')
                
            if not article_text:
                article_text = "\n".join([r.get('title', '') + " - " + r.get('content', '') for r in results[:3]])
                
            return self.engine.analyze_news_disaster(pair, article_text)
        except Exception as e:
            print(f"  ⚠️ News fetch error: {e}", flush=True)
            return False

    def _rotation_candidate(self, positions, decisions, market_data):
        today = datetime.now(timezone.utc).date().isoformat()
        self.daily_rotations = [r for r in self.daily_rotations if r.get('date') == today]
        if len(self.daily_rotations) >= 2:
            return None
        free = self.get_available_balance()
        has_open_slot = len(positions) < CONFIG['max_open_trades']
        has_enough_free_balance = free is not None and (free * 0.985) >= min(15, CONFIG['stake_amount'])
        if has_open_slot and has_enough_free_balance:
            return None
        return self.engine.evaluate_asset_rotation(positions, decisions, market_data)

    def _execute_rotation(self, rotation, positions):
        from_pair = rotation['from_pair']; to_pair = rotation['to_pair']
        trade_id = rotation.get('from_trade_id'); stake = rotation.get('stake', 0)
        if not trade_id or stake < 15 or from_pair == to_pair:
            return {'status': 'rejected', 'reason': 'invalid rotation'}
        print(f"  🔄 ROTATION candidate: {from_pair} -> {to_pair} (spread {rotation['spread']:.0f}%)")
        if not self.execute_sell(trade_id, from_pair):
            return {'status': 'sell_failed'}
        time.sleep(2)
        refreshed = self.get_open_positions()
        if any(p.get('trade_id') == trade_id for p in refreshed):
            return {'status': 'sell_not_confirmed'}
        balance = self.get_available_balance()
        if balance is None:
            return {'status': 'balance_unavailable'}
        stake = round(min(stake, balance * 0.985), 2)
        new_trade_id = self.execute_buy(to_pair, stake)
        record = {
            'date': datetime.now(timezone.utc).date().isoformat(),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'from_pair': from_pair, 'to_pair': to_pair,
            'from_trade_id': trade_id, 'new_trade_id': new_trade_id,
            'spread': rotation['spread'], 'reason': rotation['reason'],
            'sell_status': 'confirmed', 'buy_status': 'confirmed' if new_trade_id else 'failed',
        }
        self.daily_rotations.append(record)
        self.trade_log.append({'action': 'rotation', **record})
        self.save_state()
        print(f"  {'✅' if new_trade_id else '⚠️'} ROTATION {'completed' if new_trade_id else 'partial'}: {from_pair} -> {to_pair}")
        return {'status': 'completed' if new_trade_id else 'partial', **record}

    def _position_age_hours(self, position: Dict) -> float:
        opened_at = position.get('open_date') or position.get('open_date_utc')
        if not opened_at:
            return 0.0
        try:
            opened = datetime.fromisoformat(str(opened_at).replace('Z', '+00:00'))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - opened).total_seconds() / 3600)
        except (TypeError, ValueError):
            return 0.0

    def _dynamic_exit_levels(self, position, market, decision):
        return self.engine.dynamic_exit_levels(position, market, decision)

    def run_cycle(self) -> Dict:
        print("=" * 70)
        print(f"🧠 HERMES v3.0 AGGRESSIVE (30% stake, 10% TP)")
        print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Provider: nararouter ({ANALYSIS_MODEL} analysis -> {DECISION_MODEL} decisions)")
        print("=" * 70)

        can_trade, risk_reason = self.check_risk_limits()
        if not can_trade:
            print(f"\n⛔ RISK HALT: {risk_reason}")
            return {'status': 'risk_halted'}

        # Phase 1: Market Data
        print("\n📊 Phase 1: Market Analysis")
        positions = self.get_open_positions()
        open_pairs = [p.get('pair') for p in positions if isinstance(p, dict) and p.get('pair')]
        market_data = self.fetch_market_data(extra_pairs=open_pairs)
        if 'error' in market_data:
            print("  ⛔ Market data unavailable; execution blocked", flush=True)
            return {'status': 'market_data_unavailable', 'buys': 0, 'sells': 0}

        # Read positions before LLM analysis so each open position receives
        # explicit HOLD/SELL context rather than being treated as an entry.

        # Phase 2: Strategy Selection
        print("\n🧬 Phase 2: Dynamic Strategy Selection")
        engine_result = self.engine.analyze_market_and_select(self.market_data, positions)
        regime = engine_result['regime']
        decisions = engine_result['decisions']
        
        # Freqtrade is execution-only: if the analysis model did not classify the market,
        # Hermes must not submit any order.
        if regime.get('decision_source') == 'none' or regime.get('primary_regime') == 'llm_unavailable':
            print(f"  ⛔ No analysis model market decision — execution blocked")
            decisions = {pair: {**d, 'action': 'neutral', 'confidence': 0} for pair, d in decisions.items()}

        # Phase 3: Positions
        print("\n📈 Phase 3: Current Positions")
        self.open_trades_count = len(positions)  # source of truth is Freqtrade
        free_balance = self.get_available_balance()
        print(f"  💰 Available {CONFIG['stake_currency']}: {free_balance:.2f}" if free_balance is not None else "  ⛔ Available balance unavailable; BUY blocked")
        open_pairs = {
            position.get('pair')
            for position in positions
            if isinstance(position, dict) and position.get('pair')
        }
        if positions:
            for p in positions:
                pnl = p.get('profit_pct', 0)
                emoji = "🟢" if pnl > 0 else "🔴"
                print(f"  {emoji} Trade #{p.get('trade_id')}: {p.get('pair')} | PnL: {pnl:.2f}%")
        else:
            print("  No open positions")

        # Phase 4: Execution
        print("\n⚡ Phase 4: Execution (v3.0 Aggressive)")
        executed_buys = 0
        executed_sells = 0

        for pair, decision in decisions.items():
            action = decision.get('action', 'neutral')
            confidence = decision.get('confidence', 0)
            strategy_id = decision.get('strategy_id', 'none')
            
            if action == 'buy':
                # Refresh live balance to avoid stale state from preceding executions
                current_free = self.get_available_balance()
                if current_free is not None:
                    free_balance = current_free
                if free_balance is None:
                    print(f"  ⏭️ BUY skipped {pair}: live balance unavailable")
                    continue
                min_stake = 15.0
                effective_balance = free_balance * 0.985
                # Regime-Aware Sizing: scale down in risky markets
                regime_mult = CONFIG.get('regime_stake_multiplier', {}).get(
                    regime.get('primary_regime', 'range_bound'), 0.60
                )
                effective_balance *= regime_mult
                if regime_mult < 1.0:
                    print(f"  📉 Regime sizing: {regime.get('primary_regime')} → {regime_mult:.0%} of balance")
                if effective_balance < min_stake:
                    print(f"  ⏭️ BUY skipped {pair}: insufficient balance after regime adjustment (${effective_balance:.2f} < ${min_stake:.2f})")
                    continue
                # Freqtrade permits only one open spot position per pair. Do
                # not turn a known duplicate into a noisy forceenter failure.
                if pair in open_pairs:
                    print(f"  ⏭️ BUY skipped {pair}: position already open")
                    continue
                if self.open_trades_count >= CONFIG['max_open_trades']:
                    print("  ⏭️ BUY skipped: maximum open trades reached")
                    break
                threshold = self.engine.get_confidence_threshold(strategy_id)
                if confidence >= threshold:
                    loss_ok, loss_reason = self.engine.loss_guard(strategy_id, pair)
                    if not loss_ok:
                        print(f"  ⏭️ BUY skipped {pair}: {loss_reason}")
                        continue
                    confirmed, confirmation_reason = self.engine.confirm_trade_signal(
                        pair, decision, market_data[pair], regime.get('primary_regime', 'unknown')
                    )
                    if not confirmed:
                        print(f"  ⏭️ BUY skipped {pair}: confirmation rejected ({confirmation_reason})")
                        continue
                    if self.check_news_disaster(pair):
                        print(f"  ⏭️ BUY skipped {pair}: NEWS DISASTER DETECTED")
                        continue
                    if self.open_trades_count < CONFIG['max_open_trades']:
                        strategy = self.engine.get_strategy(strategy_id)
                        if strategy:
                            base_pct = strategy.base_stake_pct
                            conf_mult = strategy.confidence_multiplier
                            max_total = strategy.max_total_exposure or 5000
                        else:
                            base_pct = CONFIG['max_stake_percent']
                            conf_mult = 0.5
                            max_total = 5000
                        
                        base_size = CONFIG['wallet_size'] * (base_pct / 100)
                        stake = min(base_size * (conf_mult + confidence / 100),
                                    max_total / CONFIG['max_open_trades'])
                        
                        current_exposure = sum(p.get('stake_amount', 0) for p in positions)
                        available = max_total - current_exposure - abs(self.daily_pnl)
                        stake = round(min(CONFIG['stake_amount'], stake, available, effective_balance), 2)
                        
                        if stake < min_stake:
                            continue

                        trade_id = self.execute_buy(pair, stake)
                        if trade_id:
                            executed_buys += 1
                            self.open_trades_count += 1
                            free_balance -= stake
                            open_pairs.add(pair)
                            self.trade_log.append({
                                'timestamp': datetime.now().isoformat(),
                                'pair': pair,
                                'action': 'buy',
                                'confidence': confidence,
                                'stake': stake,
                                'strategy_id': strategy_id,
                                'regime': regime.get('primary_regime', 'unknown')
                            })
                            self.save_state()
                    else:
                        print(f"  ⏸️  {pair}: Max trades ({CONFIG['max_open_trades']})")

        # Sell execution
        for pos in positions:
            pair = pos.get('pair')
            trade_id = pos.get('trade_id')
            if not pair or not trade_id:
                continue
            pair_market = market_data.get(pair)
            if not pair_market:
                continue
            
            decision = decisions.get(pair, {})
            action = decision.get('action', 'neutral')
            confidence = decision.get('confidence', 0)
            strategy_id = decision.get('strategy_id', 'none')
            
            pnl_pct = pos.get('profit_pct', 0)
            levels = self._dynamic_exit_levels(pos, pair_market, decision)
            take_profit = levels['take_profit_pct']
            stop_loss = levels['stop_loss_pct']
            age_hours = self._position_age_hours(pos)

            # Track peak PnL for breakeven lock
            tid = str(trade_id)
            self.peak_pnl_tracker[tid] = max(self.peak_pnl_tracker.get(tid, 0), pnl_pct)
            peak_pnl = self.peak_pnl_tracker[tid]

            # Staged Take Profit: partial exits at intermediate levels
            done_stages = self.staged_exits_done.get(tid, [])
            for level_idx, level in enumerate(CONFIG.get('staged_tp_levels', [])):
                if level_idx in done_stages:
                    continue
                if pnl_pct >= level['pnl_pct']:
                    current_amount = pos.get('amount', 0)
                    sell_amount = current_amount * level['sell_fraction']
                    if sell_amount > 0 and self.execute_partial_sell(trade_id, pair, sell_amount):
                        if tid not in self.staged_exits_done:
                            self.staged_exits_done[tid] = []
                        self.staged_exits_done[tid].append(level_idx)
                        self.trade_log.append({
                            'timestamp': datetime.now().isoformat(), 'pair': pair,
                            'action': 'partial_sell', 'pnl_pct': pnl_pct,
                            'level_pct': level['pnl_pct'], 'fraction': level['sell_fraction'],
                            'trade_id': trade_id,
                        })
                        self.save_state()

            should_sell = False
            sell_reason = ""
            
            if pnl_pct >= take_profit:
                should_sell = True
                sell_reason = f"TAKE PROFIT {pnl_pct:.1f}% (target {take_profit:.1f}%)"
            elif pnl_pct <= stop_loss:
                should_sell = True
                sell_reason = f"STOP LOSS {pnl_pct:.1f}% (limit {stop_loss:.1f}%)"
            elif peak_pnl >= CONFIG['breakeven_trigger_pct'] and pnl_pct <= CONFIG['breakeven_lock_pct']:
                should_sell = True
                sell_reason = f"BREAKEVEN LOCK (peak {peak_pnl:.1f}% → now {pnl_pct:.1f}%)"
            elif age_hours >= CONFIG['time_stop_hours']:
                should_sell = True
                sell_reason = f"TIME STOP {age_hours:.1f}h (limit {CONFIG['time_stop_hours']}h, pnl {pnl_pct:+.2f}%)"
            elif action == 'sell' and confidence >= 70:
                confirmed, confirmation_reason = self.engine.confirm_trade_signal(
                    pair, decision, pair_market, regime.get('primary_regime', 'unknown'), pos
                )
                if confirmed:
                    should_sell = True
                    sell_reason = f"LLM sell confirmed (conf={confidence:.0f}%)"
                else:
                    print(f"  ⏭️ SELL skipped {pair}: confirmation rejected ({confirmation_reason})")
            
            if should_sell:
                if self.execute_sell(trade_id, pair):
                    executed_sells += 1
                    self.open_trades_count -= 1
                    self.daily_pnl += (pnl_pct / 100) * pos.get('stake_amount', 0)
                    self.weekly_pnl += (pnl_pct / 100) * pos.get('stake_amount', 0)

                    pnl_dollar = (pnl_pct / 100) * pos.get('stake_amount', 0)
                    if strategy_id != 'none':
                        self.engine.record_trade_result(strategy_id, pnl_dollar, pnl_pct, 0)
                    
                    self.trade_log.append({
                        'timestamp': datetime.now().isoformat(),
                        'pair': pair,
                        'action': 'sell',
                        'confidence': confidence,
                        'pnl': pnl_dollar,
                        'pnl_pct': pnl_pct,
                        'strategy_id': strategy_id,
                        'trade_id': trade_id,
                        'sell_reason': sell_reason,
                        'regime': regime.get('primary_regime', 'unknown')
                    })
                    self.save_state()

        # Summary
        print("\n📋 Phase 5: Cycle Summary")
        print("-" * 55)
        print(f"  🌍 Regime: {regime.get('primary_regime', '?')} | Risk: {regime.get('risk_level', '?')}")
        print(f"  🟢 Buys:  {executed_buys}")
        print(f"  🔴 Sells: {executed_sells}")
        print(f"  💰 P&L:   Daily ${self.daily_pnl:+,.2f} | Weekly ${self.weekly_pnl:+,.2f}")

        strategy_map = {s.id: s.name for s in self.engine.store.strategies.values()}
        self.last_decisions = {pair: {
            'action': d.get('action'),
            'confidence': d.get('confidence'),
            'regime': regime.get('primary_regime'),
            'strategy_id': d.get('strategy_id'),
            'strategy_name': strategy_map.get(d.get('strategy_id'), 'default'),
            'reason': (d.get('reason') or '')[:100]
        } for pair, d in decisions.items()}

        self.last_update = datetime.now().isoformat()
        self.engine.store.global_stats['total_cycles'] += 1
        self.save_state()

        print("\n" + "=" * 70)
        print("✅ Cycle completed")
        return {'status': 'success', 'buys': executed_buys, 'sells': executed_sells, 'regime': regime}


if __name__ == '__main__':
    brain = HermesBrain()
    
    if '--optimize' in sys.argv:
        result = brain.run_weekly_optimization()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif '--status' in sys.argv:
        status = brain.get_status()
        print(json.dumps(status, indent=2, ensure_ascii=False, default=str))
    elif '--exit-review' in sys.argv:
        brain.run_exit_cycle()
    else:
        brain.run_cycle()
