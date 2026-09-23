"""Pruebas unitarias para el gestor horario de Madrid, el colchón dinámico y el modo simulación."""
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.config import ScheduleConfig, SimulationConfig
from src.database.db import DatabaseManager
from src.scheduler.time_manager import MadridTimeManager
from src.simulation.reporter import SimulationReporter
from src.simulation.wallet import SimulatedWallet


class TestMadridTimeManager(unittest.TestCase):

    def setUp(self):
        self.time_mgr = MadridTimeManager()
        self.tz = ZoneInfo("Europe/Madrid")

    def test_trading_hours_morning(self):
        dt = datetime(2026, 9, 22, 10, 30, tzinfo=self.tz)
        self.assertTrue(self.time_mgr.is_trading_hour(dt))
        self.assertEqual(self.time_mgr.get_session_name(dt), "morning")

    def test_trading_hours_evening(self):
        dt = datetime(2026, 9, 22, 21, 15, tzinfo=self.tz)
        self.assertTrue(self.time_mgr.is_trading_hour(dt))
        self.assertEqual(self.time_mgr.get_session_name(dt), "evening")

    def test_resting_hours(self):
        dt_afternoon = datetime(2026, 9, 22, 18, 0, tzinfo=self.tz)
        self.assertFalse(self.time_mgr.is_trading_hour(dt_afternoon))

        dt_night = datetime(2026, 9, 22, 3, 0, tzinfo=self.tz)
        self.assertFalse(self.time_mgr.is_trading_hour(dt_night))

    def test_forced_close_detection(self):
        dt_close = datetime(2026, 9, 22, 22, 45, tzinfo=self.tz)
        self.assertTrue(self.time_mgr.is_forced_close_time(dt_close))

    def test_daily_report_detection(self):
        dt_rep = datetime(2026, 9, 22, 23, 0, tzinfo=self.tz)
        self.assertTrue(self.time_mgr.is_daily_report_time(dt_rep))

    def test_extended_hours_active_and_automatic_reversion(self):
        """Prueba que el horario extendido se active SOLO hasta 2026-09-24T08:30 y luego revierta solo."""
        sched_cfg = ScheduleConfig(
            extended_hours_until="2026-09-24T08:30:00+02:00",
        )
        time_mgr = MadridTimeManager(sched_cfg)

        # 1. Hoy de noche (ej. 23:30 del 23 de septiembre): horario activo
        dt_tonight = datetime(2026, 9, 23, 23, 30, tzinfo=self.tz)
        self.assertTrue(time_mgr.is_extended_hours_active(dt_tonight))
        self.assertTrue(time_mgr.is_trading_hour(dt_tonight))

        # 2. Madrugada (ej. 03:15 del 24 de septiembre): horario activo por excepción
        dt_early_morning = datetime(2026, 9, 24, 3, 15, tzinfo=self.tz)
        self.assertTrue(time_mgr.is_extended_hours_active(dt_early_morning))
        self.assertTrue(time_mgr.is_trading_hour(dt_early_morning))
        self.assertEqual(time_mgr.get_session_name(dt_early_morning), "extended")

        # 3. Cierre forzado de las 22:45 de hoy (23 de septiembre): DESACTIVADO
        dt_close_tonight = datetime(2026, 9, 23, 22, 45, tzinfo=self.tz)
        self.assertFalse(time_mgr.is_forced_close_time(dt_close_tonight))

        # 4. Justo antes del límite (08:29 del 24 de septiembre): activo
        dt_before_limit = datetime(2026, 9, 24, 8, 29, tzinfo=self.tz)
        self.assertTrue(time_mgr.is_extended_hours_active(dt_before_limit))
        self.assertTrue(time_mgr.is_trading_hour(dt_before_limit))

        # 5. En el límite exacto (08:30 del 24 de septiembre): excepción finaliza
        dt_at_limit = datetime(2026, 9, 24, 8, 30, tzinfo=self.tz)
        self.assertFalse(time_mgr.is_extended_hours_active(dt_at_limit))
        # Pero es trading hour por inicio de sesión normal de mañana (08:30)
        self.assertTrue(time_mgr.is_trading_hour(dt_at_limit))
        self.assertEqual(time_mgr.get_session_name(dt_at_limit), "morning")

        # 6. Reversión automática por la tarde (18:00 del 24 de septiembre): en reposo normal
        dt_afternoon_tmr = datetime(2026, 9, 24, 18, 0, tzinfo=self.tz)
        self.assertFalse(time_mgr.is_extended_hours_active(dt_afternoon_tmr))
        self.assertFalse(time_mgr.is_trading_hour(dt_afternoon_tmr))

        # 7. Cierre forzado de mañana por la noche (22:45 del 24 de septiembre): REACTIVADO AUTOMÁTICAMENTE
        dt_close_tomorrow = datetime(2026, 9, 24, 22, 45, tzinfo=self.tz)
        self.assertFalse(time_mgr.is_extended_hours_active(dt_close_tomorrow))
        self.assertTrue(time_mgr.is_forced_close_time(dt_close_tomorrow))


class TestSimulatedWalletDynamicFloor(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_sim.db")
        self.db = DatabaseManager(db_path=self.db_path)
        self.config = SimulationConfig(
            enabled=True,
            initial_balance_usdt=10.0,
            position_size_pct=100.0,
            reserve_floor_eur=6.0,
            min_order_size_eur=6.0,
            floor_activation_threshold_eur=12.0,
            fee_pct=0.1,
            slippage_pct=0.05,
        )
        self.circuit_breaker_triggered = False

        def cb(reason, bal, fl):
            self.circuit_breaker_triggered = True

        self.wallet = SimulatedWallet(self.db, self.config, on_circuit_breaker=cb)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initial_state_and_first_buy(self):
        status = self.wallet.get_portfolio_status()
        self.assertEqual(status["cash_balance"], 10.0)
        self.assertFalse(status["floor_activated"])

        now = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
        buy_res = self.wallet.open_simulated_buy("BTC/USDT", 50000.0, now, ["Señal"])
        self.assertIsNotNone(buy_res)
        # Invertido: 100% de 10€ (ajustado por comisión)
        self.assertAlmostEqual(buy_res["position_size_usdt"], 9.99, places=1)

        # Regla de exclusividad global: intentar abrir ETH mientras BTC está abierta -> debe rechazarse
        buy_eth = self.wallet.open_simulated_buy("ETH/USDT", 2500.0, now, ["Señal ETH"])
        self.assertIsNone(buy_eth)

    def test_dynamic_floor_activation_at_12_eur(self):
        now = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
        # Simular que el wallet tiene 12.50€
        self.db.update_wallet_cash(12.50)

        buy_res = self.wallet.open_simulated_buy("BTC/USDT", 50000.0, now, ["Señal 12€"])
        self.assertIsNotNone(buy_res)
        self.assertTrue(buy_res["floor_activated"])

        # Capital operable debe ser: 12.50 - 6.0 = 6.50€
        self.assertAlmostEqual(buy_res["capital_operable"], 6.50, places=2)

    def test_circuit_breaker_when_margin_exhausted(self):
        now = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
        # Activar el colchón manualmente
        self.db.set_floor_activated(True)
        # Saldo cae a 11.00€ (< 12.00€ necesario para colchón 6€ + orden mín 6€)
        self.db.update_wallet_cash(11.00)

        buy_res = self.wallet.open_simulated_buy("BTC/USDT", 50000.0, now, ["Señal riesgo"])
        self.assertIsNone(buy_res)
        self.assertTrue(self.circuit_breaker_triggered)

        # Comprobar que en base de datos quedó marcado como is_halted
        wallet = self.db.get_wallet()
        self.assertEqual(wallet["is_halted"], 1)

    def test_html_and_csv_report_generation(self):
        now = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
        self.wallet.open_simulated_buy("BTC/USDT", 50000.0, now, ["RSI en sobreventa"])
        self.wallet.close_simulated_position("BTC/USDT", 51000.0, now, ["TP"])

        reports_dir = os.path.join(self.temp_dir, "reports")
        reporter = SimulationReporter(self.db, reports_dir=reports_dir)
        daily = reporter.generate_daily_report(date_target="2026-09-22")

        self.assertIn("RESUMEN DIARIO", daily["message_text"])
        html_file = os.path.join(reports_dir, "daily_report_2026-09-22.html")
        csv_file = os.path.join(reports_dir, "daily_trades_2026-09-22.csv")
        self.assertTrue(os.path.exists(html_file))
        self.assertTrue(os.path.exists(csv_file))

        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("BTC/USDT", content)
            self.assertIn("Reporte Diario de Trading", content)


class TestBotControlDatabase(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_bot_ctrl.db")
        self.db = DatabaseManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_bot_control(self):
        ctrl = self.db.get_bot_control()
        self.assertEqual(ctrl["status"], "active")
        self.assertIsNone(ctrl["resume_at"])
        self.assertEqual(ctrl["trading_window_1_start"], "08:30")
        self.assertEqual(ctrl["trading_window_1_end"], "17:30")

    def test_update_bot_control_pause_and_resume(self):
        # Pausar temporalmente
        self.db.update_bot_control(status="paused_until", resume_at="2026-09-24T12:00:00+02:00")
        ctrl = self.db.get_bot_control()
        self.assertEqual(ctrl["status"], "paused_until")
        self.assertEqual(ctrl["resume_at"], "2026-09-24T12:00:00+02:00")

        # Pausar indefinidamente
        self.db.update_bot_control(status="paused_indefinite")
        ctrl2 = self.db.get_bot_control()
        self.assertEqual(ctrl2["status"], "paused_indefinite")

        # Reanudar (status active limpia resume_at)
        self.db.update_bot_control(status="active")
        ctrl3 = self.db.get_bot_control()
        self.assertEqual(ctrl3["status"], "active")
        self.assertIsNone(ctrl3["resume_at"])

    def test_update_trading_windows(self):
        self.db.update_bot_control(
            trading_window_1_start="09:00",
            trading_window_1_end="18:00",
            trading_window_2_start="21:00",
            trading_window_2_end="23:30",
        )
        ctrl = self.db.get_bot_control()
        self.assertEqual(ctrl["trading_window_1_start"], "09:00")
        self.assertEqual(ctrl["trading_window_1_end"], "18:00")
        self.assertEqual(ctrl["trading_window_2_start"], "21:00")
        self.assertEqual(ctrl["trading_window_2_end"], "23:30")


if __name__ == "__main__":
    unittest.main()
