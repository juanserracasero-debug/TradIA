"""Pruebas unitarias para el sistema agnóstico de notificaciones (EmailNotifier y WhatsAppNotifier)."""
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.config import AppConfig, NotificationsConfig, load_config
from src.database.db import DatabaseManager
from src.notifications import EmailNotifier, WhatsAppNotifier, get_notifier
from src.notifications.base import Notifier
from src.scheduler.service import LiveTradingService
from src.scheduler.time_manager import MadridTimeManager
from src.strategy.rules import SignalAction, SignalEvent


class TestNotifiers(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sample_report_path = os.path.join(self.temp_dir, "test_report.html")
        with open(self.sample_report_path, "w", encoding="utf-8") as f:
            f.write("<html><body><h1>Reporte de Prueba</h1></body></html>")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.dict(
        os.environ,
        {
            "MAILJET_API_KEY": "",
            "MAILJET_SECRET_KEY": "",
            "GMAIL_ADDRESS": "",
            "GMAIL_APP_PASSWORD": "",
            "SMTP_USER": "",
            "SMTP_PASSWORD": "",
        },
    )
    def test_email_notifier_mock_mode(self):
        email_notifier = EmailNotifier(
            gmail_address=None,
            app_password=None,
            recipient_to="test@example.com",
        )
        self.assertIsInstance(email_notifier, Notifier)
        self.assertFalse(email_notifier.is_configured)

        # Enviar alerta (debe retornar False en modo mock seguro sin lanzar excepciones)
        res = email_notifier.send_alert(
            message="🟢 COMPRA BTC/USDT — 10:15. Invertidos: 10.00€ a $86,250.00 BTC/USDT.",
            subject="🟢 COMPRA BTC/USDT",
        )
        self.assertFalse(res)

        # Enviar reporte diario con archivo adjunto
        rep_res = email_notifier.send_daily_report(
            summary_text="Resumen diario de trading",
            html_content="<h1>HTML</h1>",
            file_path=self.sample_report_path,
        )
        self.assertFalse(rep_res)

    def test_whatsapp_notifier_mock_mode(self):
        wa_notifier = WhatsAppNotifier(
            account_sid=None,
            auth_token=None,
            from_number="whatsapp:+14155238886",
            to_number="whatsapp:+34600000000",
        )
        self.assertIsInstance(wa_notifier, Notifier)
        self.assertFalse(wa_notifier.is_configured)

        res = wa_notifier.send_alert("🔴 VENTA BTC/USDT — Resultado: +0.15€ (+1.50%).")
        self.assertFalse(res)

        rep_res = wa_notifier.send_daily_report("Resumen WhatsApp", file_path=self.sample_report_path)
        self.assertFalse(rep_res)

    def test_factory_get_notifier(self):
        cfg_email = AppConfig(notifications=NotificationsConfig(channel="email"))
        notif_email = get_notifier(cfg_email)
        self.assertIsInstance(notif_email, EmailNotifier)

        cfg_wa = AppConfig(notifications=NotificationsConfig(channel="whatsapp"))
        notif_wa = get_notifier(cfg_wa)
        self.assertIsInstance(notif_wa, WhatsAppNotifier)

        # Fallback si canal no existe
        cfg_other = AppConfig(notifications=NotificationsConfig(channel="telegram"))
        notif_other = get_notifier(cfg_other)
        self.assertIsInstance(notif_other, EmailNotifier)


class TestUserPositionRegistration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_user_pos.db")
        self.db = DatabaseManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_confirm_and_close_user_position(self):
        self.assertEqual(len(self.db.get_open_user_positions()), 0)

        pos_id = self.db.confirm_user_position(
            symbol="BTC/USDT",
            entry_price=86000.0,
            side="BUY",
            size_usdt=10.0,
            notes="Operación manual",
        )
        self.assertGreater(pos_id, 0)

        open_pos = self.db.get_open_user_positions()
        self.assertEqual(len(open_pos), 1)
        self.assertEqual(open_pos[0]["symbol"], "BTC/USDT")
        self.assertEqual(open_pos[0]["entry_price"], 86000.0)
        self.assertEqual(open_pos[0]["is_open"], 1)

        closed_count = self.db.close_user_position(symbol="BTC/USDT", close_price=87200.0)
        self.assertEqual(closed_count, 1)

        open_pos_after = self.db.get_open_user_positions()
        self.assertEqual(len(open_pos_after), 0)


class TestHeartbeatAndErrorAlerts(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_heartbeat.db")
        self.db = DatabaseManager(db_path=self.db_path)
        self.config = load_config()
        self.config.schedule.extended_hours_until = None
        self.notifier = MagicMock(spec=Notifier)
        self.service = LiveTradingService(
            config=self.config,
            db_manager=self.db,
            notifier=self.notifier,
        )
        self.madrid_tz = ZoneInfo("Europe/Madrid")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_heartbeat_outside_trading_hours(self):
        """Debe enviar confirmación con 'Fuera de horario, en reposo' en horario normal de reposo."""
        night_time = datetime(2026, 9, 25, 3, 15, 0, tzinfo=self.madrid_tz)
        res = self.service.send_heartbeat(signals=[], current_time=night_time)
        self.notifier.send_alert.assert_called_once()
        alert_args = self.notifier.send_alert.call_args[0]
        body = alert_args[0]
        subject = self.notifier.send_alert.call_args[1]["subject"]
        self.assertIn("Ciclo OK [03:15]", subject)
        self.assertIn("Fuera de horario, en reposo", subject)
        self.assertIn("Balance: 10.00€", subject)
        self.assertIn("EN REPOSO", body)

    def test_heartbeat_during_extended_hours_overnight(self):
        """Durante la excepción de horario extendido, la madrugada se reporta como activo sin señales."""
        self.service.config.schedule.extended_hours_until = "2026-09-24T08:30:00+02:00"
        self.service.time_manager = MadridTimeManager(self.service.config.schedule)
        night_time = datetime(2026, 9, 24, 3, 15, 0, tzinfo=self.madrid_tz)
        self.service.send_heartbeat(signals=[], current_time=night_time)
        self.notifier.send_alert.assert_called_once()
        subject = self.notifier.send_alert.call_args[1]["subject"]
        self.assertIn("Ciclo OK [03:15]", subject)
        self.assertIn("Sin señales", subject)
        self.assertIn("Balance: 10.00€", subject)

    def test_heartbeat_inside_trading_hours_no_signals(self):
        """En horario activo sin señales debe reportar 'Sin señales'."""
        trade_time = datetime(2026, 9, 23, 11, 30, 0, tzinfo=self.madrid_tz)
        self.service.send_heartbeat(signals=[], current_time=trade_time)
        self.notifier.send_alert.assert_called_once()
        subject = self.notifier.send_alert.call_args[1]["subject"]
        self.assertIn("Ciclo OK [11:30]", subject)
        self.assertIn("Sin señales", subject)
        self.assertIn("Balance: 10.00€", subject)

    def test_heartbeat_inside_trading_hours_with_signals(self):
        """En horario activo con señales debe detallar las señales encontradas."""
        trade_time = datetime(2026, 9, 23, 11, 45, 0, tzinfo=self.madrid_tz)
        mock_signal = SignalEvent(
            symbol="BTC/USDT",
            action=SignalAction.BUY,
            price=85000.0,
            score=4,
            reasons=["RSI sobreventa"],
            metrics={},
            timestamp=trade_time,
        )
        self.service.send_heartbeat(signals=[mock_signal], current_time=trade_time)
        self.notifier.send_alert.assert_called_once()
        subject = self.notifier.send_alert.call_args[1]["subject"]
        self.assertIn("Ciclo OK [11:45]", subject)
        self.assertIn("Señales procesadas", subject)
        self.assertIn("BTC/USDT BUY", subject)

    def test_heartbeat_disabled_flag(self):
        """Si heartbeat_emails está desactivado, no debe enviar email."""
        self.service.config.notifications.heartbeat_emails = False
        trade_time = datetime(2026, 9, 23, 12, 0, 0, tzinfo=self.madrid_tz)
        res = self.service.send_heartbeat(signals=[], current_time=trade_time)
        self.assertFalse(res)
        self.notifier.send_alert.assert_not_called()

    def test_send_error_alert(self):
        """send_error_alert envía un email de alerta crítica con detalles."""
        trade_time = datetime(2026, 9, 23, 14, 15, 0, tzinfo=self.madrid_tz)
        test_exc = ConnectionResetError("Conexión con exchange reseteada por el peer")
        self.service.send_error_alert(exc=test_exc, current_time=trade_time)
        self.notifier.send_alert.assert_called_once()
        alert_args = self.notifier.send_alert.call_args
        subject = alert_args[1]["subject"]
        body = alert_args[0][0]
        self.assertIn("❌ TradIA — Error en ciclo [14:15]", subject)
        self.assertIn("ConnectionResetError", body)
        self.assertIn("Conexión con exchange reseteada", body)

    @patch.dict(os.environ, {"HEARTBEAT_EMAILS": "false"})
    def test_load_config_heartbeat_env_override(self):
        """Variable de entorno HEARTBEAT_EMAILS=false sobrescribe el YAML."""
        cfg = load_config()
        self.assertFalse(cfg.notifications.heartbeat_emails)


if __name__ == "__main__":
    unittest.main()
