"""CLI para ejecutar el backtesting histórico de TradIA sobre datos públicos.

Uso:
    python scripts/run_backtest.py
    python scripts/run_backtest.py --days 30 --pairs BTC/USDT ETH/USDT
    python scripts/run_backtest.py --tp 2.0 --sl 1.0
"""
import argparse
import logging
import sys
from pathlib import Path

# Configurar stdout y stderr en utf-8 para compatibilidad universal con terminales Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Añadir directorio raíz al PYTHONPATH
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from tabulate import tabulate
from src.config import load_config
from src.data.fetcher import DataFetcher
from src.indicators.technical import compute_all_indicators
from src.strategy.rules import RuleEngine
from src.backtest.engine import BacktestEngine, BacktestResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TradIA-Backtest")


def format_backtest_report(result: BacktestResult, tp_pct: float, sl_pct: float) -> str:
    """Genera un informe visual completo y detallado del resultado del backtest."""
    lines = []
    lines.append("=" * 78)
    lines.append(f" 📊 REPORTE DE BACKTESTING TRADIA — {result.symbol} ")
    lines.append("=" * 78)
    lines.append(f"• Período evaluado:    {result.start_date.strftime('%Y-%m-%d %H:%M')} -> {result.end_date.strftime('%Y-%m-%d %H:%M')} UTC")
    lines.append(f"• Total de velas (15m): {result.total_candles:,}")
    lines.append(f"• Parámetros de salida: TP = +{tp_pct:.2f}% | SL = -{sl_pct:.2f}% (Ratio: {tp_pct/sl_pct:.2f}:1)")
    lines.append("-" * 78)

    summary_data = [
        ["Total Señales Generadas", f"{result.total_signals}"],
        ["Operaciones Ejecutadas (no solapadas)", f"{result.total_trades}"],
        ["Operaciones Ganadoras (Hits TP / Ganancia)", f"{result.winning_trades} ({result.win_rate_pct:.1f}%)"],
        ["Operaciones Perdedoras (Hits SL / Pérdida)", f"{result.losing_trades} ({100 - result.win_rate_pct:.1f}%)"],
        ["Salidas por Límite de Tiempo (Intradía)", f"{result.time_limit_exits}"],
        ["Retorno Medio por Operación (Neto)", f"{result.avg_return_pct:+.2f}%"],
        ["Retorno Acumulado Neto (Suma %)", f"{result.cumulative_return_pct:+.2f}%"],
        ["Profit Factor (Ganancia / Pérdida bruta)", f"{result.profit_factor:.2f}"],
        ["Máximo Drawdown (%)", f"{result.max_drawdown_pct:.2f}%"],
        ["Duración Media de Posición", f"{result.avg_holding_bars:.1f} velas ({(result.avg_holding_bars * 15 / 60):.1f} horas)"],
    ]
    lines.append(tabulate(summary_data, headers=["Métrica Clave", "Valor"], tablefmt="fancy_grid"))

    # Desglose por indicador
    if result.indicator_stats:
        lines.append("\n📌 EFECTIVIDAD POR INDICADOR / DETONANTE:")
        ind_table = []
        for ind_name, stats in sorted(result.indicator_stats.items(), key=lambda x: x[1]["trades"], reverse=True):
            ind_table.append([
                ind_name,
                stats["trades"],
                f"{stats['win_rate']:.1f}%",
                f"{stats['avg_return']:+.2f}%",
                f"{stats['total_return']:+.2f}%",
            ])
        lines.append(tabulate(
            ind_table,
            headers=["Indicador / Razón", "Señales", "Win Rate %", "Retorno Medio", "Retorno Total"],
            tablefmt="simple",
        ))

    # Últimas 5 operaciones
    if result.trades:
        lines.append("\n📝 ÚLTIMAS OPERACIONES SIMULADAS (Muestra):")
        trade_rows = []
        for t in result.trades[-6:]:
            trade_rows.append([
                t.action.value,
                t.entry_time.strftime("%m-%d %H:%M"),
                f"${t.entry_price:,.2f}",
                t.exit_time.strftime("%m-%d %H:%M"),
                f"${t.exit_price:,.2f}",
                t.exit_reason,
                f"{t.net_return_pct:+.2f}%",
                f"{t.holding_bars} v",
            ])
        lines.append(tabulate(
            trade_rows,
            headers=["Acción", "Entrada", "Precio In", "Salida", "Precio Out", "Motivo Salida", "Retorno Neto", "Duración"],
            tablefmt="simple",
        ))

    lines.append("=" * 78)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="TradIA - Motor de Backtesting Cuantitativo")
    parser.add_argument("--days", type=int, default=None, help="Días históricos a evaluar (ej. 30)")
    parser.add_argument("--pairs", nargs="+", default=None, help="Pares de trading (ej. BTC/USDT ETH/USDT)")
    parser.add_argument("--timeframe", type=str, default=None, help="Timeframe de velas (ej. 15m)")
    parser.add_argument("--tp", type=float, default=None, help="Take Profit porcentual (ej. 1.5)")
    parser.add_argument("--sl", type=float, default=None, help="Stop Loss porcentual (ej. 0.75)")
    parser.add_argument("--refresh", action="store_true", help="Forzar re-descarga de datos públicos")
    parser.add_argument("--allow-short", action="store_true", help="Permitir simulación de posiciones cortas")

    args = parser.parse_args()

    # 1. Cargar configuración base
    config = load_config()

    # Sobrescribir con argumentos CLI si se especificaron
    days = args.days or config.backtest.default_days
    symbols = args.pairs or config.market.symbols
    timeframe = args.timeframe or config.market.timeframe
    if args.tp is not None:
        config.backtest.take_profit_pct = args.tp
    if args.sl is not None:
        config.backtest.stop_loss_pct = args.sl

    logger.info(
        f"Iniciando Backtesting TradIA | Días: {days} | Timeframe: {timeframe} | "
        f"TP: +{config.backtest.take_profit_pct}% | SL: -{config.backtest.stop_loss_pct}%"
    )

    fetcher = DataFetcher(exchange_id=config.market.exchange)
    rule_engine = RuleEngine(config=config)
    bt_engine = BacktestEngine(config=config)

    overall_results = []

    for symbol in symbols:
        try:
            logger.info(f"\nProcesando {symbol}...")
            # Descarga / carga de datos
            df = fetcher.fetch_historical(
                symbol=symbol,
                timeframe=timeframe,
                days=days,
                use_cache=True,
                force_refresh=args.refresh,
            )

            # Cálculo de indicadores
            df_with_ind = compute_all_indicators(df, config=config.indicators)

            # Generación de señales deterministas
            signals = rule_engine.evaluate_dataframe(df_with_ind, symbol=symbol)

            # Simulación de backtesting
            result = bt_engine.run(
                df=df_with_ind,
                signals=signals,
                symbol=symbol,
                allow_short=args.allow_short,
            )

            overall_results.append(result)

            report = format_backtest_report(
                result,
                tp_pct=config.backtest.take_profit_pct,
                sl_pct=config.backtest.stop_loss_pct,
            )
            print(report)

        except Exception as e:
            logger.error(f"Error procesando {symbol}: {e}", exc_info=True)

    # Resumen global multiactivo
    if len(overall_results) > 1:
        print("\n" + "=" * 65)
        print(" 🌐 RESUMEN COMPARATIVO MULTI-ACTIVO")
        print("=" * 65)
        comp_data = []
        for r in overall_results:
            comp_data.append([
                r.symbol,
                r.total_signals,
                r.total_trades,
                f"{r.win_rate_pct:.1f}%",
                f"{r.avg_return_pct:+.2f}%",
                f"{r.cumulative_return_pct:+.2f}%",
                f"{r.profit_factor:.2f}",
                f"{r.max_drawdown_pct:.2f}%",
            ])
        print(tabulate(
            comp_data,
            headers=["Par", "Señales", "Trades", "Win Rate %", "Ret. Medio", "Ret. Acum.", "P. Factor", "Max DD %"],
            tablefmt="fancy_grid",
        ))


if __name__ == "__main__":
    main()
