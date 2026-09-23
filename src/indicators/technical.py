"""Motor vectorizado de indicadores técnicos para TradIA.

Cálculos matemáticos deterministas de:
- RSI (Relative Strength Index con suavizado de Wilder)
- Cruce de medias móviles exponenciales (EMA rápida / lenta)
- MACD (Moving Average Convergence Divergence)
- Bandas de Bollinger (%B y ancho de banda)
- Ratio de volumen anómalo respecto a su media móvil
"""
import numpy as np
import pandas as pd
from typing import Optional
from src.config import IndicatorsConfig


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calcula el RSI utilizando el método de suavizado exponencial de Wilder."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Suavizado de Wilder: alpha = 1 / period
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Calcula la media móvil exponencial (EMA)."""
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(
    series: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """Calcula la línea MACD, la línea de señal y el histograma."""
    ema_fast = calc_ema(series, fast_period)
    ema_slow = calc_ema(series, slow_period)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal_period)
    hist = macd_line - signal_line

    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": hist,
    }, index=series.index)


def calc_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """Calcula las Bandas de Bollinger, %B y el ancho de banda (bandwidth)."""
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std(ddof=0)
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)

    # %B: 1.0 = en banda superior, 0.0 = en banda inferior
    band_diff = upper - lower
    pct_b = (series - lower) / band_diff.replace(0, np.nan)
    bandwidth = (band_diff / middle.replace(0, np.nan)) * 100.0

    return pd.DataFrame({
        "bb_middle": middle,
        "bb_upper": upper,
        "bb_lower": lower,
        "bb_pct_b": pct_b.fillna(0.5),
        "bb_bandwidth": bandwidth.fillna(0.0),
    }, index=series.index)


def calc_volume_metrics(
    volume_series: pd.Series,
    period: int = 20,
) -> pd.DataFrame:
    """Calcula la media móvil del volumen y el ratio de volumen actual frente a la media."""
    vol_sma = volume_series.rolling(window=period).mean()
    vol_ratio = volume_series / vol_sma.replace(0, np.nan)

    return pd.DataFrame({
        "vol_sma": vol_sma,
        "vol_ratio": vol_ratio.fillna(1.0),
    }, index=volume_series.index)


def compute_all_indicators(df: pd.DataFrame, config: Optional[IndicatorsConfig] = None) -> pd.DataFrame:
    """Añade todas las columnas de indicadores técnicos al DataFrame de velas OHLCV.

    Columnas agregadas:
    - rsi
    - ema_fast, ema_slow
    - ema_cross_bullish (True si cruce alcista en esa vela)
    - ema_cross_bearish (True si cruce bajista en esa vela)
    - macd, macd_signal, macd_hist
    - macd_cross_bullish, macd_cross_bearish
    - bb_middle, bb_upper, bb_lower, bb_pct_b, bb_bandwidth
    - vol_sma, vol_ratio
    """
    if config is None:
        config = IndicatorsConfig()

    result = df.copy()
    close = result["close"]
    volume = result["volume"]

    # 1. RSI
    result["rsi"] = calc_rsi(close, period=config.rsi.period)

    # 2. EMAs
    result["ema_fast"] = calc_ema(close, period=config.ema.fast_period)
    result["ema_slow"] = calc_ema(close, period=config.ema.slow_period)

    # Cruces de EMA
    fast = result["ema_fast"]
    slow = result["ema_slow"]
    prev_fast = fast.shift(1)
    prev_slow = slow.shift(1)

    result["ema_cross_bullish"] = (prev_fast <= prev_slow) & (fast > slow)
    result["ema_cross_bearish"] = (prev_fast >= prev_slow) & (fast < slow)

    # 3. MACD
    macd_df = calc_macd(
        close,
        fast_period=config.macd.fast_period,
        slow_period=config.macd.slow_period,
        signal_period=config.macd.signal_period,
    )
    result["macd"] = macd_df["macd"]
    result["macd_signal"] = macd_df["macd_signal"]
    result["macd_hist"] = macd_df["macd_hist"]

    # Cruces de MACD
    prev_macd = result["macd"].shift(1)
    prev_signal = result["macd_signal"].shift(1)
    result["macd_cross_bullish"] = (prev_macd <= prev_signal) & (result["macd"] > result["macd_signal"])
    result["macd_cross_bearish"] = (prev_macd >= prev_signal) & (result["macd"] < result["macd_signal"])

    # 4. Bandas de Bollinger
    bb_df = calc_bollinger_bands(
        close,
        period=config.bollinger.period,
        std_dev=config.bollinger.std_dev,
    )
    result["bb_middle"] = bb_df["bb_middle"]
    result["bb_upper"] = bb_df["bb_upper"]
    result["bb_lower"] = bb_df["bb_lower"]
    result["bb_pct_b"] = bb_df["bb_pct_b"]
    result["bb_bandwidth"] = bb_df["bb_bandwidth"]

    # 5. Métricas de Volumen
    vol_df = calc_volume_metrics(volume, period=config.volume.sma_period)
    result["vol_sma"] = vol_df["vol_sma"]
    result["vol_ratio"] = vol_df["vol_ratio"]
    result["vol_surge"] = result["vol_ratio"] >= config.volume.multiplier_threshold

    return result
