#!/usr/bin/env python3
"""
Hermes Strategy Engine v3.0 — Aggressive Trading Mode
======================================================
Updated parameters for 10% profit target:
- Base stake: 30% of wallet
- Take Profit: 10%
- Stop Loss: 3%
- Max total exposure: $5,000
"""

import json
import os
import hashlib
import re
import time
import requests
from datetime import datetime
from datetime import timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict


def parse_llm_json(raw: str) -> Dict:
    """Parse JSON-only model output, tolerating markdown/prose wrappers."""
    text = (raw or '').strip()
    if '```' in text:
        parts = text.split('```')
        text = parts[1] if len(parts) > 1 else text
        text = text.replace('json', '', 1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def extract_completion(response: requests.Response) -> Tuple[str, str, int]:
    """Return response content and metadata without assuming a valid choice."""
    body = response.json()
    choices = body.get('choices') or []
    if not choices:
        raise ValueError(f"LLM returned no choices (body keys: {sorted(body)})")
    choice = choices[0] or {}
    content = (choice.get('message') or {}).get('content')
    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            f"LLM returned empty content (finish_reason={choice.get('finish_reason')!r})"
        )
    return content, str(choice.get('finish_reason') or ''), len(content)


def valid_regime(value: Dict) -> bool:
    return (
        isinstance(value, dict)
        and value.get('primary_regime') in {
            'trending_up', 'trending_down', 'range_bound', 'breakout',
            'accumulation', 'recovery', 'high_volatility', 'crash',
        }
        and value.get('risk_level') in {'low', 'medium', 'high', 'extreme'}
        and isinstance(value.get('confidence'), int)
        and 0 <= value['confidence'] <= 100
        and isinstance(value.get('reason_arabic'), str)
    )


def valid_pair_decision(value: Dict) -> bool:
    return (
        isinstance(value, dict)
        and value.get('action') in {'buy', 'sell', 'neutral'}
        and isinstance(value.get('confidence'), int)
        and 0 <= value['confidence'] <= 100
        and isinstance(value.get('strategy_id'), str)
        and isinstance(value.get('reason'), str)
    )


def llm_diagnostics(response: requests.Response, *, stage: str, attempt: int,
                    finish_reason: str = '', content_length: int = 0,
                    error: Exception | None = None, raw: str = '') -> None:
    """Emit safe diagnostics for provider failures without logging secrets."""
    request_id = (
        response.headers.get('x-request-id')
        or response.headers.get('request-id')
        or response.headers.get('x-openai-request-id')
        or '-'
    )
    preview = raw[:160].replace('\n', ' ') if raw else ''
    print(
        f"  ❌ LLM {stage} failed: http_status={response.status_code} "
        f"finish_reason={finish_reason or '-'} content_length={content_length} "
        f"request_id={request_id} retry={attempt}/1 error={error!s} raw_prefix={preview!r}"
    )


# ═══════════════════════════════════════════════════════════════
# STRATEGY DNA
# ═══════════════════════════════════════════════════════════════

@dataclass
class StrategyDNA:
    """A single trading strategy with its rules and performance history."""
    id: str
    name: str
    description: str
    version: int = 1
    parent_id: Optional[str] = None
    
    # Regime affinity
    regime_affinity: Dict[str, float] = field(default_factory=lambda: {
        'trending_up': 0.8,
        'trending_down': 0.6,
        'high_volatility': 0.5,
        'range_bound': 0.7,
        'accumulation': 0.7,
        'distribution': 0.3,
        'breakout': 0.9,
        'crash': 0.1,
        'recovery': 0.8
    })
    
    # Entry rules
    min_confidence: float = 65.0
    require_btc_aligned: bool = False
    btc_min_change: float = 0.0
    min_volume_rank: int = 5
    max_rsi: float = 75.0
    min_rsi: float = 25.0
    require_support_near: bool = False
    require_breakout: bool = False
    
    # Exit rules
    take_profit_pct: float = 4.0            # ← SCALPING: 4% TP
    stop_loss_pct: float = -2.0             # ← TIGHT SL: 2%
    trailing_stop_pct: float = 2.0
    time_stop_hours: int = 3
    min_hold_hours: int = 1
    
    # Position sizing
    base_stake_pct: float = 30.0            # ← AGGRESSIVE: 30%
    max_stake_pct: float = 50.0
    confidence_multiplier: float = 0.5
    max_total_exposure: float = 5000.0      # Hard cap $5,000
    
    # Status
    active: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_used_at: Optional[str] = None
    
    # Performance
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_hold_hours: float = 0.0
    performance_score: float = 0.0
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'StrategyDNA':
        return cls(**d)
    
    def update_performance(self, pnl: float, pnl_pct: float, hold_hours: float):
        self.total_trades += 1
        self.total_pnl += pnl
        self.total_pnl_pct += pnl_pct
        
        if pnl > 0:
            self.winning_trades += 1
            self.avg_win = ((self.avg_win * (self.winning_trades - 1)) + pnl) / self.winning_trades
            self.largest_win = max(self.largest_win, pnl)
        else:
            self.losing_trades += 1
            self.avg_loss = ((self.avg_loss * (self.losing_trades - 1)) + pnl) / self.losing_trades
            self.largest_loss = min(self.largest_loss, pnl)
        
        self.win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        gross_profit = self.winning_trades * self.avg_win if self.winning_trades > 0 else 0.01
        gross_loss = abs(self.losing_trades * self.avg_loss) if self.losing_trades > 0 else 0.01
        self.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        self.avg_hold_hours = ((self.avg_hold_hours * (self.total_trades - 1)) + hold_hours) / self.total_trades
        
        wr_score = min(self.win_rate, 100) * 0.4
        pf_score = min(self.profit_factor * 20, 100) * 0.3
        pnl_score = min(max(self.total_pnl_pct + 10, 0) * 5, 100) * 0.3
        self.performance_score = wr_score + pf_score + pnl_score
    
    def get_affinity(self, regime: str) -> float:
        return self.regime_affinity.get(regime, 0.3)


# ═══════════════════════════════════════════════════════════════
# STRATEGY STORE
# ═══════════════════════════════════════════════════════════════

class StrategyStore:
    def __init__(self, store_path: str = None):
        self.store_path = store_path or '/root/.hermes/profiles/trader/freqtrade/strategy_store.json'
        self.strategies: Dict[str, StrategyDNA] = {}
        self.evolution_log: List[Dict] = []
        self.global_stats: Dict = {
            'total_trades': 0,
            'total_cycles': 0,
            'total_pnl': 0.0,
            'best_strategy_id': None,
            'best_strategy_score': 0.0,
            'last_optimization': None,
            'optimization_count': 0
        }
        self.load()
        if not self.strategies or any(s.min_confidence > 70 for s in self.strategies.values()):
            self._seed_default_strategies()
    
    def load(self):
        try:
            if os.path.exists(self.store_path):
                with open(self.store_path, 'r') as f:
                    data = json.load(f)
                    self.strategies = {k: StrategyDNA.from_dict(v) for k, v in data.get('strategies', {}).items()}
                    self.evolution_log = data.get('evolution_log', [])
                    self.global_stats = data.get('global_stats', self.global_stats)
        except Exception as e:
            print(f"⚠️ Strategy store load failed: {e}, starting fresh")
    
    def save(self):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        data = {
            'strategies': {k: v.to_dict() for k, v in self.strategies.items()},
            'evolution_log': self.evolution_log[-50:],
            'global_stats': self.global_stats
        }
        with open(self.store_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    
    def _seed_default_strategies(self):
        """Create seed strategies with Adaptive Scalping parameters."""
        seeds = [
            StrategyDNA(
                id='trend_follower_v1',
                name='Trend Follower',
                description='Buys in a rising trend with momentum',
                regime_affinity={'trending_up': 0.95, 'breakout': 0.8, 'recovery': 0.7, 'trending_down': 0.6},
                min_confidence=65,
                require_btc_aligned=False,
                btc_min_change=0.0,
                min_volume_rank=3,
                take_profit_pct=4.0,
                stop_loss_pct=-2.0,
                trailing_stop_pct=2.0,
                base_stake_pct=30.0
            ),
            StrategyDNA(
                id='dip_buyer_v1',
                name='Dip Buyer',
                description='Buys pullbacks and oversold bounces',
                regime_affinity={'trending_up': 0.7, 'range_bound': 0.9, 'accumulation': 0.95, 'trending_down': 0.85},
                min_confidence=60,
                require_btc_aligned=False,
                btc_min_change=-0.5,
                min_volume_rank=5,
                max_rsi=50,
                min_rsi=20,
                take_profit_pct=4.0,
                stop_loss_pct=-2.0,
                trailing_stop_pct=2.0,
                base_stake_pct=30.0,
                time_stop_hours=24
            ),
            StrategyDNA(
                id='breakout_hunter_v1',
                name='Breakout Hunter',
                description='Buys confirmed breaks above resistance levels',
                regime_affinity={'breakout': 0.95, 'high_volatility': 0.8, 'trending_up': 0.7, 'trending_down': 0.5},
                min_confidence=70,
                require_btc_aligned=False,
                btc_min_change=0.0,
                min_volume_rank=2,
                require_breakout=True,
                take_profit_pct=5.0,
                stop_loss_pct=-2.0,
                trailing_stop_pct=2.0,
                base_stake_pct=30.0,
                max_stake_pct=50.0
            ),
            StrategyDNA(
                id='range_scalper_v1',
                name='Range Scalper',
                description='Buys near range support and exits near range resistance',
                regime_affinity={'range_bound': 0.95, 'accumulation': 0.7, 'trending_down': 0.8},
                min_confidence=60,
                require_btc_aligned=False,
                btc_min_change=-2.0,
                min_volume_rank=5,
                require_support_near=True,
                take_profit_pct=3.5,
                stop_loss_pct=-2.0,
                trailing_stop_pct=1.5,
                base_stake_pct=30.0,
                time_stop_hours=24,
                min_hold_hours=1
            ),
            StrategyDNA(
                id='volume_surge_v1',
                name='Volume Surge',
                description='Trades confirmed sudden increases in market volume',
                regime_affinity={'high_volatility': 0.8, 'breakout': 0.7, 'recovery': 0.8, 'trending_down': 0.7},
                min_confidence=65,
                require_btc_aligned=False,
                btc_min_change=-1.0,
                min_volume_rank=2,
                take_profit_pct=4.0,
                stop_loss_pct=-2.0,
                trailing_stop_pct=2.0,
                base_stake_pct=30.0
            ),
            StrategyDNA(
                id='safe_haven_v1',
                name='Safe Haven',
                description='Defensive strategy requiring high confidence',
                regime_affinity={'trending_up': 0.6, 'accumulation': 0.8, 'recovery': 0.7, 'trending_down': 0.6},
                min_confidence=70,
                require_btc_aligned=False,
                btc_min_change=0.5,
                min_volume_rank=2,
                max_rsi=65,
                min_rsi=35,
                take_profit_pct=4.0,
                stop_loss_pct=-2.0,
                trailing_stop_pct=2.0,
                base_stake_pct=30.0,
                max_stake_pct=50.0
            ),
        ]
        
        for s in seeds:
            self.strategies[s.id] = s
        self.save()
        print(f"🌱 Seeded {len(seeds)} adaptive scalping strategies (30% stake, 4% TP, 2% SL)")
    
    def get_best_for_regime(self, regime: str, top_n: int = 3) -> List[StrategyDNA]:
        active = [s for s in self.strategies.values() if s.active]
        scored = []
        for s in active:
            affinity = s.get_affinity(regime)
            if affinity <= 0:
                continue
            if s.total_trades >= 10:
                score = affinity * s.performance_score
            elif s.total_trades >= 1:
                score = affinity * 35
            else:
                score = affinity * 30
            scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:top_n]]
    
    def record_trade(self, strategy_id: str, pnl: float, pnl_pct: float, hold_hours: float):
        if strategy_id in self.strategies:
            self.strategies[strategy_id].update_performance(pnl, pnl_pct, hold_hours)
            self.global_stats['total_trades'] += 1
            self.global_stats['total_pnl'] += pnl
            best = max(self.strategies.values(), key=lambda s: s.performance_score)
            self.global_stats['best_strategy_id'] = best.id
            self.global_stats['best_strategy_score'] = best.performance_score
            self.save()
    
    def evolve_strategy(self, parent_id: str, mutations: Dict) -> Optional[StrategyDNA]:
        if parent_id not in self.strategies:
            return None
        parent = self.strategies[parent_id]
        child_dict = parent.to_dict()
        child_dict.update(mutations)
        child_dict['id'] = f"{parent.id.split('_v')[0]}_v{parent.version + 1}_{hashlib.md5(str(mutations).encode()).hexdigest()[:6]}"
        child_dict['version'] = parent.version + 1
        child_dict['parent_id'] = parent_id
        child_dict['total_trades'] = 0
        child_dict['performance_score'] = 0.0
        child_dict['created_at'] = datetime.now().isoformat()
        child = StrategyDNA.from_dict(child_dict)
        self.strategies[child.id] = child
        self.evolution_log.append({
            'timestamp': datetime.now().isoformat(),
            'action': 'evolve',
            'parent_id': parent_id,
            'child_id': child.id,
            'mutations': mutations,
            'parent_score': parent.performance_score
        })
        self.save()
        return child
    
    def deactivate_strategy(self, strategy_id: str, reason: str):
        if strategy_id in self.strategies:
            self.strategies[strategy_id].active = False
            self.evolution_log.append({
                'timestamp': datetime.now().isoformat(),
                'action': 'deactivate',
                'strategy_id': strategy_id,
                'reason': reason
            })
            self.save()
    
    def get_summary(self) -> Dict:
        active = [s for s in self.strategies.values() if s.active]
        return {
            'total_strategies': len(self.strategies),
            'active_strategies': len(active),
            'total_trades': self.global_stats['total_trades'],
            'total_pnl': self.global_stats['total_pnl'],
            'best_strategy': self.global_stats.get('best_strategy_id'),
            'best_score': self.global_stats.get('best_strategy_score', 0),
            'strategies': [
                {
                    'id': s.id,
                    'name': s.name,
                    'active': s.active,
                    'win_rate': s.win_rate,
                    'profit_factor': s.profit_factor,
                    'total_trades': s.total_trades,
                    'performance_score': s.performance_score,
                    'confidence': s.min_confidence,
                    'stake_pct': s.base_stake_pct,
                    'tp': s.take_profit_pct,
                    'sl': s.stop_loss_pct
                }
                for s in sorted(active, key=lambda x: x.performance_score, reverse=True)
            ]
        }


# ═══════════════════════════════════════════════════════════════
# MARKET REGIME DETECTOR
# ═══════════════════════════════════════════════════════════════

class RegimeDetector:
    REGIMES = [
        'trending_up', 'trending_down', 'high_volatility',
        'range_bound', 'accumulation', 'distribution',
        'breakout', 'crash', 'recovery'
    ]
    
    def __init__(
        self,
        llm_url: str,
        llm_key: str,
        model: str = 'deepseek-v4-flash',
        fallback_model: str = 'agnes-2.5-flash',
        models: Optional[List[str]] = None,
    ):
        self.llm_url = llm_url
        self.llm_key = llm_key
        if models:
            self.models = [m for m in models if m]
        else:
            self.models = [model] + ([fallback_model] if fallback_model else [])
        self.model = self.models[0]
        self.fallback_model = self.models[1] if len(self.models) > 1 else 'agnes-2.5-flash'
        self.last_regime = None
    
    def detect(self, market_data: Dict) -> Dict:
        import requests
        btc = market_data.get('BTC/USDT', {})
        eth = market_data.get('ETH/USDT', {})
        up_count = sum(1 for d in market_data.values() if d.get('change_24h', 0) > 0)
        down_count = len(market_data) - up_count
        btc_change = btc.get('change_24h', 0)
        btc_price = btc.get('price', 0)
        high_low_range = ((btc.get('high_24h', 0) - btc.get('low_24h', 0)) / btc_price * 100) if btc_price > 0 else 0

        # Technical indicators from candles
        btc_ind = btc.get('indicators', {})
        eth_ind = eth.get('indicators', {})
        candle_context = ""
        if btc_ind:
            candle_context += f"\n- BTC Indicators: EMA12=${btc_ind.get('ema12', 0):.0f}, RSI14={btc_ind.get('rsi14', 50):.0f}, Trend={btc_ind.get('trend', '?')}"
        if eth_ind:
            candle_context += f"\n- ETH Indicators: EMA12=${eth_ind.get('ema12', 0):.0f}, RSI14={eth_ind.get('rsi14', 50):.0f}, Trend={eth_ind.get('trend', '?')}"
        
        prompt = f"""You are the market regime analyzer for an automated trading system.

Return exactly one valid JSON object. Do not use Markdown or code fences.
Allowed primary_regime values: trending_up, trending_down, range_bound, breakout, accumulation, recovery, high_volatility, crash.
Allowed risk_level values: low, medium, high, extreme.
confidence must be an integer from 0 to 100.

Market Regime Detection:
- BTC: ${btc_price:.0f}, 24h Change: {btc_change:.2f}%, Range: {high_low_range:.1f}%
- ETH 24h Change: {eth.get('change_24h', 0):.2f}%
- Pairs Up: {up_count}/{len(market_data)}, Down: {down_count}/{len(market_data)}{candle_context}

Output only the JSON object. Do not explain your reasoning. Keep reason_arabic under 80 characters.
Output ONLY JSON: {{"primary_regime": "<regime>", "secondary_regime": "<regime>", "confidence": <int>, "reason_arabic": "<Arabic reason>", "risk_level": "<low|medium|high|extreme>"}}"""

        failures = []
        for idx, model in enumerate(self.models):
            try:
                last_error = None
                for attempt in range(2):
                    raw = ''
                    finish_reason = ''
                    content_length = 0
                    res = requests.post(
                        f"{self.llm_url.rstrip('/')}/chat/completions",
                        json={
                            'model': model,
                            'messages': [
                                {'role': 'system', 'content': 'Return exactly one compact valid JSON object. No reasoning, Markdown, or text outside JSON. Keep reason_arabic under 80 characters.'},
                                {'role': 'user', 'content': prompt},
                            ],
                            'temperature': 0.2,
                            'max_tokens': 1000,
                            'response_format': {'type': 'json_object'},
                        },
                        headers={'Authorization': f'Bearer {self.llm_key}', 'Content-Type': 'application/json'},
                        timeout=45,
                    )
                    try:
                        if res.status_code != 200:
                            raise ValueError(f"LLM HTTP {res.status_code}: {res.text[:200]}")
                        raw, finish_reason, content_length = extract_completion(res)
                        result = parse_llm_json(raw)
                        if not valid_regime(result):
                            raise ValueError("Market regime JSON failed schema validation")
                        result['decision_source'] = f'{model}@nararouter'
                        result['analysis_model'] = model
                        self.last_regime = result
                        return result
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                        last_error = exc
                        llm_diagnostics(
                            res, stage='market regime', attempt=attempt,
                            finish_reason=finish_reason, content_length=content_length,
                            error=exc, raw=raw,
                        )
                        if attempt == 0:
                            time.sleep(1)
                raise ValueError(f"Market regime failed after retry: {last_error}")
            except Exception as e:
                failures.append(f'{model}: {e}')
                if idx + 1 < len(self.models):
                    next_model = self.models[idx + 1]
                    print(f"  ⚠️ {model} unavailable; trying {next_model}")
        print(f"  ❌ Market regime unavailable: {' | '.join(failures)}")
        return {
            'primary_regime': 'llm_unavailable',
            'secondary_regime': 'llm_unavailable',
            'confidence': 0,
            'reason_arabic': 'نماذج التحليل غير متاحة — إيقاف التداول',
            'risk_level': 'extreme',
            'decision_source': 'none',
            'llm_error': ' | '.join(failures),
        }
        
        # No rule-based regime fallback: the configured analysis models own analysis.
        return {
            'primary_regime': 'llm_unavailable',
            'secondary_regime': 'llm_unavailable',
            'confidence': 0,
            'reason_arabic': 'نماذج التحليل غير متاحة — إيقاف التداول',
            'risk_level': 'extreme',
            'decision_source': 'none'
        }
    
    def _fallback_detect(self, market_data: Dict) -> Dict:
        btc = market_data.get('BTC/USDT', {})
        change = btc.get('change_24h', 0)
        price = btc.get('price', 0)
        vol_range = ((btc.get('high_24h', 0) - btc.get('low_24h', 0)) / price * 100) if price > 0 else 0
        
        if change > 3: regime, risk = 'trending_up', 'low'
        elif change > 1: regime, risk = 'trending_up', 'medium'
        elif change < -5: regime, risk = 'crash', 'extreme'
        elif change < -2: regime, risk = 'trending_down', 'high'
        elif vol_range > 8: regime, risk = 'high_volatility', 'high'
        else: regime, risk = 'range_bound', 'medium'
        
        return {'primary_regime': regime, 'secondary_regime': 'range_bound', 'confidence': 60,
                'reason_arabic': 'تحليل آلي', 'risk_level': risk}


# ═══════════════════════════════════════════════════════════════
# DYNAMIC STRATEGY ENGINE
# ═══════════════════════════════════════════════════════════════

class StrategyEngine:
    def __init__(
        self,
        llm_url: str,
        llm_key: str,
        model: str = 'agnes-2.5-flash',
        analysis_model: str = 'deepseek-v4-flash',
        analysis_fallback: str = 'agnes-2.5-flash',
        decision_model: str = 'agnes-2.5-flash',
        analysis_models: Optional[List[str]] = None,
        decision_models: Optional[List[str]] = None,
    ):
        self.store = StrategyStore()
        # Keep the execution guard backed by the same persisted trade archive
        # used by the learning cycle. The controller calls this through the
        # strategy engine before every BUY decision.
        from hermes_learning_engine import HermesLearningEngine

        self.analysis_models = analysis_models or [
            analysis_model,
            analysis_fallback,
            'agnes-2.0-flash',
            'minimax-m3-free',
        ]
        self.decision_models = decision_models or [
            decision_model,
            'deepseek-v4-flash',
            'agnes-2.0-flash',
            'minimax-m3-free',
        ]

        self.learning_engine = HermesLearningEngine(llm_url, llm_key, self.decision_models[0])
        self.regime_detector = RegimeDetector(
            llm_url, llm_key, models=self.analysis_models
        )
        self.llm_url = llm_url
        self.llm_key = llm_key
        self.model = self.decision_models[0]
        self.analysis_model = self.analysis_models[0]
        self.analysis_fallback = self.analysis_models[1] if len(self.analysis_models) > 1 else 'agnes-2.5-flash'
        self.decision_model = self.decision_models[0]
        self.current_regime: Optional[Dict] = None
        self.active_strategies: List[StrategyDNA] = []
        self.cycle_decisions: Dict = {}
    
    def analyze_market_and_select(self, market_data: Dict, open_positions: Optional[List[Dict]] = None) -> Dict:
        import requests
        
        regime = self.regime_detector.detect(market_data)
        self.current_regime = regime
        primary = regime.get('primary_regime', 'range_bound')
        risk = regime.get('risk_level', 'medium')
        
        print(f"\n  🌍 Market Regime: {primary} | Risk: {risk}")
        
        # The analysis model owns regime analysis. If it fails, do not ask for pair
        # decisions and never create an order from partial/fallback output.
        if primary == 'llm_unavailable' or regime.get('decision_source') == 'none':
            decisions = {
                pair: {
                    'action': 'neutral',
                    'confidence': 0,
                    'strategy_id': 'none',
                    'reason': f'{self.analysis_model} unavailable - no trade',
                    'decision_source': 'none',
                    'price': data.get('price', 0),
                    'change_24h': data.get('change_24h', 0)
                }
                for pair, data in market_data.items()
            }
            self.active_strategies = []
            self.cycle_decisions = decisions
            return {'regime': regime, 'strategies': [], 'decisions': decisions}

        self.active_strategies = self.store.get_best_for_regime(primary, top_n=3)
        if risk in ('high', 'extreme'):
            self.active_strategies = self.active_strategies[:1]

        now_iso = datetime.now().isoformat()
        for s in self.active_strategies:
            s.last_used_at = now_iso
        if self.active_strategies:
            self.store.save()

        decisions = {}
        open_by_pair = {
            p.get('pair'): p for p in (open_positions or [])
            if isinstance(p, dict) and p.get('pair')
        }
        # NaraRouter may queue requests. Pace requests rather than
        # bursting 14 concurrent calls, which increases queueing and timeouts.
        for pair, data in market_data.items():
            decision = self._analyze_pair_with_strategies(
                pair, data, market_data, primary, open_by_pair.get(pair)
            )
            decisions[pair] = decision
            time.sleep(0.25)

        self.cycle_decisions = decisions
        return {'regime': regime, 'strategies': [s.id for s in self.active_strategies], 'decisions': decisions}

    def analyze_open_positions(self, market_data: Dict, positions: List[Dict]) -> Dict:
        """Analyze only open positions for HOLD/SELL; never produces entry work."""
        self.active_strategies = self.store.get_best_for_regime('range_bound', top_n=3)
        decisions = {}
        for position in positions:
            pair = position.get('pair')
            if not pair or pair not in market_data:
                continue
            decisions[pair] = self._analyze_pair_with_strategies(
                pair, market_data[pair], market_data, 'exit_review', position
            )
        return {
            'regime': {
                'primary_regime': 'exit_review', 'risk_level': 'medium',
                'decision_source': 'exit_review',
            },
            'decisions': decisions,
        }

    def evaluate_asset_rotation(self, positions: List[Dict], decisions: Dict, market_data: Dict) -> Optional[Dict]:
        """Choose a stale position only when a materially stronger entry exists."""
        open_pairs = {p.get('pair') for p in positions}
        stale = []
        for position in positions:
            pair = position.get('pair')
            decision = decisions.get(pair, {})
            age = self._position_age_hours(position)
            pnl = float(position.get('profit_pct', 0) or 0)
            if age >= 3 and -0.8 <= pnl <= 0.5 and decision.get('action') != 'buy':
                stale.append((float(decision.get('confidence', 0) or 0), pair, position))

        candidates = []
        for pair, decision in decisions.items():
            if pair in open_pairs or decision.get('action') != 'buy':
                continue
            confidence = float(decision.get('confidence', 0) or 0)
            if confidence >= 80 and pair in market_data:
                candidates.append((confidence, pair, decision))

        if not stale or not candidates:
            return None
        old_confidence, old_pair, old_position = min(stale, key=lambda item: item[0])
        new_confidence, new_pair, new_decision = max(candidates, key=lambda item: item[0])
        spread = new_confidence - old_confidence
        if spread < 25:
            return None
        return {
            'from_pair': old_pair,
            'from_trade_id': old_position.get('trade_id'),
            'to_pair': new_pair,
            'to_confidence': new_confidence,
            'from_confidence': old_confidence,
            'spread': spread,
            'stake': float(old_position.get('stake_amount', 0) or 0),
            'reason': 'OPPORTUNITY_ROTATION',
            'decision': new_decision,
        }

    @staticmethod
    def _position_age_hours(position: Dict) -> float:
        opened = position.get('open_date') or position.get('open_date_utc')
        if not opened:
            return 0.0
        try:
            value = datetime.fromisoformat(str(opened).replace('Z', '+00:00'))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - value).total_seconds() / 3600)
        except (TypeError, ValueError):
            return 0.0
    
    def _analyze_pair_with_strategies(self, pair: str, data: Dict, all_data: Dict, regime: str, position: Optional[Dict] = None) -> Dict:
        import requests
        btc = all_data.get('BTC/USDT', {})
        
        strat_descriptions = []
        for s in self.active_strategies:
            strat_descriptions.append(
                f"- {s.name}: conf={s.min_confidence}%, TP={s.take_profit_pct}%, SL={s.stop_loss_pct}%, stake={s.base_stake_pct}%"
            )
        
        position_context = "No open position. Evaluate entry only."
        if position:
            position_context = (
                "OPEN POSITION: evaluate HOLD or SELL before any BUY. "
                f"trade_id={position.get('trade_id')}, "
                f"entry={position.get('open_rate')}, current={position.get('current_rate')}, "
                f"profit_pct={position.get('profit_pct')}%, "
                f"profit_abs={position.get('profit_abs')}, "
                f"stake={position.get('stake_amount')}, "
                f"open_date={position.get('open_date') or position.get('open_date_utc')}"
            )

        candle_context = ""
        indicators = data.get('indicators', {})
        if indicators:
            kaufman_er = indicators.get('kaufman_er', 0.5)
            adaptive_rsi = indicators.get('adaptive_rsi', indicators.get('rsi14', 50))
            ad_period = indicators.get('adaptive_rsi_period', 14)
            fee_be = indicators.get('fee_breakeven_pct', 0.30)
            candle_context += (
                f"\nTechnical: EMA12=${indicators.get('ema12', 0):.4f} (period {indicators.get('adaptive_ema_period', 12)}), "
                f"Adaptive RSI({ad_period})={adaptive_rsi:.0f}, RSI14={indicators.get('rsi14', 50):.0f}, "
                f"Kaufman ER={kaufman_er:.2f}, ATR14={indicators.get('atr14', 0):.4f} ({indicators.get('atr_pct', 0):.2f}%), "
                f"OKX Fee Floor={fee_be:.2f}%, Trend={indicators.get('trend', '?')}"
            )
            if indicators.get('rsi_divergence') and indicators.get('rsi_divergence') != 'NONE':
                candle_context += f", Divergence={indicators.get('rsi_divergence')} 🟢"
            if indicators.get('buy_vol_ratio') is not None:
                candle_context += f", Volume Flow={indicators.get('buy_vol_ratio')}% Buyer Volume"
            if indicators.get('fast_15m_trend') and indicators.get('fast_15m_trend') != 'neutral':
                candle_context += f", 15m Fast Trend={indicators.get('fast_15m_trend').upper()}"
            candle_context += f", 24h Range: ${indicators.get('low_24h_candle', 0):.4f}-${indicators.get('high_24h_candle', 0):.4f}"
            
        ob = data.get('orderbook', {})
        if ob:
            candle_context += f"\nOrder Book: {ob.get('bid_ratio', 50)}% Bids vs {ob.get('ask_ratio', 50)}% Asks (Spread: {ob.get('spread_pct', 0.05)}%)"

        # Adaptive Multi-Timeframe Context
        candles_15m = data.get('candles_15m', [])
        if candles_15m and (regime in ('high_volatility', 'scalping') or len(candles_15m) >= 4):
            recent_15m = candles_15m[-6:]
            c15_lines = []
            for c in recent_15m:
                from datetime import datetime as _dt
                t_str = _dt.utcfromtimestamp(c[0] / 1000).strftime('%H:%M') if c[0] > 1e9 else '??:??'
                direction = '▲' if c[4] >= c[1] else '▼'
                c15_lines.append(f"  {t_str} {direction} {c[1]:.2f}→{c[4]:.2f} (H{c[2]:.2f}/L{c[3]:.2f})")
            candle_context += f"\n15m Fast Candles (last {len(recent_15m)}):\n" + '\n'.join(c15_lines)

        candles_1h = data.get('candles_1h', [])
        if candles_1h:
            recent = candles_1h[-6:]
            candle_lines = []
            for c in recent:
                from datetime import datetime as _dt
                t_str = _dt.utcfromtimestamp(c[0] / 1000).strftime('%H:%M') if c[0] > 1e9 else '??:??'
                direction = '▲' if c[4] >= c[1] else '▼'
                candle_lines.append(f"  {t_str} {direction} {c[1]:.2f}→{c[4]:.2f} (H{c[2]:.2f}/L{c[3]:.2f})")
            candle_context += f"\n1h Macro Candles (last {len(recent)}):\n" + '\n'.join(candle_lines)

        prompt = f"""You are Hermes AI Trading Brain - Dynamic Strategy Engine v4.0 (ADAPTIVE MODE).
Analyze {pair} using active strategies.

Market: {pair} at ${data['price']:.4f}, 24h: {data['change_24h']:.2f}%
BTC: ${btc.get('price', 0):.0f}, 24h: {btc.get('change_24h', 0):.2f}%
Regime: {regime}{candle_context}
{position_context}

Active Strategies:
{chr(10).join(strat_descriptions)}

Rules:
1. Output ONLY valid JSON: {{"action": "buy"|"sell"|"neutral", "confidence": <int 0-100>, "strategy_id": "<matching strategy>", "reason": "<Arabic rationale>"}}
2. For an OPEN POSITION, decide HOLD as `neutral` unless there is a concrete exit reason; use `sell` only when exit is justified by trend deterioration, risk, or a valid target/stop condition. Never return `buy` for an open position.
3. For a new pair, evaluate entry. In trending_up or breakout with high Kaufman ER (>0.55) or buyer volume > 50%, favor BUY with confidence 60-85%. In volatile or down regimes, require strict confirmation.
4. Write reason in ARABIC and keep it under 80 characters.
5. Do not explain your reasoning. Return only the JSON object."""

        failures = []
        for idx, model in enumerate(self.decision_models):
            try:
                last_error = None
                for attempt in range(2):
                    raw = ''
                    finish_reason = ''
                    content_length = 0
                    res = requests.post(
                        f"{self.llm_url.rstrip('/')}/chat/completions",
                        json={
                            'model': model,
                            'messages': [
                                {'role': 'system', 'content': 'Return exactly one compact valid JSON object. No reasoning, Markdown, or text outside JSON. Keep reason under 80 characters.'},
                                {'role': 'user', 'content': prompt},
                            ],
                            'temperature': 0.1,
                            'max_tokens': 500,
                            'response_format': {'type': 'json_object'},
                        },
                        headers={'Authorization': f'Bearer {self.llm_key}', 'Content-Type': 'application/json'},
                        timeout=45,
                    )
                    try:
                        if res.status_code != 200:
                            raise ValueError(f"LLM HTTP {res.status_code}: {res.text[:200]}")
                        raw, finish_reason, content_length = extract_completion(res)
                        decision = parse_llm_json(raw)
                        if not valid_pair_decision(decision):
                            raise ValueError("Pair decision JSON failed schema validation")
                        decision['decision_source'] = f'{model}@nararouter'
                        decision['decision_model'] = model
                        decision['price'] = data['price']
                        decision['change_24h'] = data['change_24h']
                        sid = decision.get('strategy_id', 'none')
                        if sid != 'none' and sid not in self.store.strategies:
                            decision['strategy_id'] = self.active_strategies[0].id if self.active_strategies else 'none'
                        print(f"  🧠 {pair}: {decision['action'].upper()} | Conf: {decision.get('confidence', 0)}% | Model: {model} | Strategy: {decision.get('strategy_id', 'none')[:20]}")
                        return decision
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                        last_error = exc
                        llm_diagnostics(
                            res, stage=f'pair decision {pair}', attempt=attempt,
                            finish_reason=finish_reason, content_length=content_length,
                            error=exc, raw=raw,
                        )
                        if attempt == 0:
                            time.sleep(1)
                raise ValueError(f"Pair decision failed after retry: {last_error}")
            except Exception as e:
                failures.append(f'{model}: {e}')
                if idx + 1 < len(self.decision_models):
                    next_model = self.decision_models[idx + 1]
                    print(f"  ⚠️ {model} unavailable for {pair}; trying {next_model}")

        print(f"  ❌ All decision models unavailable for {pair}: {' | '.join(failures)}")
        return {
            'action': 'neutral',
            'confidence': 0,
            'strategy_id': 'none',
            'reason': 'نماذج القرار غير متاحة — لا يوجد تداول',
            'decision_source': 'none',
            'llm_error': ' | '.join(failures),
            'price': data['price'],
            'change_24h': data['change_24h']
        }
    
    def confirm_trade_signal(
        self,
        pair: str,
        decision: Dict,
        data: Dict,
        regime: str,
        position: Optional[Dict] = None,
    ) -> Tuple[bool, str]:
        """Require a second model confirmation before an LLM-generated order."""
        action = decision.get('action', 'neutral')
        if action not in {'buy', 'sell'}:
            return True, 'no confirmation required for neutral'

        import requests
        position_text = 'open position' if position else 'no open position'
        prompt = f"""You are the independent trade-signal verifier for Hermes.
Return only JSON: {{"confirm": true|false, "action": "buy"|"sell"|"neutral", "confidence": 0, "reason": "Arabic"}}

Pair: {pair}; price: {data.get('price', 0):.8f}; 24h: {data.get('change_24h', 0):.2f}%
Market regime: {regime}; position: {position_text}
Proposed action: {action}; confidence: {decision.get('confidence', 0)}
Proposed reason: {decision.get('reason', '')}
Confirm only when the action is justified. Never confirm BUY for an open position.
Keep reason under 80 characters."""

        for model in self.decision_models:
            try:
                res = requests.post(
                    f"{self.llm_url.rstrip('/')}/chat/completions",
                    json={
                        'model': model,
                        'messages': [
                            {'role': 'system', 'content': 'Return one compact valid JSON object only.'},
                            {'role': 'user', 'content': prompt},
                        ],
                        'temperature': 0.0,
                        'max_tokens': 160,
                        'response_format': {'type': 'json_object'},
                    },
                    headers={'Authorization': f'Bearer {self.llm_key}', 'Content-Type': 'application/json'},
                    timeout=45,
                )
                if res.status_code != 200:
                    raise ValueError(f"confirmation HTTP {res.status_code}: {res.text[:160]}")
                raw, _, _ = extract_completion(res)
                result = parse_llm_json(raw)
                regime_lower = str(regime).lower()
                min_conf = 55 if ('trend_up' in regime_lower or 'breakout' in regime_lower) else (75 if ('down' in regime_lower or 'crash' in regime_lower) else 60)
                confirmed = (
                    result.get('confirm') is True
                    and result.get('action') == action
                    and isinstance(result.get('confidence'), int)
                    and result['confidence'] >= min_conf
                )
                reason = str(result.get('reason') or 'confirmation rejected')[:120]
                print(
                    f"  {'✅' if confirmed else '⛔'} {pair} {action.upper()} confirmation ({model}): "
                    f"{'accepted' if confirmed else 'rejected'} ({result.get('confidence', 0)}%, req >={min_conf}%)"
                )
                return confirmed, reason
            except Exception as exc:
                print(f"  ⚠️ {model} confirmation unavailable: {exc}")
                continue

        return False, 'all confirmation models unavailable'

    def analyze_news_disaster(self, pair: str, article_text: str) -> bool:
        """Analyze a full article to detect disastrous news. Returns True if disaster (panic sell/abort)."""
        import requests
        prompt = f"""You are the Hermes News Guard.
Analyze the following crypto news article text for {pair}.
Determine if there is a CATASTROPHIC DISASTER that warrants an immediate panic sell or aborting a buy.
Catastrophic disasters include: major network hacks, CEO arrests, SEC lawsuits, delisting, or bankruptcy.
Normal price drops or bearish sentiment are NOT disasters.
Return only JSON: {{"disaster": true|false, "reason": "Arabic rationale"}}

Article Text (truncated to 2000 chars):
{article_text[:2000]}"""
        
        for model in self.decision_models:
            try:
                provider_cfg = self._get_provider_config(model)
                res = requests.post(
                    provider_cfg.url,
                    json=provider_cfg.build_payload(model, prompt),
                    headers=provider_cfg.headers,
                    timeout=15
                )
                if res.status_code != 200: continue
                raw, _, _ = extract_completion(res)
                result = parse_llm_json(raw)
                
                is_disaster = result.get('disaster') is True
                reason = result.get('reason', '')
                if is_disaster:
                    print(f"  🚨 NEWS DISASTER DETECTED for {pair}: {reason}")
                return is_disaster
            except Exception as e:
                continue
        return False

    def get_confidence_threshold(self, strategy_id: str, regime: Optional[str] = None) -> float:
        s = self.store.strategies.get(strategy_id)
        base = s.min_confidence if s else 65.0
        regime_lower = str(regime or (self.current_regime or {}).get('primary_regime', '')).lower()
        if 'trend_up' in regime_lower or 'breakout' in regime_lower:
            regime_target = 60.0
        elif 'range' in regime_lower or 'accum' in regime_lower or 'recovery' in regime_lower:
            regime_target = 68.0
        elif 'volatil' in regime_lower or 'trend_down' in regime_lower:
            regime_target = 80.0
        elif 'crash' in regime_lower:
            regime_target = 95.0
        else:
            regime_target = 65.0
        
        # Weighted blend of strategy's baseline confidence and current market regime requirement
        return round(max(55.0, min(95.0, (base * 0.4) + (regime_target * 0.6))), 1)
    
    def get_strategy(self, strategy_id: str) -> Optional[StrategyDNA]:
        return self.store.strategies.get(strategy_id)

    def dynamic_exit_levels(self, position: Dict, market: Dict, decision: Dict) -> Dict[str, float]:
        """Derive adaptive exits from volatility, ATR, confidence, age, and regime."""
        price = float(market.get('price', 0) or 0)
        high = float(market.get('high_24h', 0) or 0)
        low = float(market.get('low_24h', 0) or 0)
        change = abs(float(market.get('change_24h', 0) or 0))
        range_pct = ((high - low) / price * 100) if price and high >= low else 0.0
        
        indicators = market.get('indicators', {}) if isinstance(market, dict) else {}
        atr_pct = float(indicators.get('atr_pct', 0) or 0)
        base_volatility = max(change, range_pct * 0.35, 0.5)
        volatility = max(base_volatility, atr_pct) if atr_pct > 0 else base_volatility

        confidence = float(decision.get('confidence', 50) or 50)
        age = self._position_age_hours(position)
        regime = str(decision.get('regime', '')).lower()

        # Adaptive ATR / Volatility bounds
        target = max(2.0, min(9.0, volatility * 1.4 + confidence / 100 * 1.5))
        stop = max(1.0, min(3.5, volatility * 0.85 + 0.6))
        if 'trend' in regime or 'breakout' in regime:
            target = min(10.0, target * 1.25)
        if age >= 2:
            target = max(1.5, target * 0.85)
            stop = max(1.0, stop * 0.9)
        return {'take_profit_pct': target, 'stop_loss_pct': -stop,
                'volatility_pct': volatility, 'age_hours': age}

    def loss_guard(self, strategy_id: str, pair: str, current_regime: Optional[str] = None) -> tuple[bool, str]:
        return self.learning_engine.loss_guard(strategy_id, pair, current_regime=current_regime)
    
    def record_trade_result(self, strategy_id: str, pnl: float, pnl_pct: float, hold_hours: float):
        self.store.record_trade(strategy_id, pnl, pnl_pct, hold_hours)
        self.store.global_stats['total_cycles'] += 1
        self.store.save()
    
    def run_weekly_optimization(self) -> Dict:
        import requests
        prompt = self.store.get_summary()
        prompt += "\n\nOptimize these strategies for maximum profit (10% TP mode)."
        prompt += """\n\nOutput JSON: {"actions": [{"type": "tune|deactivate|evolve", "strategy_id": "...", "changes": {...}, "reason_arabic": "..."}], "summary_arabic": "..."}"""
        
        try:
            res = requests.post(
                f"{self.llm_url.rstrip('/')}/chat/completions",
                json={'model': self.model, 'messages': [{'role': 'system', 'content': 'Optimizer. Output JSON only.'}, {'role': 'user', 'content': prompt}], 'temperature': 0.3, 'max_tokens': 500},
                headers={'Authorization': f'Bearer {self.llm_key}', 'Content-Type': 'application/json'},
                timeout=600
            )
            if res.status_code == 200:
                raw = res.json()['choices'][0]['message']['content'].strip()
                if '```json' in raw:
                    raw = raw.split('```json')[1].split('```')[0].strip()
                plan = json.loads(raw)
                results = []
                for action in plan.get('actions', []):
                    atype = action.get('type')
                    if atype == 'tune' and action['strategy_id'] in self.store.strategies:
                        for key, val in action.get('changes', {}).items():
                            if hasattr(self.store.strategies[action['strategy_id']], key):
                                setattr(self.store.strategies[action['strategy_id']], key, val)
                        results.append(f"✅ Tuned {action['strategy_id']}")
                self.store.global_stats['last_optimization'] = datetime.now().isoformat()
                self.store.global_stats['optimization_count'] += 1
                self.store.save()
                return {'status': 'success', 'actions': results}
        except:
            pass
        return {'status': 'error'}
    
    def get_status_report(self) -> Dict:
        return {
            'regime': self.current_regime,
            'strategies': self.store.get_summary(),
            'active_in_cycle': [s.id for s in self.active_strategies] if self.active_strategies else [],
            'last_decisions': self.cycle_decisions
        }


if __name__ == '__main__':
    store = StrategyStore()
    print(f"\n📊 Strategy Store: {len(store.strategies)} strategies")
    for s in store.strategies.values():
        print(f"  {s.name}: Score={s.performance_score:.0f}, Trades={s.total_trades}, WR={s.win_rate:.0f}%, Stake={s.base_stake_pct}%, TP={s.take_profit_pct}%")
