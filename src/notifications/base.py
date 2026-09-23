"""Interfaz base para canales de notificación (Email, WhatsApp, etc.).

Aplica el principio de Inversión de Dependencias (SOLID):
El resto del sistema de trading interactúa únicamente con Notifier,
permaneciendo completamente agnóstico del canal de transporte subyacente.
"""
from abc import ABC, abstractmethod
from typing import Optional


class Notifier(ABC):
    """Interfaz abstracta para el envío de alertas y reportes periódicos."""

    @abstractmethod
    def send_alert(self, message: str, subject: Optional[str] = None) -> bool:
        """Envía una alerta puntual de trading (apertura, cierre, circuit breaker).

        Args:
            message: Contenido textual de la alerta.
            subject: Asunto breve opcional (útil para Email; si no se pasa, se infiere del mensaje).

        Returns:
            bool: True si el envío fue exitoso (o simulado correctamente), False si falló.
        """
        pass

    @abstractmethod
    def send_daily_report(
        self,
        summary_text: str,
        html_content: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> bool:
        """Envía el informe de fin de jornada.

        Args:
            summary_text: Resumen conciso para el cuerpo del mensaje.
            html_content: Contenido HTML opcional para emails enriquecidos.
            file_path: Ruta al archivo local generado (ej. HTML/CSV) para adjuntar.

        Returns:
            bool: True si el envío fue exitoso, False en caso de error.
        """
        pass
