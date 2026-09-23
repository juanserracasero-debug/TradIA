"""Cliente de notificaciones por WhatsApp (Twilio / Meta Cloud API).

Implementa la interfaz común Notifier para poder conmutarse fácilmente
desde config.yaml cambiando notification_channel a 'whatsapp'.
"""
import logging
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from src.notifications.base import Notifier

logger = logging.getLogger(__name__)

env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class WhatsAppNotifier(Notifier):
    """Implementación de Notifier para envío por WhatsApp."""

    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        from_number: Optional[str] = None,
        to_number: Optional[str] = None,
    ):
        self.account_sid = account_sid or os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or os.getenv("TWILIO_AUTH_TOKEN")
        self.from_number = from_number or os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
        self.to_number = to_number or os.getenv("WHATSAPP_TO")

        self.client = None
        self.is_configured = False

        if self.account_sid and self.auth_token and self.to_number:
            try:
                from twilio.rest import Client
                self.client = Client(self.account_sid, self.auth_token)
                self.is_configured = True
                logger.info("WhatsAppNotifier configurado con cuenta Twilio.")
            except Exception as e:
                logger.warning(f"Error inicializando cliente Twilio: {e}. Operará en modo MOCK.")
        else:
            logger.info("Credenciales de Twilio no detectadas. WhatsAppNotifier en modo MOCK.")

    def send_alert(self, message: str, subject: Optional[str] = None) -> bool:
        """Envía una alerta puntual de trading por WhatsApp."""
        if not self.is_configured or self.client is None:
            logger.info(f"\n[WHATSAPP MOCK]\nPara: {self.to_number or 'NO_CONFIGURADO'}\n{message}\n")
            return False

        try:
            from_wh = self.from_number if self.from_number.startswith("whatsapp:") else f"whatsapp:{self.from_number}"
            to_wh = self.to_number if self.to_number.startswith("whatsapp:") else f"whatsapp:{self.to_number}"

            msg = self.client.messages.create(
                body=message,
                from_=from_wh,
                to=to_wh,
            )
            logger.info(f"Mensaje de WhatsApp enviado exitosamente. SID: {msg.sid}")
            return True
        except Exception as e:
            logger.error(f"Error al enviar mensaje por WhatsApp: {e}")
            return False

    def send_daily_report(
        self,
        summary_text: str,
        html_content: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> bool:
        """Envía el resumen diario por WhatsApp."""
        return self.send_alert(message=summary_text)
