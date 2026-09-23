"""Punto de entrada para la ejecución en vivo del sistema de señales y simulación.

Uso:
    python scripts/run_live.py           # Inicia el scheduler continuo con APScheduler
    python scripts/run_live.py --once    # Ejecuta un único ciclo de análisis inmediato y finaliza
"""
import argparse
import logging
import sys
import time
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


def main():
    parser = argparse.ArgumentParser(description="TradIA - Servicio de Señales y Simulación (GitHub Actions Cron / Local)")
    parser.add_argument("--loop", action="store_true", help="Ejecutar en bucle continuo con APScheduler (modo demonio)")
    parser.add_argument("--once", action="store_true", help="Ejecutar un ciclo único y finalizar (comportamiento por defecto)")
    parser.add_argument("--reset-wallet", action="store_true", help="Reinicia el wallet ficticio a su saldo inicial de 10.0€")
    args = parser.parse_args()

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
    print(f"• Cierre forzado:     {config.schedule.force_close_time} (Liquidación de posiciones intradía)")
    print(f"• Modo Simulación:    {'ACTIVO' if config.simulation.enabled else 'DESACTIVADO'}")
    portfolio = service.wallet.get_portfolio_status()
    print(f"• Balance Wallet:     ${portfolio['total_equity']:,.2f} USDT (Efectivo: ${portfolio['cash_balance']:,.2f})")
    print(f"• Colchón Activo:     {'SÍ (6.00€ protegidos)' if portfolio['floor_activated'] else 'NO (acumulando hasta 12.00€)'}")
    print("=" * 76)

    # Si se especifica --loop, ejecutar como demonio continuo
    if args.loop:
        logger.info("Modo continuo con APScheduler activado (--loop)...")
        service.start_scheduler()
        service.run_analysis_cycle()
        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            logger.info("\nDetención solicitada por el usuario. Cerrando scheduler...")
            service.stop_scheduler()
            print("TradIA detenido limpiamente.")
        return

    # Comportamiento por defecto (ideal para GitHub Actions): un único ciclo y salir
    logger.info("Ejecutando ciclo de análisis programado...")
    signals = service.run_analysis_cycle()
    logger.info(f"Ciclo finalizado con éxito. Señales detectadas: {len(signals)}")


if __name__ == "__main__":
    main()
