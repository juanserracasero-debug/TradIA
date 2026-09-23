"""Pruebas unitarias para el sistema agnóstico de notificaciones (EmailNotifier y WhatsAppNotifier)."""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from src.config import AppConfig, NotificationsConfig
from src.database.db import DatabaseManager
from src.notifications import EmailNotifier, WhatsAppNotifier, get_notifier
from src.notifications.base import Notifier


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


if __name__ == "__main__":
    unittest.main()
