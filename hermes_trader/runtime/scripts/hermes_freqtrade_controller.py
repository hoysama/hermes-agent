#!/usr/bin/env python3
"""Hermes AI Freqtrade Controller v3.0 — AGGRESSIVE MODE
Stake: 30% per trade | TP: 10% | SL: 3%
Provider: nararouter (deepseek-v4-flash-free analysis + agnes-2.5-flash decisions)
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
    'max_open_trades': 14,
    'stake_amount': 50,
    # Positions are evaluated continuously by each cycle. A long-held
    # position that is not profitable is exited rather than held forever.
    'time_stop_hours': 24,
    'max_daily_loss': 50,
    'max_weekly_loss': 150,
    'trading_pairs': [
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'LINK/USDT', 'AVAX/USDT',
        'BNB/USDT', 'XRP/USDT', 'ADA/USDT', 'DOT/USDT',
        'UNI/USDT', 'ATOM/USDT', 'LTC/USDT', 'XLM/USDT', 'ALGO/USDT'
    ]
}

FREQTRADE_API = {'url': 'http://127.0.0.1:8080', 'username': 'hermes', 'password': 'hermes123'}
STATE_DIR = '/root/.hermes/profiles/trader/freqtrade'
STATE_FILE = os.path.join(STATE_DIR, 'hermes_state.json')

# NaraRouter only. DeepSeek analyzes first; Agnes produces pair decisions.
NARAROUTER_BASE_URL = os.environ.get('NARAROUTER_BASE_URL', 'https://router.bynara.id/v1')
NARAROUTER_API_KEY = os.environ.get('NARAROUTER_API_KEY', '')
ANALYSIS_MODEL = 'deepseek-v4-flash-free'
DECISION_MODEL = 'agnes-2.5-flash'

_engine = None

def get_engine() -> StrategyEngine:
    global _engine
    if _engine is None:
        _engine = StrategyEngine(
            llm_url=NARAROUTER_BASE_URL,
            llm_key=NARAROUTER_API_KEY,
            model=DECISION_MODEL,
            analysis_model=ANALYSIS_MODEL,
            decision_model=DECISION_MODEL,
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
                'last_update': datetime.now().isoformat()
                ,'daily_rotations': self.daily_rotations[-20:]
            }
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as exc:
            print(f"Warning saving state: {exc}")

    def fetch_market_data(self) -> Dict:
        try:
            import ccxt
            # Request only the configured symbols. Fetching every OKX ticker
            # can block long enough to consume the entire Cron cycle.
            exchange = ccxt.okx({
                'enableRateLimit': True,
                'timeout': 15000,
            })
            symbols = list(CONFIG['trading_pairs'])
            print(f"  📡 Fetching live OKX tickers ({len(symbols)} pairs)...", flush=True)
            tickers = exchange.fetch_tickers(symbols)
            print(f"  ✅ Received {len(tickers)} OKX tickers", flush=True)
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
                    }
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
    def execute_buy(self, pair: str, stake: float) -> Optional[int]:
        try:
            auth = (FREQTRADE_API['username'], FREQTRADE_API['password'])
            payload = {'pair': pair, 'stake_amount': stake}
            res = requests.post(f"{FREQTRADE_API['url']}/api/v1/forceenter", auth=auth, json=payload, timeout=10)
            if res.status_code == 200:
                result = res.json()
                trade_id = result.get('trade_id')
                print(f"  🟢 BUY {pair}: ${stake:.0f}")
                return trade_id
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
                return True
            else:
                print(f"  ❌ SELL failed #{trade_id}: {res.text[:100]}")
            return False
        except Exception as e:
            print(f"  ❌ Error selling trade #{trade_id}: {e}")
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

        market_data = self.fetch_market_data()
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
            decision = decisions.get(pair, {})
            action = decision.get('action', 'neutral')
            confidence = decision.get('confidence', 0)
            pnl_pct = position.get('profit_pct', 0)
            levels = self._dynamic_exit_levels(position, market_data[pair], decision)
            take_profit = levels['take_profit_pct']
            stop_loss = levels['stop_loss_pct']
            age_hours = self._position_age_hours(position)
            should_sell = False
            reason = ''
            if pnl_pct >= take_profit:
                should_sell, reason = True, f"TAKE PROFIT {pnl_pct:.1f}% (target {take_profit:.1f}%)"
            elif pnl_pct <= stop_loss:
                should_sell, reason = True, f"STOP LOSS {pnl_pct:.1f}% (limit {stop_loss:.1f}%)"
            elif age_hours >= CONFIG['time_stop_hours'] and pnl_pct <= 0:
                should_sell, reason = True, f"TIME STOP {age_hours:.1f}h at {pnl_pct:.1f}%"
            elif action == 'sell' and confidence >= 70:
                confirmed, confirm_reason = self.engine.confirm_trade_signal(
                    pair, decision, market_data[pair], regime.get('primary_regime', 'range_bound'), position
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

    def _rotation_candidate(self, positions, decisions, market_data):
        today = datetime.now(timezone.utc).date().isoformat()
        self.daily_rotations = [r for r in self.daily_rotations if r.get('date') == today]
        if len(self.daily_rotations) >= 2:
            return None
        free = self.get_available_balance()
        has_open_slot = len(positions) < CONFIG['max_open_trades']
        has_enough_free_balance = free is not None and free >= max(20, CONFIG['stake_amount'])
        if has_open_slot and has_enough_free_balance:
            return None
        return self.engine.evaluate_asset_rotation(positions, decisions, market_data)

    def _execute_rotation(self, rotation, positions):
        from_pair = rotation['from_pair']; to_pair = rotation['to_pair']
        trade_id = rotation.get('from_trade_id'); stake = rotation.get('stake', 0)
        if not trade_id or stake < 20 or from_pair == to_pair:
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
        stake = min(stake, balance)
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
        market_data = self.fetch_market_data()
        if 'error' in market_data:
            print("  ⛔ Market data unavailable; execution blocked", flush=True)
            return {'status': 'market_data_unavailable', 'buys': 0, 'sells': 0}

        # Read positions before LLM analysis so each open position receives
        # explicit HOLD/SELL context rather than being treated as an entry.
        positions = self.get_open_positions()

        # Phase 2: Strategy Selection
        print("\n🧬 Phase 2: Dynamic Strategy Selection")
        engine_result = self.engine.analyze_market_and_select(self.market_data, positions)
        regime = engine_result['regime']
        decisions = engine_result['decisions']
        
        # Freqtrade is execution-only: if the analysis model did not classify the market,
        # Hermes must not submit any order.
        if regime.get('decision_source') == 'none' or regime.get('primary_regime') == 'llm_unavailable':
            print(f"  ⛔ No {ANALYSIS_MODEL} market decision — execution blocked")
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
                if free_balance is None:
                    print(f"  ⏭️ BUY skipped {pair}: live balance unavailable")
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
                        stake = min(CONFIG['stake_amount'], stake, available, free_balance)
                        
                        if stake < 20:
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
            
            decision = decisions.get(pair, {})
            action = decision.get('action', 'neutral')
            confidence = decision.get('confidence', 0)
            strategy_id = decision.get('strategy_id', 'none')
            
            pnl_pct = pos.get('profit_pct', 0)
            levels = self._dynamic_exit_levels(pos, market_data[pair], decision)
            take_profit = levels['take_profit_pct']
            stop_loss = levels['stop_loss_pct']
            opened_at = pos.get('open_date') or pos.get('open_date_utc')
            age_hours = 0.0
            if opened_at:
                try:
                    opened = datetime.fromisoformat(str(opened_at).replace('Z', '+00:00'))
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    age_hours = max(0.0, (datetime.now(timezone.utc) - opened).total_seconds() / 3600)
                except (TypeError, ValueError):
                    age_hours = 0.0
            should_sell = False
            sell_reason = ""
            
            if action == 'sell' and confidence >= 70:
                confirmed, confirmation_reason = self.engine.confirm_trade_signal(
                    pair, decision, market_data[pair], regime.get('primary_regime', 'unknown'), pos
                )
                if confirmed:
                    should_sell = True
                    sell_reason = f"LLM sell confirmed (conf={confidence:.0f}%)"
                else:
                    print(f"  ⏭️ SELL skipped {pair}: confirmation rejected ({confirmation_reason})")
            elif pnl_pct >= take_profit:
                should_sell = True
                sell_reason = f"TAKE PROFIT {pnl_pct:.1f}% (target {take_profit:.1f}%)"
            elif pnl_pct <= stop_loss:
                should_sell = True
                sell_reason = f"STOP LOSS {pnl_pct:.1f}% (limit {stop_loss:.1f}%)"
            elif age_hours >= CONFIG['time_stop_hours'] and pnl_pct <= 0:
                should_sell = True
                sell_reason = f"TIME STOP {age_hours:.1f}h at {pnl_pct:.1f}%"
            
            if should_sell:
                if self.execute_sell(trade_id, pair):
                    executed_sells += 1
                    self.open_trades_count -= 1
                    self.daily_pnl += (pnl_pct / 100) * pos.get('stake_amount', 0)
                    self.weekly_pnl += (pnl_pct / 100) * pos.get('stake_amount', 0)

                    # Always derive the realized PnL before recording it.
                    # Positions without a strategy id can still be closed by
                    # a stop/time rule and must not abort the cycle/report.
                    pnl_dollar = (pnl_pct / 100) * pos.get('stake_amount', 0)
                    if strategy_id != 'none':
                        self.engine.record_trade_result(strategy_id, pnl_dollar, pnl_pct, 0)
                    
                    self.trade_log.append({
                        'timestamp': datetime.now().isoformat(),
                        'pair': pair,
                        'action': 'sell',
                        'confidence': confidence,
                        'pnl': (pnl_pct / 100) * pos.get('stake_amount', 0),
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
