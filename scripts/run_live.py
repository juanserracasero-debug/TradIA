"""Punto de entrada para la ejecución en vivo del sistema de señales y simulación.

Uso:
    python scripts/run_live.py           # Inicia el scheduler continuo con APScheduler
    python scripts/run_live.py --once    # Ejecuta un único ciclo de análisis inmediato y finaliza
"""
import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Configurar stdout y stderr en utf-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from src.config import load_config
from src.database.db import DatabaseManager
from src.scheduler.service import LiveTradingService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TradIA-Live")


def check_and_apply_bot_control(db: DatabaseManager, service: LiveTradingService) -> tuple[bool, str]:
    """Consulta la tabla bot_control y aplica el estado operativo y franjas horarias.

    Retorna:
        tuple[bool, str]: (should_proceed, state_description)
    """
    control = db.get_bot_control()
    status = control.get("status", "active")
    resume_at_raw = control.get("resume_at")
    madrid_now = service.time_manager.to_madrid()

    # 1. Pausa indefinida
    if status == "paused_indefinite":
        msg = "Bot pausado indefinidamente desde el dashboard"
        logger.info(f"⏸️ [PAUSA INDEFINIDA] {msg}.")
        return False, msg

    # 2. Pausa temporal con fecha/hora de reanudación
    if status == "paused_until" and resume_at_raw:
        try:
            resume_dt = datetime.fromisoformat(str(resume_at_raw))
            if resume_dt.tzinfo is None:
                resume_dt = resume_dt.replace(tzinfo=timezone.utc)
            resume_madrid = service.time_manager.to_madrid(resume_dt)

            if madrid_now < resume_madrid:
                resume_str = resume_madrid.strftime("%d/%m %H:%M")
                msg = f"Bot pausado temporalmente hasta {resume_str}"
                logger.info(f"⏸️ [PAUSA TEMPORAL] {msg} (hora actual: {madrid_now.strftime('%H:%M')}).")
                return False, msg
            else:
                logger.info(
                    f"▶️ [PAUSA EXPIRADA] Límite {resume_madrid.strftime('%H:%M')} superado. "
                    f"Reanudando operaciones a 'active' automáticamente."
                )
                db.update_bot_control(status="active", resume_at=None)
                status = "active"
        except Exception as e:
            logger.warning(f"Error parseando resume_at ({resume_at_raw}): {e}. Continuando como activo.")
            status = "active"

    # 3. Estado activo: aplicar franjas horarias dinámicas de bot_control
    w1_s = control.get("trading_window_1_start")
    w1_e = control.get("trading_window_1_end")
    w2_s = control.get("trading_window_2_start")
    w2_e = control.get("trading_window_2_end")
    service.time_manager.update_trading_windows(
        morning_start=w1_s,
        morning_end=w1_e,
        evening_start=w2_s,
        evening_end=w2_e,
    )
    return True, "active"


def main():
    parser = argparse.ArgumentParser(description="TradIA - Servicio de Señales y Simulación (GitHub Actions Cron / Local)")
    parser.add_argument("--loop", action="store_true", help="Ejecutar en bucle continuo con APScheduler (modo demonio)")
    parser.add_argument("--once", action="store_true", help="Ejecutar un ciclo único y finalizar (comportamiento por defecto)")
    parser.add_argument("--reset-wallet", action="store_true", help="Reinicia el wallet ficticio a su saldo inicial de 10.0€")
    args = parser.parse_args()

    service = None
    try:
        config = load_config()
        db = DatabaseManager()

        if args.reset_wallet:
            db.init_wallet(initial_balance=config.simulation.initial_balance_usdt, force_reset=True)
            print(f"🔄 Wallet simulado reiniciado a {config.simulation.initial_balance_usdt:.2f}€ con éxito.")
            if not args.once and not args.loop:
                return

        service = LiveTradingService(config=config, db_manager=db)
        db_mode = "SUPABASE (Nube)" if getattr(db, "is_supabase", False) else "SQLITE (Local)"

        print("=" * 76)
        print(" 🚀 TRADIA — SISTEMA DE SEÑALES DE DAY-TRADING (MODO SIMULACIÓN)")
        print("=" * 76)
        print(f"• Base de Datos:      {db_mode}")
        print(f"• Exchange:           {config.market.exchange.upper()} (Endpoints 100% Públicos)")
        print(f"• Pares activos:      {', '.join(config.market.symbols)}")
        print(f"• Timeframe:          {config.market.timeframe}")
        print(f"• Zona horaria:       {config.schedule.timezone}")
        print(f"• Franjas de trading: {config.schedule.morning_window.start}–{config.schedule.morning_window.end} y {config.schedule.evening_window.start}–{config.schedule.evening_window.end}")
        if service.time_manager.is_extended_hours_active():
            print(f"• Horario Extendido:  ACTIVO hasta {config.schedule.extended_hours_until} (Trading continuo 24h)")
        print(f"• Cierre forzado:     {config.schedule.force_close_time} (Liquidación de posiciones intradía)")
        print(f"• Modo Simulación:    {'ACTIVO' if config.simulation.enabled else 'DESACTIVADO'}")
        print(f"• Heartbeat Emails:   {'ACTIVO (cada 15m)' if config.notifications.heartbeat_emails else 'DESACTIVADO'}")
        portfolio = service.wallet.get_portfolio_status()
        print(f"• Balance Wallet:     ${portfolio['total_equity']:,.2f} USDT (Efectivo: ${portfolio['cash_balance']:,.2f})")
        print(f"• Colchón Activo:     {'SÍ (6.00€ protegidos)' if portfolio['floor_activated'] else 'NO (acumulando hasta 12.00€)'}")
        print("=" * 76)

        # 1. Comprobar control operativo remoto (tabla bot_control en Supabase / SQLite)
        should_run, control_state = check_and_apply_bot_control(db, service)
        if not should_run:
            madrid_now = service.time_manager.to_madrid()
            hora_str = madrid_now.strftime("%H:%M")
            total_bal = portfolio.get("total_equity", portfolio.get("cash_balance", 0.0))
            headline = f"⏸️ TradIA — Ciclo OK [{hora_str}]. {control_state}. Balance: {total_bal:.2f}€"
            body = (
                f"{headline}\n\n"
                f"📋 Estado del ciclo:\n"
                f"• Hora Madrid:   {madrid_now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                f"• Estado Bot:    PAUSADO ({control_state})\n"
                f"• Base de Datos: {db_mode}\n"
                f"• Balance Total: {total_bal:.2f}€\n\n"
                f"💡 El bot no analizará mercado ni enviará señales hasta su reactivación desde el Dashboard."
            )
            if config.notifications.heartbeat_emails:
                try:
                    service.notifier.send_alert(body, subject=headline)
                except Exception as mail_err:
                    logger.warning(f"No se pudo enviar heartbeat de pausa: {mail_err}")
            logger.info(f"Ciclo finalizado en modo pausa: '{control_state}'")
            return

        # 2. Si se especifica --loop, ejecutar como demonio continuo
        if args.loop:
            logger.info("Modo continuo con APScheduler activado (--loop)...")
            service.start_scheduler()
            sigs = service.run_analysis_cycle()
            service.send_heartbeat(signals=sigs)
            try:
                while True:
                    time.sleep(1)
            except (KeyboardInterrupt, SystemExit):
                logger.info("\nDetención solicitada por el usuario. Cerrando scheduler...")
                service.stop_scheduler()
                print("TradIA detenido limpiamente.")
            return

        # 3. Comportamiento por defecto (ideal para GitHub Actions): un único ciclo y salir
        logger.info("Ejecutando ciclo de análisis programado...")
        signals = service.run_analysis_cycle()
        logger.info(f"Ciclo finalizado con éxito. Señales detectadas: {len(signals)}")
        service.send_heartbeat(signals=signals)

    except Exception as exc:
        logger.error(f"Fallo crítico en la ejecución del ciclo: {exc}", exc_info=True)
        if service:
            service.send_error_alert(exc)
        else:
            try:
                from src.notifications import get_notifier
                fallback_cfg = load_config()
                fallback_notif = get_notifier(fallback_cfg)
                from datetime import datetime
                from zoneinfo import ZoneInfo
                now_str = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%H:%M")
                fallback_notif.send_alert(
                    f"❌ ALERTA CRÍTICA TRADIA — Error en ciclo [{now_str}]:\n{exc}",
                    subject=f"❌ TradIA — Error en ciclo [{now_str}]",
                )
            except Exception as mail_err:
                logger.error(f"Error adicional al intentar alertar por fallback: {mail_err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
