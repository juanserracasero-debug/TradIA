"""Gestor de tiempo y franjas horarias de Europe/Madrid.

Respeta estrictamente:
- Horario de verano y de invierno (CEST/CET) mediante zoneinfo nativo.
- Franja matutina: 08:30 - 17:30
- Franja nocturna: 20:30 - 23:00
- Cierre forzado intradía: 22:45
- Emisión de reporte diario: 23:00
"""
from datetime import datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from src.config import ScheduleConfig


class MadridTimeManager:
    """Gestiona la comprobación de franjas horarias y eventos de cierre para Madrid."""

    def __init__(self, config: Optional[ScheduleConfig] = None):
        self.config = config or ScheduleConfig()
        self.tz = ZoneInfo(self.config.timezone)

        # Parseo de horas límite
        self.morning_start = self._parse_time(self.config.morning_window.start)
        self.morning_end = self._parse_time(self.config.morning_window.end)
        self.evening_start = self._parse_time(self.config.evening_window.start)
        self.evening_end = self._parse_time(self.config.evening_window.end)
        self.force_close_t = self._parse_time(self.config.force_close_time)
        self.daily_report_t = self._parse_time(self.config.daily_report_time)

    @staticmethod
    def _parse_time(time_str: str) -> time:
        parts = [int(p) for p in time_str.split(":")]
        return time(hour=parts[0], minute=parts[1])

    def to_madrid(self, dt: Optional[datetime] = None) -> datetime:
        """Convierte una fecha/hora dada (o la actual) a la zona horaria de Madrid."""
        if dt is None:
            return datetime.now(self.tz)
        if dt.tzinfo is None:
            # Asumir UTC si es ingenuo
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.tz)

    def is_trading_hour(self, dt: Optional[datetime] = None) -> bool:
        """Determina si la fecha/hora corresponde a una franja de trading activa en Madrid."""
        m_dt = self.to_madrid(dt)
        curr_time = m_dt.time()

        in_morning = self.morning_start <= curr_time <= self.morning_end
        in_evening = self.evening_start <= curr_time <= self.evening_end
        return in_morning or in_evening

    def is_forced_close_time(self, dt: Optional[datetime] = None) -> bool:
        """Identifica si es el momento del cierre forzado (ej. 22:45).
        
        Permite una ventana de tolerancia de +9 minutos para absorber latencias de scheduling en GitHub Actions.
        """
        m_dt = self.to_madrid(dt)
        curr_time = m_dt.time()
        return (curr_time.hour == self.force_close_t.hour and
                self.force_close_t.minute <= curr_time.minute <= self.force_close_t.minute + 9)

    def is_daily_report_time(self, dt: Optional[datetime] = None) -> bool:
        """Identifica si es el momento del reporte diario (ej. 23:00).
        
        Permite una ventana de tolerancia de +9 minutos para absorber latencias de scheduling en GitHub Actions.
        """
        m_dt = self.to_madrid(dt)
        curr_time = m_dt.time()
        return (curr_time.hour == self.daily_report_t.hour and
                self.daily_report_t.minute <= curr_time.minute <= self.daily_report_t.minute + 9)

    def get_session_name(self, dt: Optional[datetime] = None) -> Optional[str]:
        """Devuelve el nombre de la sesión activa ('morning', 'evening' o None)."""
        m_dt = self.to_madrid(dt)
        curr_time = m_dt.time()
        if self.morning_start <= curr_time <= self.morning_end:
            return "morning"
        if self.evening_start <= curr_time <= self.evening_end:
            return "evening"
        return None
