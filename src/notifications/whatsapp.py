"""Cliente de Notificaciones por WhatsApp mediante Twilio API.

Formatos requeridos para operaciones:
- Al ABRIR: 🟢 COMPRA [activo] — [hora]. Invertidos: [importe]€ a [precio] [activo]/USDT.
- Al CERRAR: 🔴 VENTA [activo] — [hora]. Recibidos: [importe]€ (comprado a [precio_entrada], vendido a [precio_salida] el [hora_entrada]). Resultado: [+/-][profit]€ ([+/-][profit_pct]%).
- Transparencia total en pérdidas, sin maquillar resultados.
- Feedback de indicadores reservado para SQLite/logs, nunca enviado por WhatsApp.
"""
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class WhatsAppNotifier:
    """Cliente para envío de alertas de trading por WhatsApp vía Twilio API."""

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
                logger.info("Cliente de WhatsApp Twilio configurado correctamente.")
            except Exception as e:
                logger.warning(f"Error inicializando cliente Twilio: {e}. Operará en modo MOCK.")
        else:
            logger.info("Credenciales de Twilio no detectadas en .env. Notificador en modo MOCK (consola).")

    def send_message(self, message_body: str) -> bool:
        """Envía un mensaje de WhatsApp o lo registra en modo MOCK si no hay credenciales."""
        if not self.is_configured or self.client is None:
            logger.info(f"\n[WHATSAPP MOCK SIMULADO]\nPara: {self.to_number or 'NO CONFIGURADO'}\n{message_body}\n")
            return False

        try:
            from_wh = self.from_number if self.from_number.startswith("whatsapp:") else f"whatsapp:{self.from_number}"
            to_wh = self.to_number if self.to_number.startswith("whatsapp:") else f"whatsapp:{self.to_number}"

            msg = self.client.messages.create(
                body=message_body,
                from_=from_wh,
                to=to_wh,
            )
            logger.info(f"Mensaje de WhatsApp enviado exitosamente. SID: {msg.sid}")
            return True
        except Exception as e:
            logger.error(f"Error al enviar mensaje por WhatsApp: {e}")
            return False

    def send_trade_open_alert(
        self,
        symbol: str,
        invested_eur: float,
        price: float,
        time_str: str,
    ) -> bool:
        """Formato solicitado:
        🟢 COMPRA [activo] — [hora]. Invertidos: [importe]€ a [precio] [activo]/USDT.
        """
        body = f"🟢 COMPRA {symbol} — {time_str}. Invertidos: {invested_eur:.2f}€ a ${price:,.2f} {symbol}/USDT."
        return self.send_message(body)

    def send_trade_close_alert(
        self,
        symbol: str,
        received_eur: float,
        entry_price: float,
        exit_price: float,
        entry_time_str: str,
        exit_time_str: str,
        profit_eur: float,
        profit_pct: float,
    ) -> bool:
        """Formato solicitado:
        🔴 VENTA [activo] — [hora]. Recibidos: [importe]€ (comprado a [precio_entrada], vendido a [precio_salida] el [hora_entrada]). Resultado: [+/-][profit]€ ([+/-][profit_pct]%).
        """
        # Formato explícito y honesto tanto en ganancias como en pérdidas
        sign = "+" if profit_eur >= 0 else ""
        body = (
            f"🔴 VENTA {symbol} — {exit_time_str}. Recibidos: {received_eur:.2f}€ "
            f"(comprado a ${entry_price:,.2f}, vendido a ${exit_price:,.2f} el {entry_time_str}). "
            f"Resultado: {sign}{profit_eur:.2f}€ ({sign}{profit_pct:.2f}%)."
        )
        return self.send_message(body)

    def send_circuit_breaker_alert(
        self,
        balance_eur: float,
        floor_eur: float,
        reason: str,
    ) -> bool:
        """Envía alerta crítica cuando el margen sobre el colchón se agota o cae por debajo del mínimo."""
        if floor_eur > 0:
            body = (
                f"🚨 ALERTA TRADIA — Se ha agotado el margen operable por encima del colchón de seguridad.\n"
                f"• Balance actual: {balance_eur:.2f}€\n"
                f"• Colchón blindado: {floor_eur:.2f}€\n"
                f"• Motivo: {reason}\n"
                f"⚠️ El sistema ha pausado automáticamente nuevas operaciones para tu revisión manual."
            )
        else:
            body = (
                f"⚠️ ALERTA TRADIA — Balance insuficiente para operar ({balance_eur:.2f}€ < mínimo 6.00€).\n"
                f"⚠️ El sistema se ha pausado para tu revisión manual."
            )
        return self.send_message(body)

    def send_forced_close_alert(
        self,
        symbol: str,
        price: float,
        reason: str = "Cierre obligatorio de sesión nocturna (22:45 Madrid). ¡Cierra tu posición!",
    ) -> bool:
        """Alerta de cierre antes de las 23:00."""
        body = f"🔴 VENDE TODO {symbol} — {reason} (Precio aprox: ${price:,.2f})"
        return self.send_message(body)

    def send_daily_report_alert(self, daily_report_text: str) -> bool:
        """Envía el resumen breve de fin de jornada por WhatsApp."""
        return self.send_message(daily_report_text)
