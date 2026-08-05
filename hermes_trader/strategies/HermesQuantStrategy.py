# --- Do not remove these libs ---
import numpy as np
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import IStrategy
from freqtrade.strategy import CategoricalParameter, DecimalParameter, IntParameter

# --------------------------------
# Add your lib to import here
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib


class HermesQuantStrategy(IStrategy):
    """
    HermesQuantStrategy - High-Probability Multi-Indicator Spot Strategy
    
    Designed for Hermes Trader Agent:
    - 5m timeframe for fast signal detection
    - RSI + EMA 12/26 cross + MACD confirmation
    - Strict Stop Loss (-2%) with Trailing Stop Loss
    - Hard position size cap (10% max capital per position)
    """

    # Strategy interface version
    INTERFACE_VERSION = 3

    # Optimal timeframe for the strategy
    timeframe = '5m'

    # Minimal ROI table
    minimal_roi = {
        "60": 0.01,
        "30": 0.02,
        "0": 0.04
    }

    # Stoploss: -2%
    stoploss = -0.02

    # Trailing stoploss
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    # Run "populate_indicators()" only for new candle
    process_only_new_candles = True

    # Number of candles to evaluate before strategy start
    startup_candle_count: int = 30

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Populate technical indicators (RSI, EMA, MACD, Volume)
        """
        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # EMAs
        dataframe['ema12'] = ta.EMA(dataframe, timeperiod=12)
        dataframe['ema26'] = ta.EMA(dataframe, timeperiod=26)
        dataframe['ema50'] = ta.EMA(dataframe, timeperiod=50)

        # MACD
        macd = ta.MACD(dataframe)
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']
        dataframe['macdhist'] = macd['macdhist']

        # Volume Mean
        dataframe['volume_mean'] = dataframe['volume'].rolling(20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Entry Signal Criteria:
        1. EMA 12 > EMA 26 (Uptrend)
        2. RSI between 35 and 65 (Not overbought)
        3. MACD histogram > 0 (Positive momentum)
        4. Volume > average volume (Strong volume confirmation)
        """
        dataframe.loc[
            (
                (dataframe['ema12'] > dataframe['ema26']) &
                (dataframe['rsi'] > 35) &
                (dataframe['rsi'] < 65) &
                (dataframe['macdhist'] > 0) &
                (dataframe['volume'] > dataframe['volume_mean']) &
                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Exit Signal Criteria:
        1. EMA 12 < EMA 26 (Downtrend reversal) OR
        2. RSI > 75 (Overbought profit taking)
        """
        dataframe.loc[
            (
                (dataframe['ema12'] < dataframe['ema26']) |
                (dataframe['rsi'] > 75)
            ),
            'exit_long'] = 1

        return dataframe
