#!/usr/bin/env python3
"""
Hermes Learning Engine v1.0 — Self-Evolving Strategy System
============================================================
DeepSeek-V4-Pro analyzes every closed trade to extract lessons,
builds new strategies from winning patterns, and retires losers.
"""

import json
import os
import sys
import shutil
from datetime import datetime
from typing import Dict, List, Optional


TRADE_ARCHIVE = "/root/.hermes/profiles/trader/freqtrade/trade_archive.json"
LESSON_DIR = "/root/.hermes/profiles/trader/freqtrade/lessons"
STRATEGY_STORE = "/root/.hermes/profiles/trader/freqtrade/strategy_store.json"
HERMES_STATE = "/root/.hermes/profiles/trader/freqtrade/hermes_state.json"


class TradeArchive:
    """Stores every closed trade with full context for learning."""

    def __init__(self, path: str = TRADE_ARCHIVE):
        self.path = path
        self.trades: List[Dict] = []
        self.load()

    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path) as f:
                    self.trades = json.load(f)
        except:
            self.trades = []

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self.trades[-500:], f, indent=2, ensure_ascii=False)

    def add(self, trade: Dict):
        """Add a trade with full context."""
        trade['archived_at'] = datetime.now().isoformat()
        self.trades.append(trade)
        self.save()

    def get_recent_closed(self, n: int = 20) -> List[Dict]:
        return self.trades[-n:] if self.trades else []

    def win_rate_by_strategy(self) -> Dict[str, Dict]:
        """Compute stats per strategy."""
        from collections import defaultdict
        by_strat = defaultdict(lambda: {'wins': 0, 'losses': 0, 'total_pnl': 0.0, 'trades': 0})
        for t in self.trades:
            sid = t.get('strategy_id', 'unknown')
            pnl = float(t.get('pnl', 0))
            by_strat[sid]['total_pnl'] += pnl
            by_strat[sid]['trades'] += 1
            if pnl > 0:
                by_strat[sid]['wins'] += 1
            else:
                by_strat[sid]['losses'] += 1
        for s in by_strat.values():
            total = s['trades']
            s['win_rate'] = (s['wins'] / total * 100) if total > 0 else 0
        return dict(by_strat)


class HermesLearningEngine:
    """Core learning engine — analyzes trades and evolves strategies."""

    def __init__(self, llm_url: str, llm_key: str, model: str):
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = model
        self.archive = TradeArchive()
        self.winning_patterns: List[Dict] = []
        self.losing_patterns: List[Dict] = []
        self.load_lessons()

    def apply_strategy_changes(self, new_plans: List[Dict], suggestions: Dict) -> Dict:
        """Apply only bounded, auditable strategy changes after daily review."""
        from hermes_strategy_engine import StrategyStore

        if not os.path.exists(STRATEGY_STORE):
            return {'created': [], 'deactivated': [], 'tuned': [], 'error': 'store_missing'}
        backup = f"{STRATEGY_STORE}.backup-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(STRATEGY_STORE, backup)
        store = StrategyStore(STRATEGY_STORE)
        result = {'created': [], 'deactivated': [], 'tuned': [], 'backup': backup}

        # Never delete history. Retire only strategies with enough evidence.
        for sid in suggestions.get('deactivate', []):
            strategy = store.strategies.get(sid)
            if strategy and strategy.active and strategy.total_trades >= 5:
                strategy.active = False
                result['deactivated'].append(sid)
                store.evolution_log.append({
                    'timestamp': datetime.now().isoformat(),
                    'action': 'deactivate', 'strategy_id': sid,
                    'reason': suggestions.get('reason_arabic', 'daily risk review'),
                })

        allowed = {
            'min_confidence', 'take_profit_pct', 'stop_loss_pct',
            'trailing_stop_pct', 'require_btc_aligned', 'btc_min_change',
        }
        for sid, changes in (suggestions.get('tune') or {}).items():
            strategy = store.strategies.get(sid)
            if not strategy or not strategy.active or not isinstance(changes, dict):
                continue
            for key, value in changes.items():
                if key not in allowed or not isinstance(value, (bool, int, float)):
                    continue
                if key == 'min_confidence' and not 60 <= float(value) <= 95:
                    continue
                if key in {'take_profit_pct', 'trailing_stop_pct'} and not 1 <= float(value) <= 20:
                    continue
                if key == 'stop_loss_pct' and not -15 <= float(value) <= -0.5:
                    continue
                setattr(strategy, key, value)
                result['tuned'].append(f'{sid}.{key}')

        for plan in new_plans:
            if not isinstance(plan, dict) or not plan.get('name'):
                continue
            parent_ids = plan.get('evolved_from') or []
            parent = next((store.strategies.get(x) for x in parent_ids if x in store.strategies), None)
            if not parent:
                continue
            mutations = {k: plan[k] for k in allowed | {'name', 'description', 'regime_affinity'} if k in plan}
            child = store.evolve_strategy(parent.id, mutations)
            if child:
                result['created'].append(child.id)
        store.save()
        return result

    def load_lessons(self):
        os.makedirs(LESSON_DIR, exist_ok=True)
        try:
            path = os.path.join(LESSON_DIR, 'lessons.json')
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                    self.winning_patterns = data.get('winning_patterns', [])
                    self.losing_patterns = data.get('losing_patterns', [])
        except:
            pass

    def _lesson_key(self, trade: Dict) -> tuple:
        return (
            str(trade.get('trade_id', '')),
            trade.get('pair'),
            trade.get('timestamp') or trade.get('closed_at'),
            trade.get('pnl_pct'),
        )

    def sync_controller_trades(self) -> int:
        """Import closed trades persisted by the live controller."""
        try:
            with open(HERMES_STATE, encoding='utf-8') as handle:
                state = json.load(handle)
        except (OSError, ValueError, TypeError):
            return 0
        existing = {
            (str(t.get('trade_id', '')), t.get('pair'), t.get('timestamp'), t.get('pnl_pct'))
            for t in self.archive.trades
        }
        imported = 0
        for trade in state.get('trade_log', []):
            if trade.get('action') != 'sell':
                continue
            key = (str(trade.get('trade_id', '')), trade.get('pair'),
                   trade.get('timestamp'), trade.get('pnl_pct'))
            if key in existing:
                continue
            self.archive.add({
                'pair': trade.get('pair'),
                'pnl': float(trade.get('pnl', 0) or 0),
                'pnl_pct': float(trade.get('pnl_pct', 0) or 0),
                'strategy_id': trade.get('strategy_id', 'unknown'),
                'sell_reason': trade.get('sell_reason', 'unknown'),
                'context': {'regime': trade.get('regime', 'unknown')},
                'closed_at': trade.get('timestamp') or datetime.now().isoformat(),
                'source': 'hermes_state.json',
                'trade_id': trade.get('trade_id'),
            })
            existing.add(key)
            imported += 1
        return imported

    def loss_guard(self, strategy_id: str, pair: str) -> tuple[bool, str]:
        """Block repeating a recently observed losing pattern."""
        recent = [t for t in self.archive.trades[-20:]
                  if t.get('strategy_id') == strategy_id and t.get('pair') == pair]
        losses = [t for t in recent if float(t.get('pnl', 0) or 0) < 0]
        if len(losses) >= 2:
            return False, f"repeated loss pattern: {strategy_id} on {pair}"
        return True, "loss guard passed"

    def save_lessons(self):
        data = {
            'winning_patterns': self.winning_patterns[-50:],
            'losing_patterns': self.losing_patterns[-50:],
            'last_learning_cycle': datetime.now().isoformat(),
            'total_lessons': len(self.winning_patterns) + len(self.losing_patterns)
        }
        with open(os.path.join(LESSON_DIR, 'lessons.json'), 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def record_trade_result(self, trade_data: Dict, strategy_id: str):
        """Called when a trade closes — saves to archive for learning."""
        self.archive.add({
            **trade_data,
            'strategy_id': strategy_id,
            'closed_at': datetime.now().isoformat()
        })
        print(f"  📦 Trade archived: {trade_data.get('pair')} | PnL: ${trade_data.get('pnl', 0):+.2f}")

    def extract_lesson(self, trade: Dict) -> Optional[Dict]:
        """Use LLM to analyze WHY this trade won or lost."""
        import requests

        pnl = float(trade.get('pnl', 0))
        pnl_pct = float(trade.get('pnl_pct', 0))
        hold_hours = float(trade.get('hold_hours', 0))
        direction = "WIN" if pnl > 0 else "LOSS"

        ctx = trade.get('context', {})
        prompt = f"""You are Hermes Learning Analyst. Analyze this CLOSED trade and extract the LEARNING.

TRADE DATA:
- Pair: {trade.get('pair')}
- Direction: LONG
- Entry: ${trade.get('entry_price', 0):.4f}
- Exit: ${trade.get('exit_price', 0):.4f}
- P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)
- Hold Time: {hold_hours:.1f} hours
- Strategy Used: {trade.get('strategy_id', 'unknown')}
- Market Regime: {ctx.get('regime', '?')}
- BTC Change at Entry: {ctx.get('btc_change', '?')}%

REASON GIVEN BY BRAIN: {trade.get('reason', 'N/A')}

Output ONLY valid JSON:
{{
  "direction": "{direction}",
  "learning_category": "<pattern_type>",
  "key_factors": ["<what contributed>", ...],
  "mistake_if_any": "<what went wrong>",
  "improvement_suggestion": "<what to do differently>",
  "trust_worthy": true|false,
  "replicable": true|false,
  "summary_arabic": "<2-line Arabic summary>"
}}"""

        try:
            res = requests.post(
                f"{self.llm_url.rstrip('/')}/chat/completions",
                json={
                    'model': self.model,
                    'messages': [
                        {'role': 'system', 'content': 'You are Hermes Learning Analyst. Output JSON only.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'temperature': 0.2,
                    'max_tokens': 400
                },
                headers={'Authorization': f'Bearer {self.llm_key}', 'Content-Type': 'application/json'},
                timeout=600
            )
            if res.status_code == 200:
                raw = res.json()['choices'][0]['message']['content'].strip()
                if '```json' in raw:
                    raw = raw.split('```json')[1].split('```')[0].strip()
                elif '```' in raw:
                    raw = raw.split('```')[1].split('```')[0].strip()
                analysis = json.loads(raw)
                analysis['trade'] = trade

                if direction == "WIN":
                    self.winning_patterns.append(analysis)
                else:
                    self.losing_patterns.append(analysis)

                print(f"  🧠 Lesson: {direction} | {trade.get('pair')} | Cat: {analysis.get('learning_category', '?')}")
                return analysis
        except Exception as e:
            print(f"  ⚠️ Lesson extraction failed: {e}")

        return None

    def build_new_strategy_from_winners(self) -> Optional[Dict]:
        """Use LLM to create a new strategy from winning patterns."""
        import requests

        recent_wins = self.winning_patterns[-10:]
        if not recent_wins:
            return None

        win_categories = {}
        for w in recent_wins:
            cat = w.get('learning_category', 'unknown')
            win_categories[cat] = win_categories.get(cat, 0) + 1

        prompt = f"""Analyze winning trade patterns and CREATE a NEW trading strategy.

Recent WINNING patterns found:
{json.dumps(win_categories, ensure_ascii=False)}

Last {len(recent_wins)} wins had these factors:
"""
        for w in recent_wins[:5]:
            summary = w.get('summary_arabic', '')
            key = ', '.join(w.get('key_factors', [])[:3])
            prompt += f"  • {summary} | Factors: {key}\n"

        prompt += """Create a NEW trading strategy based on these patterns.
Output ONLY valid JSON:
{
  "name": "English strategy name",
  "description": "English description of why this works",
  "min_confidence": <int 60-90>,
  "take_profit_pct": <float>,
  "stop_loss_pct": <float>,
  "trailing_stop_pct": <float>,
  "base_stake_pct": <float>,
  "max_stake_pct": <float>,
  "require_btc_aligned": true|false,
  "regime_affinity": {"trending_up": <0-1>, "range_bound": <0-1>},
  "evolved_from": ["<strategy_id>"],
  "reason": "English explanation of why this works"
}"""

        try:
            res = requests.post(
                f"{self.llm_url.rstrip('/')}/chat/completions",
                json={
                    'model': self.model,
                    'messages': [
                        {'role': 'system', 'content': 'You are Hermes Strategy Evolution Engine. Output JSON only. Use English for strategy names, descriptions, and reasons.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'temperature': 0.3,
                    'max_tokens': 500
                },
                headers={'Authorization': f'Bearer {self.llm_key}', 'Content-Type': 'application/json'},
                timeout=600
            )
            if res.status_code == 200:
                raw = res.json()['choices'][0]['message']['content'].strip()
                if '```json' in raw:
                    raw = raw.split('```json')[1].split('```')[0].strip()
                return json.loads(raw)
        except Exception as e:
            print(f"  ⚠️ Strategy evolution failed: {e}")

        return None

    def suggest_deactivations(self) -> Dict:
        """Identify strategies to potentially remove."""
        import requests

        by_strat = self.archive.win_rate_by_strategy()
        failing = {k: v for k, v in by_strat.items() if v['win_rate'] < 40 and v['trades'] >= 3}

        if not failing:
            return {"deactivate": [], "tune": {}, "reason_arabic": ""}

        prompt = f"Failing strategies (WR < 40%, min 3 trades):\n"
        for sid, stats in failing.items():
            prompt += f"  • {sid}: WR={stats['win_rate']:.0f}% | Trades={stats['trades']} | PnL=${stats['total_pnl']:+.2f}\n"

        prompt += """Should these be deactivated or tuned?
Output JSON: {"deactivate": ["ids"], "tune": {"id": {...}}, "reason_arabic": "..."}"""

        try:
            res = requests.post(
                f"{self.llm_url.rstrip('/')}/chat/completions",
                json={
                    'model': self.model,
                    'messages': [
                        {'role': 'system', 'content': 'You are Hermes Risk Analyst. Output JSON only.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'temperature': 0.2,
                    'max_tokens': 400
                },
                headers={'Authorization': f'Bearer {self.llm_key}', 'Content-Type': 'application/json'},
                timeout=600
            )
            if res.status_code == 200:
                raw = res.json()['choices'][0]['message']['content'].strip()
                if '```json' in raw:
                    raw = raw.split('```json')[1].split('```')[0].strip()
                return json.loads(raw)
        except:
            pass
        return {"deactivate": [], "tune": {}, "reason_arabic": ""}

    def run_learning_cycle(self) -> Dict:
        """Full learning cycle."""
        imported = self.sync_controller_trades()
        print("\n" + "=" * 70)
        print("🧠 HERMES LEARNING ENGINE")
        print("=" * 70)

        results = {
            'lessons_extracted': 0,
            'new_strategies': [],
            'deactivations': [],
            'tuning_actions': [],
            'errors': [],
            'trades_imported': imported,
            'trades_available': len(self.archive.trades),
        }

        # Step 1: Extract lessons from recent closed trades
        closed_trades = self.archive.get_recent_closed(n=10)
        if closed_trades:
            print(f"\n📊 Analyzing {len(closed_trades)} recent trades...")
            analyzed = {
                self._lesson_key(item.get('trade', {}))
                for item in self.winning_patterns + self.losing_patterns
                if isinstance(item, dict)
            }
            for trade in reversed(closed_trades):
                if self._lesson_key(trade) in analyzed:
                    continue
                lesson = self.extract_lesson(trade)
                if lesson:
                    results['lessons_extracted'] += 1
        else:
            print("\n⏸️ No closed trades to analyze.")
            return results

        # Step 2: Build new strategies from winners
        if self.winning_patterns and len(self.winning_patterns) >= 3:
            print(f"\n🧬 Evolving from {len(self.winning_patterns)} winning patterns...")
            new_plan = self.build_new_strategy_from_winners()
            if new_plan:
                results['new_strategies'].append(new_plan)

        # Step 3: Suggest deactivations
        if len(self.archive.trades) >= 10:
            print("\n🗑️ Evaluating strategies...")
            suggestions = self.suggest_deactivations()
            results['deactivations'] = suggestions.get('deactivate', [])
            results['tuning_actions'] = suggestions.get('tune', {})

        applied = self.apply_strategy_changes(results['new_strategies'], suggestions if len(self.archive.trades) >= 10 else {})
        results['applied_changes'] = applied

        self.save_lessons()
        results['total_lessons'] = len(self.winning_patterns) + len(self.losing_patterns)
        return results


if __name__ == '__main__':
    engine = HermesLearningEngine(
        llm_url=os.environ.get('HCNSEC_BASE_URL', 'https://api.hcnsec.cn/v1'),
        llm_key='',
        model='DeepSeek-V4-Pro'
    )

    if '--analyze' in sys.argv:
        result = engine.run_learning_cycle()
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"📦 Archive: {len(engine.archive.trades)} trades")
        print(f"✅ Winning: {len(engine.winning_patterns)}")
        print(f"❌ Losing: {len(engine.losing_patterns)}")
        by_strat = engine.archive.win_rate_by_strategy()
        for sid, stats in by_strat.items():
            print(f"  {sid}: {stats['win_rate']:.0f}% WR | {stats['trades']} trades")
