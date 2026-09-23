"""Motor determinista de señales y reglas de trading para TradIA.

Combina los indicadores técnicos en señales de COMPRA y VENTA explicables,
sin decisiones opacas ni aprendizaje automático no supervisado.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import pandas as pd

from src.config import AppConfig


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class SignalEvent:
    """Representa una señal generada con todas sus métricas y razones explícitas."""
    timestamp: datetime
    symbol: str
    action: SignalAction
    price: float
    score: int
    reasons: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_alert_message(self) -> str:
        """Formatea la señal para envío por WhatsApp / consola."""
        emoji = "🟢" if self.action == SignalAction.BUY else "🔴"
        action_es = "COMPRA" if self.action == SignalAction.BUY else "VENTA"
        date_str = self.timestamp.strftime("%d/%m %H:%M UTC")

        reasons_str = " | ".join(self.reasons)
        return (
            f"{emoji} {action_es} {self.symbol} — Precio: ${self.price:,.2f} ({date_str})\n"
            f"📊 Motivos (Puntuación {self.score}):\n"
            + "\n".join([f"  • {r}" for r in self.reasons])
        )


class RuleEngine:
    """Motor de evaluación de reglas e indicadores para disparar señales."""

    def __init__(self, config: AppConfig):
        self.config = config

    def evaluate_candle(self, row: pd.Series, symbol: str, timestamp: datetime) -> Optional[SignalEvent]:
        """Evalúa una única vela con todos sus indicadores ya calculados."""
        strat = self.config.strategy
        ind = self.config.indicators
        weights = strat.weights

        buy_score = 0
        sell_score = 0
        buy_reasons: List[str] = []
        sell_reasons: List[str] = []

        rsi_val = float(row.get("rsi", 50.0))
        close_price = float(row.get("close", 0.0))
        vol_ratio = float(row.get("vol_ratio", 1.0))
        vol_surge = bool(row.get("vol_surge", False))

        ema_bullish = bool(row.get("ema_cross_bullish", False))
        ema_bearish = bool(row.get("ema_cross_bearish", False))

        macd_bullish = bool(row.get("macd_cross_bullish", False))
        macd_bearish = bool(row.get("macd_cross_bearish", False))

        bb_pct_b = float(row.get("bb_pct_b", 0.5))

        # 1. Evaluación de RSI
        w_rsi = weights.get("rsi", 2)
        if rsi_val <= ind.rsi.oversold:
            buy_score += w_rsi
            buy_reasons.append(f"RSI en {rsi_val:.1f} (sobreventa < {ind.rsi.oversold})")
        elif rsi_val >= ind.rsi.overbought:
            sell_score += w_rsi
            sell_reasons.append(f"RSI en {rsi_val:.1f} (sobrecompra > {ind.rsi.overbought})")

        # 2. Cruce de EMAs
        w_ema = weights.get("ema_cross", 2)
        if ema_bullish:
            buy_score += w_ema
            buy_reasons.append(f"Cruce alcista EMA{ind.ema.fast_period} sobre EMA{ind.ema.slow_period}")
        elif ema_bearish:
            sell_score += w_ema
            sell_reasons.append(f"Cruce bajista EMA{ind.ema.fast_period} bajo EMA{ind.ema.slow_period}")

        # 3. Cruce de MACD
        w_macd = weights.get("macd_cross", 1)
        if macd_bullish:
            buy_score += w_macd
            buy_reasons.append("Cruce alcista MACD sobre señal")
        elif macd_bearish:
            sell_score += w_macd
            sell_reasons.append("Cruce bajista MACD bajo señal")

        # 4. Bandas de Bollinger (%B < 0.05 o > 0.95)
        w_bb = weights.get("bollinger", 1)
        if bb_pct_b <= 0.05:
            buy_score += w_bb
            buy_reasons.append(f"Precio en rebote banda inferior Bollinger (%B: {bb_pct_b:.2f})")
        elif bb_pct_b >= 0.95:
            sell_score += w_bb
            sell_reasons.append(f"Precio en techo banda superior Bollinger (%B: {bb_pct_b:.2f})")

        # 5. Anomalía de volumen
        w_vol = weights.get("volume_surge", 1)
        if vol_surge:
            # El volumen potencia la dirección que tenga mayor tracción
            if buy_score > sell_score:
                buy_score += w_vol
                buy_reasons.append(f"Volumen anómalo +{((vol_ratio - 1) * 100):.0f}% sobre su media")
            elif sell_score > buy_score:
                sell_score += w_vol
                sell_reasons.append(f"Volumen anómalo +{((vol_ratio - 1) * 100):.0f}% sobre su media")

        metrics_snapshot = {
            "rsi": rsi_val,
            "ema_fast": float(row.get("ema_fast", 0.0)),
            "ema_slow": float(row.get("ema_slow", 0.0)),
            "macd": float(row.get("macd", 0.0)),
            "macd_signal": float(row.get("macd_signal", 0.0)),
            "macd_hist": float(row.get("macd_hist", 0.0)),
            "bb_pct_b": bb_pct_b,
            "vol_ratio": vol_ratio,
        }

        # Decisión final de señal
        if buy_score >= strat.min_score_buy and buy_score > sell_score:
            return SignalEvent(
                timestamp=timestamp,
                symbol=symbol,
                action=SignalAction.BUY,
                price=close_price,
                score=buy_score,
                reasons=buy_reasons,
                metrics=metrics_snapshot,
            )
        elif sell_score >= strat.min_score_sell and sell_score > buy_score:
            return SignalEvent(
                timestamp=timestamp,
                symbol=symbol,
                action=SignalAction.SELL,
                price=close_price,
                score=sell_score,
                reasons=sell_reasons,
                metrics=metrics_snapshot,
            )

        return None

    def evaluate_dataframe(self, df_with_indicators: pd.DataFrame, symbol: str) -> List[SignalEvent]:
        """Evalúa un DataFrame completo y retorna la lista cronológica de señales generadas."""
        signals = []
        for ts, row in df_with_indicators.iterrows():
            signal = self.evaluate_candle(row, symbol=symbol, timestamp=ts)
            if signal is not None:
                signals.append(signal)
        return signals
