"""
Hermes Execution Strategy - Dumb Executor
==========================================
This strategy does NOT make trading decisions.
It only provides risk management (stoploss, trailing stop) as safety nets.
ALL entry/exit decisions come from Hermes AI via REST API force-enter/force-exit.
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class HermesExecutionStrategy(IStrategy):
    """
    Dumb execution strategy for Hermes AI.
    - No entry signals (Hermes decides via force-enter)
    - No exit signals (Hermes decides via force-exit)
    - Only provides: stoploss, trailing stop as safety net
    """
    
    def version(self) -> str:
        return "1.0"
    
    # Risk Management - Safety nets only
    stoploss = -0.03          # 3% safety stoploss; Hermes remains decision authority
    trailing_stop = True      # Enable trailing stop
    trailing_stop_positive = 0.05     # Trail after 5% profit
    trailing_stop_positive_offset = 0.07  # Start trailing at 7%
    trailing_only_offset_is_reached = False
    
    # Position management
    max_open_trades = 14
    
    # Order types - market orders for instant execution
    order_types = {
        "entry": "market",
        "exit": "market",
        "emergency_exit": "market",
        "force_exit": "market",
        "force_entry": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    
    order_time_in_force = {
        "entry": "gtc",
        "exit": "gtc",
    }
    
    # 5min timeframe for responsive execution
    timeframe = "5m"
    
    # Process only new candles
    process_only_new_candles = True
    
    # Startup candle count (minimal since we don't use indicators for signals)
    startup_candle_count: int = 20
    
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Minimal indicators - only for trailing stop calculation.
        Hermes makes ALL decisions externally.
        """
        # ATR for trailing stop calculation (used internally by freqtrade)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        
        # SMA for reference only (not used for signals)
        dataframe['sma_20'] = ta.SMA(dataframe, timeperiod=20)
        dataframe['sma_50'] = ta.SMA(dataframe, timeperiod=50)
        
        return dataframe
    
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        NO ENTRY SIGNALS.
        Hermes controls entries via force-enter API.
        This returns all zeros - no automatic entries.
        """
        dataframe.loc[:, 'enter_long'] = 0
        dataframe.loc[:, 'enter_tag'] = ''
        return dataframe
    
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        NO EXIT SIGNALS.
        Hermes controls exits via force-exit API.
        Trailing stop and stoploss still work as safety nets.
        """
        dataframe.loc[:, 'exit_long'] = 0
        dataframe.loc[:, 'exit_tag'] = ''
        return dataframe
    
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, 
                            rate: float, time_in_force: str, **kwargs) -> bool:
        """
        Confirm trade entry.
        Since Hermes decides via force-enter, always confirm.
        """
        return True
    
    def confirm_trade_exit(self, pair: str, trade: 'Trade', order_type: str, 
                           amount: float, rate: float, time_in_force: str, 
                           exit_reason: str, **kwargs) -> bool:
        """
        Confirm trade exit.
        Allow all exits (force-exit, stoploss, trailing stop, ROI).
        """
        return True
    
    # Optional: Custom stake sizing (not used with force-enter which specifies stake)
    def custom_stake_amount(self, pair: str, current_time, current_rate: float, 
                            proposed_stake: float, min_stake: float, 
                            max_stake: float, leverage: float, 
                            entry_tag: str, **kwargs) -> float:
        return proposed_stake


# For backward compatibility - alias
HermesCryptoStrategy = HermesExecutionStrategy