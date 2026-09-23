"""Replay acelerado de Simulación (Paper Trading) sobre datos históricos.

Permite simular el paso de 14 jornadas de trading reales respetando:
- Horarios de Madrid (08:30-17:30 y 20:30-23:00)
- Tamaño del 10% por operación en un wallet de 10.000 USDT
- Comisiones (0.1%) y deslizamiento (0.05%)
- Cierre forzado intradía a las 22:45
- Emisión del informe diario para cada jornada
- Informe final acumulado a los 14 días vs Buy & Hold
"""
import argparse
import logging
import sys
from datetime import datetime, time, timezone
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
from src.data.fetcher import DataFetcher
from src.indicators.technical import compute_all_indicators
from src.scheduler.time_manager import MadridTimeManager
from src.simulation.reporter import SimulationReporter
from src.simulation.wallet import SimulatedWallet
from src.strategy.rules import RuleEngine, SignalAction

from src.notifications import get_notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TradIA-Replay")


def main():
    parser = argparse.ArgumentParser(description="TradIA - Replay de Simulación y Paper Trading")
    parser.add_argument("--days", type=int, default=14, help="Días de simulación (por defecto 14)")
    parser.add_argument("--capital", type=float, default=None, help="Capital inicial en USDT (por defecto de config: 10.0€)")
    parser.add_argument("--reset-db", action="store_true", help="Reiniciar base de datos de simulación")
    args = parser.parse_args()

    config = load_config()
    initial_cap = args.capital if args.capital is not None else config.simulation.initial_balance_usdt
    config.simulation.initial_balance_usdt = initial_cap

    # Base de datos en archivo de simulación
    db = DatabaseManager()
    if args.reset_db:
        logger.info("Reiniciando base de datos de simulación...")
        db.init_wallet(initial_balance=initial_cap, force_reset=True)
    else:
        db.init_wallet(initial_balance=initial_cap)

    notifier = get_notifier(config)

    def cb(reason, bal, fl):
        msg = f"🚨 ALERTA TRADIA — Se ha agotado el margen operable sobre el colchón ({bal:.2f}€). Motivo: {reason}"
        notifier.send_alert(msg, subject="🚨 ALERTA TRADIA: Colchón de seguridad alcanzado")

    wallet = SimulatedWallet(db, config.simulation, on_circuit_breaker=cb)
    reporter = SimulationReporter(db)
    time_mgr = MadridTimeManager(config.schedule)
    fetcher = DataFetcher(exchange_id=config.market.exchange)
    rule_engine = RuleEngine(config)

    logger.info(
        f"Iniciando Replay de Simulación TradIA ({args.days} días) | "
        f"Capital inicial: ${initial_cap:,.2f} USDT | Tamaño: {config.simulation.position_size_pct}% | "
        f"Comisión: {config.simulation.fee_pct}% | Slippage: {config.simulation.slippage_pct}%"
    )

    # 1. Descargar datos para los pares configurados
    datasets = {}
    benchmark_prices = {}

    for sym in config.market.symbols:
        df = fetcher.fetch_historical(
            symbol=sym,
            timeframe=config.market.timeframe,
            days=args.days + 2,
            use_cache=True,
        )
        df_ind = compute_all_indicators(df, config.indicators)
        datasets[sym] = df_ind
        if not df.empty:
            benchmark_prices[sym] = {
                "start": float(df.iloc[0]["close"]),
                "end": float(df.iloc[-1]["close"]),
            }

    # 2. Unificar timestamps en orden cronológico
    all_timestamps = sorted(list(set.union(*[set(df.index) for df in datasets.values()])))

    # Filtrar a los últimos N días
    cutoff = all_timestamps[-1] - (all_timestamps[-1] - all_timestamps[0])
    sim_timestamps = all_timestamps[- (args.days * 96):]  # 96 velas de 15m por día

    current_sim_date = None
    daily_reports_generated = []

    for ts in sim_timestamps:
        madrid_dt = time_mgr.to_madrid(ts)
        candle_date = madrid_dt.strftime("%Y-%m-%d")
        candle_time = madrid_dt.time()

        # Cambio de jornada -> generar informe del día anterior si no se emitió
        if current_sim_date is not None and candle_date != current_sim_date:
            daily_rep = reporter.generate_daily_report(date_target=current_sim_date)
            daily_reports_generated.append(daily_rep)
            current_sim_date = candle_date
        elif current_sim_date is None:
            current_sim_date = candle_date

        # A. Comprobar si es el momento del cierre forzado (22:45 en Madrid)
        if time_mgr.is_forced_close_time(madrid_dt):
            current_prices = {}
            for sym, df_ind in datasets.items():
                if ts in df_ind.index:
                    current_prices[sym] = float(df_ind.loc[ts]["close"])
            forced_trades = wallet.force_close_all(current_prices, timestamp=ts)
            if forced_trades:
                logger.info(f"[{candle_date} 22:45] Cierre forzado de {len(forced_trades)} posiciones.")

        # B. Comprobar si está dentro del horario de trading activo
        if not time_mgr.is_trading_hour(madrid_dt):
            continue

        # C. Evaluar señales para cada par en este timestamp
        for sym, df_ind in datasets.items():
            if ts not in df_ind.index:
                continue

            row = df_ind.loc[ts]
            sig = rule_engine.evaluate_candle(row, symbol=sym, timestamp=ts)

            if sig:
                db.record_signal(
                    timestamp=sig.timestamp,
                    symbol=sig.symbol,
                    action=sig.action.value,
                    price=sig.price,
                    score=sig.score,
                    reasons=sig.reasons,
                    metrics=sig.metrics,
                )

                if sig.action == SignalAction.BUY:
                    wallet.open_simulated_buy(
                        symbol=sym,
                        signal_price=sig.price,
                        timestamp=sig.timestamp,
                        reasons=sig.reasons,
                    )
                elif sig.action == SignalAction.SELL:
                    wallet.close_simulated_position(
                        symbol=sym,
                        signal_price=sig.price,
                        timestamp=sig.timestamp,
                        exit_reasons=sig.reasons,
                        exit_tag="SEÑAL_VENTA",
                    )

    # Liquidar cualquier posición abierta en la última vela antes de los reportes
    final_prices = {}
    for sym, df_ind in datasets.items():
        if sim_timestamps[-1] in df_ind.index:
            final_prices[sym] = float(df_ind.loc[sim_timestamps[-1]]["close"])
    wallet.force_close_all(final_prices, timestamp=sim_timestamps[-1], reason="FIN_SIMULACION_14D")

    # Informe del último día simulado
    if current_sim_date:
        daily_rep = reporter.generate_daily_report(date_target=current_sim_date)
        daily_reports_generated.append(daily_rep)

    # Imprimir muestra de los últimos 2 reportes diarios
    print("\n" + "=" * 76)
    print(" 📰 MUESTRA DE REPORTES DIARIOS SIMULADOS (Últimas jornadas)")
    print("=" * 76)
    for rep in daily_reports_generated[-2:]:
        print(rep["message_text"])
        print()

    # 3. Generar el Informe Final a 14 días
    final_rep = reporter.generate_final_14day_report(benchmark_prices=benchmark_prices)
    print(final_rep["report_text"])


if __name__ == "__main__":
    main()
