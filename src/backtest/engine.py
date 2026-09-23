"""Motor de Backtesting cuantitativo con Take Profit y Stop Loss fijos.

Permite simular rigurosamente el rendimiento histórico de las señales
antes de activar cualquier alerta en vivo, teniendo en cuenta comisiones,
deslizamiento (slippage) y reglas conservadoras de salida.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from src.config import AppConfig
from src.strategy.rules import SignalAction, SignalEvent


@dataclass
class Trade:
    """Registro de una operación simulada en el backtesting."""
    symbol: str
    action: SignalAction
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    exit_reason: str  # "TAKE_PROFIT", "STOP_LOSS", "TIME_LIMIT", "END_OF_DATA"
    return_pct: float
    net_return_pct: float
    holding_bars: int
    signal_score: int
    signal_reasons: List[str]


@dataclass
class BacktestResult:
    """Métricas y estadísticas consolidadas del backtest."""
    symbol: str
    total_candles: int
    start_date: datetime
    end_date: datetime
    total_signals: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    avg_return_pct: float
    cumulative_return_pct: float
    profit_factor: float
    max_drawdown_pct: float
    avg_holding_bars: float
    tp_hits: int
    sl_hits: int
    time_limit_exits: int
    trades: List[Trade] = field(default_factory=list)
    indicator_stats: Dict[str, Dict[str, float]] = field(default_factory=dict)


class BacktestEngine:
    """Simulador de operativa con TP/SL fijos sobre velas OHLCV e indicadores calculados."""

    def __init__(self, config: AppConfig):
        self.config = config

    def run(
        self,
        df: pd.DataFrame,
        signals: List[SignalEvent],
        symbol: str,
        allow_short: bool = False,
    ) -> BacktestResult:
        """Ejecuta la simulación de backtesting cruzando señales con precios futuros.

        Args:
            df: DataFrame con [open, high, low, close] e índice temporal UTC.
            signals: Lista de señales generadas por RuleEngine.
            symbol: Par evaluado (ej. 'BTC/USDT').
            allow_short: Si es False, solo simula compras (long-only, típico de Spot).

        Returns:
            BacktestResult con todas las métricas calculadas.
        """
        if df.empty or not signals:
            return BacktestResult(
                symbol=symbol,
                total_candles=len(df),
                start_date=df.index[0] if not df.empty else datetime.now(),
                end_date=df.index[-1] if not df.empty else datetime.now(),
                total_signals=len(signals),
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                avg_return_pct=0.0,
                cumulative_return_pct=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                avg_holding_bars=0.0,
                tp_hits=0,
                sl_hits=0,
                time_limit_exits=0,
            )

        tp_pct = self.config.backtest.take_profit_pct / 100.0
        sl_pct = self.config.backtest.stop_loss_pct / 100.0
        fee_pct = self.config.backtest.fee_pct / 100.0
        slippage_pct = self.config.backtest.slippage_pct / 100.0
        max_bars = self.config.backtest.max_holding_bars

        # Mapeo rápido de timestamp a índice entero
        ts_to_idx = {ts: idx for idx, ts in enumerate(df.index)}
        total_len = len(df)

        trades: List[Trade] = []
        last_exit_idx = -1  # Para no solapar trades en el mismo activo

        for sig in signals:
            if not allow_short and sig.action == SignalAction.SELL:
                continue

            if sig.timestamp not in ts_to_idx:
                continue

            entry_idx = ts_to_idx[sig.timestamp]
            # Si aún estamos dentro del período del trade anterior, no abrimos otro simultáneo
            if entry_idx <= last_exit_idx:
                continue

            # Si la señal ocurre en la última vela, no hay velas futuras para evaluar
            if entry_idx >= total_len - 1:
                break

            # Entrada al cierre de la vela de señal ajustada por deslizamiento
            raw_entry = sig.price
            if sig.action == SignalAction.BUY:
                entry_price = raw_entry * (1.0 + slippage_pct)
                target_tp = entry_price * (1.0 + tp_pct)
                target_sl = entry_price * (1.0 - sl_pct)
            else:
                entry_price = raw_entry * (1.0 - slippage_pct)
                target_tp = entry_price * (1.0 - tp_pct)
                target_sl = entry_price * (1.0 + sl_pct)

            exit_reason = "END_OF_DATA"
            exit_price = entry_price
            exit_idx = total_len - 1
            bars_held = 0

            # Recorrer velas posteriores
            for step, future_idx in enumerate(range(entry_idx + 1, total_len), start=1):
                candle = df.iloc[future_idx]
                c_high = float(candle["high"])
                c_low = float(candle["low"])
                c_close = float(candle["close"])
                bars_held = step

                if sig.action == SignalAction.BUY:
                    # Enfoque conservador: si ambos se tocan en la misma vela, asumimos SL primero
                    hit_tp = c_high >= target_tp
                    hit_sl = c_low <= target_sl

                    if hit_tp and hit_sl:
                        exit_price = target_sl
                        exit_reason = "STOP_LOSS"
                        exit_idx = future_idx
                        break
                    elif hit_sl:
                        exit_price = target_sl
                        exit_reason = "STOP_LOSS"
                        exit_idx = future_idx
                        break
                    elif hit_tp:
                        exit_price = target_tp
                        exit_reason = "TAKE_PROFIT"
                        exit_idx = future_idx
                        break
                else:  # SHORT
                    hit_tp = c_low <= target_tp
                    hit_sl = c_high >= target_sl

                    if hit_tp and hit_sl:
                        exit_price = target_sl
                        exit_reason = "STOP_LOSS"
                        exit_idx = future_idx
                        break
                    elif hit_sl:
                        exit_price = target_sl
                        exit_reason = "STOP_LOSS"
                        exit_idx = future_idx
                        break
                    elif hit_tp:
                        exit_price = target_tp
                        exit_reason = "TAKE_PROFIT"
                        exit_idx = future_idx
                        break

                # Límite de tiempo intradía (ej. 32 velas de 15m = 8h)
                if step >= max_bars:
                    exit_price = c_close * (1.0 - slippage_pct if sig.action == SignalAction.BUY else 1.0 + slippage_pct)
                    exit_reason = "TIME_LIMIT"
                    exit_idx = future_idx
                    break

            # Cálculo de retorno
            if sig.action == SignalAction.BUY:
                raw_return = (exit_price - entry_price) / entry_price
            else:
                raw_return = (entry_price - exit_price) / entry_price

            # Descontar comisiones de entrada y salida (ej. 2 * 0.075%)
            total_fees = fee_pct * 2.0
            net_return = raw_return - total_fees

            trade = Trade(
                symbol=symbol,
                action=sig.action,
                entry_time=sig.timestamp,
                exit_time=df.index[exit_idx],
                entry_price=entry_price,
                exit_price=exit_price,
                exit_reason=exit_reason,
                return_pct=raw_return * 100.0,
                net_return_pct=net_return * 100.0,
                holding_bars=bars_held,
                signal_score=sig.score,
                signal_reasons=sig.reasons,
            )
            trades.append(trade)
            last_exit_idx = exit_idx

        # Estadísticas consolidadas
        total_trades = len(trades)
        if total_trades == 0:
            return BacktestResult(
                symbol=symbol,
                total_candles=len(df),
                start_date=df.index[0],
                end_date=df.index[-1],
                total_signals=len(signals),
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                avg_return_pct=0.0,
                cumulative_return_pct=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                avg_holding_bars=0.0,
                tp_hits=0,
                sl_hits=0,
                time_limit_exits=0,
            )

        winning = [t for t in trades if t.net_return_pct > 0]
        losing = [t for t in trades if t.net_return_pct <= 0]
        tp_count = sum(1 for t in trades if t.exit_reason == "TAKE_PROFIT")
        sl_count = sum(1 for t in trades if t.exit_reason == "STOP_LOSS")
        time_count = sum(1 for t in trades if t.exit_reason == "TIME_LIMIT")

        gross_gains = sum(t.net_return_pct for t in winning)
        gross_losses = abs(sum(t.net_return_pct for t in losing))
        profit_factor = (gross_gains / gross_losses) if gross_losses > 0 else (99.0 if gross_gains > 0 else 0.0)

        win_rate = (len(winning) / total_trades) * 100.0
        avg_ret = float(np.mean([t.net_return_pct for t in trades]))
        cum_ret = float(sum(t.net_return_pct for t in trades))
        avg_bars = float(np.mean([t.holding_bars for t in trades]))

        # Cálculo de Maximum Drawdown
        equity_curve = [0.0]
        current_eq = 0.0
        for t in trades:
            current_eq += t.net_return_pct
            equity_curve.append(current_eq)

        eq_series = pd.Series(equity_curve)
        peak = eq_series.cummax()
        drawdown = peak - eq_series
        max_dd = float(drawdown.max())

        # Desglose de rendimiento por categoría de indicador
        ind_stats: Dict[str, Dict[str, float]] = {}
        for t in trades:
            for reason in t.signal_reasons:
                if "RSI" in reason:
                    key = "RSI Sobreventa/Sobrecompra"
                elif "EMA" in reason:
                    key = "Cruce de Medias EMA"
                elif "MACD" in reason:
                    key = "Cruce MACD / Señal"
                elif "Bollinger" in reason:
                    key = "Bandas de Bollinger"
                elif "Volumen anómalo" in reason:
                    key = "Volumen Anómalo (>1.5x)"
                else:
                    key = reason.split("(")[0].strip()

                if key not in ind_stats:
                    ind_stats[key] = {"trades": 0, "wins": 0, "total_return": 0.0}
                ind_stats[key]["trades"] += 1
                if t.net_return_pct > 0:
                    ind_stats[key]["wins"] += 1
                ind_stats[key]["total_return"] += t.net_return_pct

        for key, s in ind_stats.items():
            s["win_rate"] = (s["wins"] / s["trades"]) * 100.0 if s["trades"] > 0 else 0.0
            s["avg_return"] = s["total_return"] / s["trades"] if s["trades"] > 0 else 0.0

        return BacktestResult(
            symbol=symbol,
            total_candles=len(df),
            start_date=df.index[0],
            end_date=df.index[-1],
            total_signals=len(signals),
            total_trades=total_trades,
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate_pct=win_rate,
            avg_return_pct=avg_ret,
            cumulative_return_pct=cum_ret,
            profit_factor=profit_factor,
            max_drawdown_pct=max_dd,
            avg_holding_bars=avg_bars,
            tp_hits=tp_count,
            sl_hits=sl_count,
            time_limit_exits=time_count,
            trades=trades,
            indicator_stats=ind_stats,
        )
