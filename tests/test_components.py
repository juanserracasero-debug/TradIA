import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

from src.config import load_config
from src.database.db import DatabaseManager
from src.indicators.technical import (
    calc_bollinger_bands,
    calc_ema,
    calc_macd,
    calc_rsi,
    calc_volume_metrics,
    compute_all_indicators,
)
from src.notifications.base import Notifier
from src.scheduler.service import LiveTradingService
from src.strategy.rules import RuleEngine, SignalAction, SignalEvent
from src.backtest.engine import BacktestEngine


def create_mock_ohlcv(n_candles: int = 100) -> pd.DataFrame:
    """Genera datos OHLCV sintéticos para pruebas deterministas."""
    dates = pd.date_range("2026-01-01 00:00", periods=n_candles, freq="15min", tz="UTC")
    base = 50000.0
    drift = np.linspace(-500, 1000, n_candles)
    sine = 200 * np.sin(np.linspace(0, 4 * np.pi, n_candles))
    close = base + drift + sine

    high = close + np.random.uniform(10, 50, n_candles)
    low = close - np.random.uniform(10, 50, n_candles)
    open_p = close + np.random.uniform(-20, 20, n_candles)
    volume = np.random.uniform(50, 200, n_candles)

    df = pd.DataFrame(
        {
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )
    return df


class TestTradIACore(unittest.TestCase):

    def test_config_loading(self):
        """Valida la carga correcta de la configuración."""
        cfg = load_config()
        self.assertEqual(cfg.market.exchange, "binance")
        self.assertIn("BTC/USDT", cfg.market.symbols)
        self.assertEqual(cfg.market.timeframe, "15m")
        self.assertEqual(cfg.indicators.rsi.period, 14)
        self.assertEqual(cfg.indicators.rsi.oversold, 30.0)
        self.assertEqual(cfg.backtest.take_profit_pct, 1.5)
        self.assertEqual(cfg.backtest.stop_loss_pct, 0.75)

    def test_indicators_math(self):
        """Valida que los indicadores calculen valores coherentes."""
        df = create_mock_ohlcv(100)
        res = compute_all_indicators(df)

        expected_cols = [
            "rsi", "ema_fast", "ema_slow", "ema_cross_bullish", "ema_cross_bearish",
            "macd", "macd_signal", "macd_hist", "bb_middle", "bb_upper", "bb_lower",
            "bb_pct_b", "vol_ratio", "vol_surge",
        ]
        for col in expected_cols:
            self.assertIn(col, res.columns, f"Falta la columna {col}")

        # RSI debe estar entre 0 y 100
        valid_rsi = res["rsi"].dropna()
        self.assertTrue((valid_rsi >= 0).all() and (valid_rsi <= 100).all())

        # Bandas de Bollinger: upper >= middle >= lower
        valid_bb = res.dropna()
        self.assertTrue((valid_bb["bb_upper"] >= valid_bb["bb_middle"]).all())
        self.assertTrue((valid_bb["bb_middle"] >= valid_bb["bb_lower"]).all())

    def test_rule_engine_evaluation(self):
        """Valida que el motor de reglas genere señales explicables."""
        cfg = load_config()
        rule_engine = RuleEngine(config=cfg)

        mock_row = pd.Series({
            "close": 50000.0,
            "rsi": 25.0,                 # Sobreventa -> +2 pts
            "ema_fast": 50100.0,
            "ema_slow": 50000.0,
            "ema_cross_bullish": True,   # Cruce alcista -> +2 pts
            "ema_cross_bearish": False,
            "macd": 10.0,
            "macd_signal": 5.0,
            "macd_hist": 5.0,
            "macd_cross_bullish": True,  # Cruce MACD -> +1 pt
            "macd_cross_bearish": False,
            "bb_pct_b": 0.02,            # Rebote banda inferior -> +1 pt
            "vol_ratio": 2.2,
            "vol_surge": True,           # Volumen anómalo -> +1 pt
        })

        sig = rule_engine.evaluate_candle(mock_row, symbol="BTC/USDT", timestamp=datetime.now(timezone.utc))
        self.assertIsNotNone(sig)
        self.assertEqual(sig.action, SignalAction.BUY)
        self.assertGreaterEqual(sig.score, 3)
        self.assertGreaterEqual(len(sig.reasons), 3)
        self.assertIn("RSI", sig.reasons[0])
        # Verificar formateo de mensaje
        msg = sig.to_alert_message()
        self.assertIn("🟢 COMPRA BTC/USDT", msg)

    def test_backtest_engine_simulation(self):
        """Valida la simulación de trades con TP y SL."""
        cfg = load_config()
        cfg.backtest.take_profit_pct = 1.0
        cfg.backtest.stop_loss_pct = 0.5
        cfg.backtest.fee_pct = 0.0
        cfg.backtest.slippage_pct = 0.0

        df = create_mock_ohlcv(50)
        df_with_ind = compute_all_indicators(df, cfg.indicators)
        rule_engine = RuleEngine(config=cfg)
        signals = rule_engine.evaluate_dataframe(df_with_ind, symbol="BTC/USDT")

        bt_engine = BacktestEngine(config=cfg)
        result = bt_engine.run(df_with_ind, signals, symbol="BTC/USDT", allow_short=False)

        self.assertEqual(result.symbol, "BTC/USDT")
        self.assertEqual(result.total_candles, 50)
        self.assertEqual(result.total_signals, len(signals))
        self.assertLessEqual(result.total_trades, len(signals))
        if result.total_trades > 0:
            self.assertTrue(0.0 <= result.win_rate_pct <= 100.0)


class TestLiveTradingServiceIntegration(unittest.TestCase):
    """Pruebas de integración del ciclo en vivo conectando Scheduler, Wallet y Notificaciones."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_live_int.db")
        self.db = DatabaseManager(db_path=self.db_path)
        self.config = load_config()
        self.notifier = MagicMock(spec=Notifier)
        self.service = LiveTradingService(
            config=self.config,
            db_manager=self.db,
            notifier=self.notifier,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_outside_trading_hours_sleeps(self):
        """Fuera de las franjas de Madrid (ej. 03:00) el bot permanece en reposo."""
        madrid_tz = ZoneInfo("Europe/Madrid")
        night_time = datetime(2026, 9, 23, 3, 0, 0, tzinfo=madrid_tz)
        signals = self.service.run_analysis_cycle(test_datetime=night_time)
        self.assertEqual(signals, [])
        self.notifier.send_alert.assert_not_called()

    def test_live_cycle_buy_and_sell_signals(self):
        """En franja activa (10:00 Madrid), una señal ejecuta orden en wallet y envía alerta."""
        madrid_tz = ZoneInfo("Europe/Madrid")
        trade_time = datetime(2026, 9, 23, 10, 0, 0, tzinfo=madrid_tz)

        # 1. Simular compra
        with patch.object(self.service.fetcher, "fetch_ohlcv", side_effect=lambda symbol, **kwargs: create_mock_ohlcv(60)):
            mock_buy_signal = SignalEvent(
                symbol="BTC/USDT",
                action=SignalAction.BUY,
                price=85000.0,
                score=4,
                reasons=["RSI sobreventa", "Cruce EMA alcista"],
                metrics={},
                timestamp=trade_time,
            )
            with patch.object(self.service.rule_engine, "evaluate_candle", side_effect=lambda candle, symbol, timestamp: mock_buy_signal if symbol == "BTC/USDT" else None):
                signals = self.service.run_analysis_cycle(test_datetime=trade_time)
                self.assertEqual(len(signals), 1)

        # Verificar wallet abierto
        status = self.service.wallet.get_portfolio_status()
        self.assertEqual(status["open_positions_count"], 1)
        self.assertLess(status["cash_balance"], 1.0)

        # Verificar envío de alerta de compra vía Notifier
        self.assertTrue(self.notifier.send_alert.called)
        first_alert = self.notifier.send_alert.call_args_list[0][0][0]
        self.assertIn("COMPRA BTC/USDT", first_alert)

        # 2. Simular venta
        sell_time = datetime(2026, 9, 23, 11, 0, 0, tzinfo=madrid_tz)
        with patch.object(self.service.fetcher, "fetch_ohlcv", side_effect=lambda symbol, **kwargs: create_mock_ohlcv(60)):
            mock_sell_signal = SignalEvent(
                symbol="BTC/USDT",
                action=SignalAction.SELL,
                price=87000.0,
                score=4,
                reasons=["RSI sobrecompra"],
                metrics={},
                timestamp=sell_time,
            )
            with patch.object(self.service.rule_engine, "evaluate_candle", side_effect=lambda candle, symbol, timestamp: mock_sell_signal if symbol == "BTC/USDT" else None):
                self.service.run_analysis_cycle(test_datetime=sell_time)

        status_after = self.service.wallet.get_portfolio_status()
        self.assertEqual(status_after["open_positions_count"], 0)
        self.assertGreater(status_after["cash_balance"], 10.0)

        # Verificar envío de alerta de venta vía Notifier
        last_alert_sell = self.notifier.send_alert.call_args[0][0]
        self.assertIn("VENTA BTC/USDT", last_alert_sell)
        self.assertIn("Resultado:", last_alert_sell)

    def test_live_cycle_forced_close_at_2245(self):
        """A las 22:45 Madrid se liquidan todas las posiciones y se alerta."""
        madrid_tz = ZoneInfo("Europe/Madrid")
        self.db.add_position(
            symbol="BTC/USDT",
            qty=0.0001,
            entry_price=85000.0,
            entry_time=datetime(2026, 9, 23, 21, 0, 0, tzinfo=madrid_tz).isoformat(),
            entry_reasons=["Test"],
            entry_fee=0.01,
            position_cost_usdt=8.5,
        )
        self.assertEqual(len(self.db.get_open_positions()), 1)

        close_time = datetime(2026, 9, 23, 22, 45, 0, tzinfo=madrid_tz)
        with patch.object(self.service.fetcher, "fetch_ohlcv", side_effect=lambda symbol, **kwargs: create_mock_ohlcv(10)):
            signals = self.service.run_analysis_cycle(test_datetime=close_time)

        self.assertEqual(signals, [])
        self.assertEqual(len(self.db.get_open_positions()), 0)
        last_alert = self.notifier.send_alert.call_args[0][0]
        self.assertIn("VENTA BTC/USDT", last_alert)

    def test_live_cycle_daily_report_at_2300(self):
        """A las 23:00 Madrid se genera y envía el reporte diario con archivo adjunto."""
        madrid_tz = ZoneInfo("Europe/Madrid")
        report_time = datetime(2026, 9, 23, 23, 0, 0, tzinfo=madrid_tz)
        self.service.run_analysis_cycle(test_datetime=report_time)
        self.assertTrue(self.notifier.send_daily_report.called)


if __name__ == "__main__":
    unittest.main()
