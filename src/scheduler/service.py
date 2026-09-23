"""Servicio de orquestación y scheduler en vivo para TradIA.

Combina:
- Descarga pública de velas en tiempo real.
- Motor de indicadores técnicos deterministas.
- Generación de señales explicables.
- Modo Simulación (Paper Trading con Wallet de 10.0€ y Colchón Dinámico de 6.0€).
- Notificaciones agnósticas (Email por defecto o WhatsApp) mediante la interfaz Notifier.
- Control de franjas horarias Europe/Madrid.
- Cierre forzado de posiciones a las 22:45.
- Informe diario a las 23:00 (resumen + archivo HTML adjunto).
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import AppConfig
from src.data.fetcher import DataFetcher
from src.database.db import DatabaseManager
from src.indicators.technical import compute_all_indicators
from src.notifications import Notifier, get_notifier
from src.scheduler.time_manager import MadridTimeManager
from src.simulation.reporter import SimulationReporter
from src.simulation.wallet import SimulatedWallet
from src.strategy.rules import RuleEngine, SignalAction, SignalEvent

logger = logging.getLogger("TradIA-Service")


class LiveTradingService:
    """Orquestador del ciclo de análisis, simulación y alertas."""

    def __init__(
        self,
        config: AppConfig,
        db_manager: Optional[DatabaseManager] = None,
        notifier: Optional[Notifier] = None,
    ):
        self.config = config
        self.db = db_manager or DatabaseManager()
        self.fetcher = DataFetcher(exchange_id=self.config.market.exchange)
        self.time_manager = MadridTimeManager(self.config.schedule)
        self.rule_engine = RuleEngine(self.config)
        self.notifier = notifier or get_notifier(self.config)

        # Callback de circuit breaker para alertar por el canal configurado
        def handle_circuit_breaker(reason: str, balance: float, floor: float):
            if floor > 0:
                body = (
                    f"🚨 ALERTA TRADIA — Se ha agotado el margen operable por encima del colchón de seguridad.\n"
                    f"• Balance actual: {balance:.2f}€\n"
                    f"• Colchón blindado: {floor:.2f}€\n"
                    f"• Motivo: {reason}\n"
                    f"⚠️ El sistema ha pausado automáticamente nuevas operaciones para tu revisión manual."
                )
            else:
                body = (
                    f"⚠️ ALERTA TRADIA — Balance insuficiente para operar ({balance:.2f}€ < mínimo 6.00€).\n"
                    f"⚠️ El sistema se ha pausado para tu revisión manual."
                )
            self.notifier.send_alert(body, subject="🚨 ALERTA TRADIA: Colchón de seguridad alcanzado")

        self.wallet = SimulatedWallet(
            self.db,
            self.config.simulation,
            on_circuit_breaker=handle_circuit_breaker,
        )
        self.reporter = SimulationReporter(self.db)
        self.scheduler: Optional[BackgroundScheduler] = None

    def run_analysis_cycle(self, test_datetime: Optional[datetime] = None) -> List[SignalEvent]:
        """Ejecuta un ciclo completo de análisis para todos los activos configurados."""
        madrid_now = self.time_manager.to_madrid(test_datetime)
        madrid_str = madrid_now.strftime("%Y-%m-%d %H:%M:%S (%Z)")
        logger.info(f"=== Ciclo de Análisis TradIA | Hora Madrid: {madrid_str} ===")

        # 1. Comprobar evento de CIERRE FORZADO (22:45)
        if self.time_manager.is_forced_close_time(madrid_now):
            logger.warning("🚨 [22:45] Momento de cierre forzado intradía para evitar exposición nocturna.")
            self._handle_forced_closing(madrid_now)
            return []

        # 2. Comprobar evento de INFORME DIARIO (23:00)
        if self.time_manager.is_daily_report_time(madrid_now):
            logger.info("📋 [23:00] Generando reporte diario de trading...")
            report_data = self.reporter.generate_daily_report(date_target=madrid_now.strftime("%Y-%m-%d"))
            try:
                print("\n" + report_data["message_text"] + "\n")
            except Exception:
                safe_txt = report_data["message_text"].encode("ascii", errors="replace").decode("ascii")
                print("\n" + safe_txt + "\n")

            # Leer contenido HTML si existe para enviar por email adjunto
            html_content = None
            html_path = report_data.get("html_path")
            if html_path and Path(html_path).exists():
                try:
                    with open(html_path, "r", encoding="utf-8") as f:
                        html_content = f.read()
                except Exception as e:
                    logger.warning(f"No se pudo leer HTML para envío: {e}")

            self.notifier.send_daily_report(
                summary_text=report_data["message_text"],
                html_content=html_content,
                file_path=html_path,
            )
            return []

        # 3. Comprobar si estamos dentro de las franjas operativas
        if not self.time_manager.is_trading_hour(madrid_now):
            logger.info(
                f"⏸️ [MERCADO EN REPOSO] La hora actual {madrid_now.strftime('%H:%M')} está fuera "
                f"de las franjas activas de Madrid (08:30–17:30 y 20:30–23:00). Bot en reposo."
            )
            return []

        # 4. Análisis de mercado activo
        active_signals: List[SignalEvent] = []

        for symbol in self.config.market.symbols:
            try:
                candles_df = self.fetcher.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=self.config.market.timeframe,
                    limit=100,
                )
                if candles_df.empty or len(candles_df) < 30:
                    continue

                if "timestamp" in candles_df.columns:
                    candles_df = candles_df.set_index("timestamp")
                candles_df = candles_df.sort_index()

                df_with_ind = compute_all_indicators(candles_df, self.config.indicators)
                closed_candle = df_with_ind.iloc[-2]
                closed_ts = df_with_ind.index[-2]

                signal = self.rule_engine.evaluate_candle(closed_candle, symbol=symbol, timestamp=closed_ts)

                if signal:
                    active_signals.append(signal)
                    # Registrar señal en base de datos
                    self.db.record_signal(
                        timestamp=signal.timestamp,
                        symbol=signal.symbol,
                        action=signal.action.value,
                        price=signal.price,
                        score=signal.score,
                        reasons=signal.reasons,
                        metrics=signal.metrics,
                    )

                    time_madrid_str = self.time_manager.to_madrid(signal.timestamp).strftime("%H:%M")

                    # Ejecución en el Wallet Simulado si la simulación está activa
                    if self.config.simulation.enabled:
                        if signal.action == SignalAction.BUY:
                            buy_trade = self.wallet.open_simulated_buy(
                                symbol=symbol,
                                signal_price=signal.price,
                                timestamp=signal.timestamp,
                                reasons=signal.reasons,
                            )
                            if buy_trade:
                                body = (
                                    f"🟢 COMPRA {symbol} — {time_madrid_str}. "
                                    f"Invertidos: {buy_trade['position_size_usdt']:.2f}€ a ${buy_trade['entry_price']:,.2f} {symbol}/USDT."
                                )
                                self.notifier.send_alert(body, subject=f"🟢 COMPRA {symbol}")

                        elif signal.action == SignalAction.SELL:
                            sell_trade = self.wallet.close_simulated_position(
                                symbol=symbol,
                                signal_price=signal.price,
                                timestamp=signal.timestamp,
                                exit_reasons=signal.reasons,
                                exit_tag="SEÑAL_VENTA",
                            )
                            if sell_trade:
                                entry_dt = datetime.fromisoformat(sell_trade["entry_time"])
                                entry_madrid_str = self.time_manager.to_madrid(entry_dt).strftime("%H:%M")
                                sign = "+" if sell_trade["net_pnl_usdt"] >= 0 else ""
                                body = (
                                    f"🔴 VENTA {symbol} — {time_madrid_str}. Recibidos: {sell_trade['net_exit_cash']:.2f}€ "
                                    f"(comprado a ${sell_trade['entry_price']:,.2f}, vendido a ${sell_trade['exit_price']:,.2f} el {entry_madrid_str}). "
                                    f"Resultado: {sign}{sell_trade['net_pnl_usdt']:.2f}€ ({sign}{sell_trade['net_pnl_pct']:.2f}%)."
                                )
                                self.notifier.send_alert(
                                    body,
                                    subject=f"🔴 VENTA {symbol} ({sign}{sell_trade['net_pnl_pct']:.2f}%)",
                                )

            except Exception as e:
                logger.error(f"Error analizando {symbol}: {e}", exc_info=True)

        return active_signals

    def _handle_forced_closing(self, madrid_now: datetime) -> None:
        """Gestiona el cierre forzado a las 22:45 tanto para posiciones simuladas como confirmadas por el usuario."""
        sim_positions = self.db.get_open_positions()
        user_positions = self.db.get_open_user_positions()

        if not sim_positions and not user_positions:
            logger.info("ℹ️ [22:45] No hay posiciones abiertas que requieran cierre.")
            return

        current_prices = {}
        all_symbols = set([p["symbol"] for p in sim_positions] + [p["symbol"] for p in user_positions])
        for sym in all_symbols:
            try:
                recent = self.fetcher.fetch_ohlcv(symbol=sym, timeframe="15m", limit=2)
                if not recent.empty:
                    current_prices[sym] = float(recent.iloc[-1]["close"])
            except Exception as e:
                logger.warning(f"No se pudo obtener precio de cierre para {sym}: {e}")

        time_now_str = madrid_now.strftime("%H:%M")

        # 1. Liquidar en wallet simulado y notificar con formato transparente
        if sim_positions:
            closed = self.wallet.force_close_all(current_prices, timestamp=madrid_now)
            for t in closed:
                entry_dt = datetime.fromisoformat(t["entry_time"])
                entry_str = self.time_manager.to_madrid(entry_dt).strftime("%H:%M")
                sign = "+" if t["net_pnl_usdt"] >= 0 else ""
                body = (
                    f"🔴 VENTA {t['symbol']} — {time_now_str}. Recibidos: {t['net_exit_cash']:.2f}€ "
                    f"(comprado a ${t['entry_price']:,.2f}, vendido a ${t['exit_price']:,.2f} el {entry_str}). "
                    f"Resultado: {sign}{t['net_pnl_usdt']:.2f}€ ({sign}{t['net_pnl_pct']:.2f}%)."
                )
                self.notifier.send_alert(body, subject=f"🔴 VENTA CIERRE 22:45 {t['symbol']}")

        # 2. Alerta al usuario si tiene posiciones reales abiertas
        for pos in user_positions:
            sym = pos["symbol"]
            curr_p = current_prices.get(sym, float(pos["entry_price"]))
            body = (
                f"🔴 VENDE TODO {sym} — Cierre obligatorio de sesión nocturna (22:45 Madrid). "
                f"¡Cierra tu posición manual en el exchange! (Precio aprox: ${curr_p:,.2f})"
            )
            self.notifier.send_alert(body, subject=f"🔴 VENDE TODO {sym} (Cierre 22:45)")

    def start_scheduler(self) -> None:
        """Inicia el planificador con APScheduler respetando Europe/Madrid."""
        tz_str = self.config.schedule.timezone
        self.scheduler = BackgroundScheduler(timezone=tz_str)

        self.scheduler.add_job(
            func=self.run_analysis_cycle,
            trigger=CronTrigger(minute="0,15,30,45", second="5", timezone=tz_str),
            id="candle_analysis_cycle",
            name="Análisis OHLCV cada 15m",
            replace_existing=True,
        )

        self.scheduler.add_job(
            func=lambda: self.run_analysis_cycle(),
            trigger=CronTrigger(hour="22", minute="45", second="0", timezone=tz_str),
            id="forced_close_2245",
            name="Cierre Forzado Intradía 22:45",
            replace_existing=True,
        )

        self.scheduler.add_job(
            func=lambda: self.run_analysis_cycle(),
            trigger=CronTrigger(hour="23", minute="0", second="0", timezone=tz_str),
            id="daily_report_2300",
            name="Reporte Diario 23:00",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info(
            f"✅ Scheduler TradIA en marcha (Zona: {tz_str}). "
            f"Canal de alertas: {self.config.notifications.channel.upper()}. "
            f"Franjas de trading: 08:30-17:30 y 20:30-23:00. Cierre forzado: 22:45."
        )

    def stop_scheduler(self) -> None:
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler detenido.")
