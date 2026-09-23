"""Módulo de notificaciones de TradIA.

Exporta:
- Notifier (interfaz base)
- EmailNotifier (canal Email SMTP / Mailjet)
- WhatsAppNotifier (canal WhatsApp)
- get_notifier (factoría basada en config.yaml)
"""
import logging
from src.notifications.base import Notifier
from src.notifications.email_client import EmailNotifier
from src.notifications.whatsapp_client import WhatsAppNotifier

logger = logging.getLogger(__name__)


def get_notifier(config=None) -> Notifier:
    """Instancia el notificador correspondiente según el canal configurado."""
    channel = "email"
    if config and hasattr(config, "notifications"):
        channel = getattr(config.notifications, "channel", "email").lower()

    if channel == "whatsapp":
        logger.info("Canal de alertas seleccionado: WhatsApp")
        return WhatsAppNotifier()
    elif channel == "email":
        logger.info("Canal de alertas seleccionado: Email (SMTP / Mailjet)")
        return EmailNotifier()
    else:
        logger.warning(f"Canal '{channel}' no reconocido. Seleccionando EmailNotifier por defecto.")
        return EmailNotifier()


__all__ = ["Notifier", "EmailNotifier", "WhatsAppNotifier", "get_notifier"]
