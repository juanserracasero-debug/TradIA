"""Cliente de notificaciones por Correo Electrónico mediante SMTP (Mailjet, Gmail, etc.) con TLS.

Configuración:
- Servidor: SMTP_HOST (por defecto 'in-v3.mailjet.com' si hay claves Mailjet, o 'smtp.gmail.com')
- Puerto: SMTP_PORT (por defecto 587)
- Cifrado: TLS (starttls)
- Credenciales:
    * MAILJET_API_KEY / MAILJET_SECRET_KEY / MAILJET_SENDER
    * O SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_SENDER
    * Compatibilidad legacy con GMAIL_ADDRESS / GMAIL_APP_PASSWORD
- Si no están configuradas, opera en modo MOCK seguro sin interrumpir el bot.
"""
import logging
import os
import smtplib
import ssl
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from src.notifications.base import Notifier

logger = logging.getLogger(__name__)

env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class EmailNotifier(Notifier):
    """Implementación de Notifier para envío mediante SMTP configurable (Mailjet, Gmail, etc.)."""

    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        sender: Optional[str] = None,
        recipient_to: Optional[str] = None,
        # Parámetros de compatibilidad hacia atrás
        gmail_address: Optional[str] = None,
        app_password: Optional[str] = None,
    ):
        # 1. Determinar usuario y contraseña
        self.username = (
            username
            or os.getenv("SMTP_USER")
            or os.getenv("MAILJET_API_KEY")
            or gmail_address
            or os.getenv("GMAIL_ADDRESS")
            or ""
        ).strip()

        raw_pwd = (
            password
            or os.getenv("SMTP_PASSWORD")
            or os.getenv("MAILJET_SECRET_KEY")
            or app_password
            or os.getenv("GMAIL_APP_PASSWORD")
            or ""
        )
        self.password = raw_pwd.replace(" ", "").strip()

        # 2. Determinar remitente (Sender)
        self.sender = (
            sender
            or os.getenv("SMTP_SENDER")
            or os.getenv("MAILJET_SENDER")
            or gmail_address
            or os.getenv("GMAIL_ADDRESS")
            or self.username
        ).strip()

        # 3. Determinar destinatario (Recipient)
        self.recipient_to = (
            recipient_to
            or os.getenv("SMTP_TO")
            or os.getenv("MAILJET_TO")
            or os.getenv("GMAIL_TO")
            or self.sender
        ).strip()

        # 4. Determinar Host y Puerto
        default_host = "in-v3.mailjet.com" if (os.getenv("MAILJET_API_KEY") or "mailjet" in self.username) else "smtp.gmail.com"
        self.smtp_host = (
            smtp_host
            or os.getenv("SMTP_HOST")
            or default_host
        ).strip()

        raw_port = smtp_port or os.getenv("SMTP_PORT") or 587
        try:
            self.smtp_port = int(raw_port)
        except (ValueError, TypeError):
            self.smtp_port = 587

        # 5. Estado de configuración
        self.is_configured = bool(self.username and self.password and self.sender and self.recipient_to)
        if self.is_configured:
            logger.info(
                f"EmailNotifier configurado ({self.sender} -> {self.recipient_to} vía {self.smtp_host}:{self.smtp_port})."
            )
        else:
            logger.info("Credenciales SMTP no detectadas. Operando en modo MOCK.")

    def _infer_subject(self, message: str) -> str:
        """Extrae un asunto conciso a partir de la primera línea del mensaje."""
        first_line = message.strip().splitlines()[0] if message else "Alerta TradIA"
        # Limitar longitud del asunto
        if len(first_line) > 60:
            return first_line[:57] + "..."
        return first_line

    def _send_smtp(
        self,
        subject: str,
        body_text: str,
        html_body: Optional[str] = None,
        attachment_path: Optional[str] = None,
    ) -> bool:
        """Conexión y envío SMTP seguro con TLS."""
        if not self.is_configured:
            logger.info(
                f"\n[EMAIL MOCK SIMULADO]\n"
                f"Host: {self.smtp_host}:{self.smtp_port}\n"
                f"De: {self.sender or 'REMITENTE_NO_CONFIGURADO'}\n"
                f"Para: {self.recipient_to or 'DESTINATARIO_NO_CONFIGURADO'}\n"
                f"Asunto: {subject}\n"
                f"Adjunto: {attachment_path or 'Ninguno'}\n"
                f"Cuerpo:\n{body_text}\n"
            )
            return False

        try:
            msg = MIMEMultipart("mixed")
            msg["From"] = self.sender
            msg["To"] = self.recipient_to
            msg["Subject"] = Header(subject, "utf-8")

            # Cuerpo del mensaje (texto plano o alternativo HTML)
            body_container = MIMEMultipart("alternative")
            part_text = MIMEText(body_text, "plain", "utf-8")
            body_container.attach(part_text)

            if html_body:
                part_html = MIMEText(html_body, "html", "utf-8")
                body_container.attach(part_html)

            msg.attach(body_container)

            # Archivo adjunto (ej. reporte diario HTML)
            if attachment_path:
                att_file = Path(attachment_path)
                if att_file.exists():
                    with open(att_file, "rb") as f:
                        part_file = MIMEApplication(f.read(), Name=att_file.name)
                    part_file["Content-Disposition"] = f'attachment; filename="{att_file.name}"'
                    msg.attach(part_file)
                else:
                    logger.warning(f"Archivo adjunto no encontrado en {attachment_path}")

            # Conexión SMTP TLS
            context = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(self.username, self.password)
                server.sendmail(self.sender, [self.recipient_to], msg.as_string())

            logger.info(f"Email enviado con éxito a {self.recipient_to} vía {self.smtp_host}:{self.smtp_port} | Asunto: {subject}")
            return True

        except Exception as e:
            logger.error(f"Error al enviar email por SMTP ({self.smtp_host}:{self.smtp_port}): {e}", exc_info=True)
            return False

    def send_alert(self, message: str, subject: Optional[str] = None) -> bool:
        """Envía una alerta puntual de trading por email."""
        sub = subject or self._infer_subject(message)
        return self._send_smtp(subject=sub, body_text=message)

    def send_daily_report(
        self,
        summary_text: str,
        html_content: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> bool:
        """Envía el reporte diario con el archivo HTML adjunto."""
        subject = self._infer_subject(summary_text)
        if not subject.startswith("📅"):
            subject = f"📅 {subject}"
        return self._send_smtp(
            subject=subject,
            body_text=summary_text,
            html_body=html_content,
            attachment_path=file_path,
        )
