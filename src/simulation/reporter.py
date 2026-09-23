"""Generador de Informes de Simulación (Paper Trading).

Genera:
1. Informe Diario (23:00):
   - Resumen conciso por WhatsApp.
   - Archivo HTML auto-contenido (y CSV) guardado en carpeta reports/daily_report_YYYY-MM-DD.html.
2. Informe Final (14 días):
   - P&L acumulado vs Buy & Hold, efectividad por indicador y sugerencias de ajuste manual.
"""
import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from tabulate import tabulate

from src.database.db import DatabaseManager

logger = logging.getLogger(__name__)


class SimulationReporter:
    """Generador de informes diarios y agregados a 14 días."""

    def __init__(self, db: DatabaseManager, reports_dir: Optional[str] = None):
        self.db = db
        if reports_dir:
            self.reports_dir = Path(reports_dir)
        else:
            self.reports_dir = Path(__file__).resolve().parent.parent.parent / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_report(self, date_target: Optional[str] = None) -> Dict[str, Any]:
        """Genera el informe de cierre diario de la jornada en WhatsApp y en archivo HTML/CSV."""
        now = datetime.now(timezone.utc)
        if date_target is None:
            date_target = now.strftime("%Y-%m-%d")

        start_day = f"{date_target}T00:00:00"
        end_day = f"{date_target}T23:59:59"

        # 1. Obtener datos de la base de datos
        trades = self.db.get_trades(start_date=start_day, end_date=end_day)
        signals_count = self.db.get_signals_count(start_date=start_day, end_date=end_day)
        wallet = self.db.get_wallet()
        cash = float(wallet["cash_balance"])
        floor_activated = bool(wallet.get("floor_activated", 0))

        total_trades = len(trades)
        winning_trades = [t for t in trades if float(t["net_pnl_usdt"]) > 0]
        losing_trades = [t for t in trades if float(t["net_pnl_usdt"]) <= 0]
        net_pnl_day = sum(float(t["net_pnl_usdt"]) for t in trades)
        total_fees_day = sum(float(t["total_fees_usdt"]) for t in trades)

        initial_capital = float(wallet["initial_balance"])
        net_pnl_pct_capital = (net_pnl_day / initial_capital) * 100.0 if initial_capital > 0 else 0.0
        win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0

        best_trade = max(trades, key=lambda t: float(t["net_pnl_usdt"])) if trades else None
        worst_trade = min(trades, key=lambda t: float(t["net_pnl_usdt"])) if trades else None

        # Guardar snapshot diario en SQLite
        self.db.save_daily_snapshot(
            date_str=date_target,
            closing_balance=cash,
            net_pnl_usdt=net_pnl_day,
            net_pnl_pct=net_pnl_pct_capital,
            trades_count=total_trades,
            win_rate_pct=win_rate,
        )

        # 2. Generar archivo HTML y CSV en reports/
        html_file = self._save_html_daily_report(
            date_target=date_target,
            signals_count=signals_count,
            total_trades=total_trades,
            winning_count=len(winning_trades),
            losing_count=len(losing_trades),
            win_rate=win_rate,
            net_pnl_day=net_pnl_day,
            net_pnl_pct=net_pnl_pct_capital,
            closing_balance=cash,
            total_fees=total_fees_day,
            floor_activated=floor_activated,
            trades=trades,
        )
        self._save_csv_daily_report(date_target=date_target, trades=trades)

        # 3. Formatear mensaje conciso para WhatsApp
        pnl_sign = "+" if net_pnl_day >= 0 else ""
        pnl_emoji = "🟢" if net_pnl_day >= 0 else "🔴"
        colchon_status = "Blindado (6.00€)" if floor_activated else "Inactivo (acumulando)"

        lines = [
            f"📅 *RESUMEN DIARIO TRADIA — {date_target}*",
            "════════════════════════════════",
            f"📡 *Señales generadas:* {signals_count}",
            f"⚡ *Operaciones ejecutadas:* {total_trades}",
            f"🎯 *Ratio de acierto:* {win_rate:.1f}% ({len(winning_trades)}G / {len(losing_trades)}P)",
            f"{pnl_emoji} *P&L Neto del Día:* {pnl_sign}{net_pnl_day:.2f}€ ({pnl_sign}{net_pnl_pct_capital:.2f}%)",
            f"💸 *Comisiones pagadas:* {total_fees_day:.2f}€",
            f"💼 *Balance Wallet cierre:* {cash:.2f}€",
            f"🛡️ *Colchón de seguridad:* {colchon_status}",
            "────────────────────────────────",
        ]

        if best_trade:
            sym = best_trade["symbol"]
            pnl = float(best_trade["net_pnl_usdt"])
            pct = float(best_trade["net_pnl_pct"])
            s = "+" if pnl >= 0 else ""
            lines.append(f"🏆 *Mejor trade:* {sym} -> {s}{pnl:.2f}€ ({s}{pct:.2f}%)")

        if worst_trade and worst_trade != best_trade:
            sym = worst_trade["symbol"]
            pnl = float(worst_trade["net_pnl_usdt"])
            pct = float(worst_trade["net_pnl_pct"])
            s = "+" if pnl >= 0 else ""
            lines.append(f"⚠️ *Peor trade:* {sym} -> {s}{pnl:.2f}€ ({s}{pct:.2f}%)")

        lines.append("════════════════════════════════")
        lines.append(f"📁 _Informe detallado guardado en reports/daily_report_{date_target}.html_")

        message_text = "\n".join(lines)

        return {
            "date": date_target,
            "signals_count": signals_count,
            "total_trades": total_trades,
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "win_rate_pct": win_rate,
            "net_pnl_usdt": net_pnl_day,
            "net_pnl_pct_capital": net_pnl_pct_capital,
            "closing_balance": cash,
            "floor_activated": floor_activated,
            "html_path": str(html_file),
            "message_text": message_text,
        }

    def _save_html_daily_report(
        self,
        date_target: str,
        signals_count: int,
        total_trades: int,
        winning_count: int,
        losing_count: int,
        win_rate: float,
        net_pnl_day: float,
        net_pnl_pct: float,
        closing_balance: float,
        total_fees: float,
        floor_activated: bool,
        trades: List[Dict[str, Any]],
    ) -> Path:
        """Genera un archivo HTML auto-contenido y estilizado para el reporte diario."""
        filepath = self.reports_dir / f"daily_report_{date_target}.html"
        pnl_color = "#16a34a" if net_pnl_day >= 0 else "#dc2626"
        pnl_sign = "+" if net_pnl_day >= 0 else ""

        rows_html = []
        for t in trades:
            pnl = float(t["net_pnl_usdt"])
            pct = float(t["net_pnl_pct"])
            color = "#16a34a" if pnl >= 0 else "#dc2626"
            sign = "+" if pnl >= 0 else ""
            rows_html.append(f"""
            <tr>
                <td><strong>{t['symbol']}</strong></td>
                <td>{t['entry_time'][11:16]}</td>
                <td>${float(t['entry_price']):,.2f}</td>
                <td>{t['exit_time'][11:16]}</td>
                <td>${float(t['exit_price']):,.2f}</td>
                <td>{float(t['position_size_usdt']):.2f}€</td>
                <td style="color: {color}; font-weight: bold;">{sign}{pnl:.2f}€</td>
                <td style="color: {color}; font-weight: bold;">{sign}{pct:.2f}%</td>
                <td><small>{t.get('exit_reasons', [''])[0] if isinstance(t.get('exit_reasons'), list) else str(t.get('exit_reasons'))}</small></td>
            </tr>
            """)

        table_body = "\n".join(rows_html) if rows_html else "<tr><td colspan='9' style='text-align:center;'>No hubo operaciones cerradas en esta jornada.</td></tr>"

        html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Reporte Diario TradIA — {date_target}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f8fafc; color: #1e293b; padding: 24px; margin: 0; }}
        .container {{ max-width: 960px; margin: 0 auto; background: #ffffff; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); padding: 32px; }}
        h1 {{ margin-top: 0; color: #0f172a; border-bottom: 2px solid #e2e8f0; padding-bottom: 12px; font-size: 24px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin: 24px 0; }}
        .card {{ background: #f1f5f9; padding: 16px; border-radius: 8px; border-left: 4px solid #3b82f6; }}
        .card .label {{ font-size: 12px; color: #64748b; text-transform: uppercase; font-weight: 600; margin-bottom: 4px; }}
        .card .val {{ font-size: 20px; font-weight: 700; color: #0f172a; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 24px; }}
        th {{ background: #f8fafc; padding: 12px; text-align: left; font-size: 13px; color: #475569; border-bottom: 2px solid #cbd5e1; }}
        td {{ padding: 12px; border-bottom: 1px solid #e2e8f0; font-size: 14px; }}
        .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
        .badge-active {{ background: #dcfce7; color: #166534; }}
        .badge-inactive {{ background: #fef3c7; color: #92400e; }}
        footer {{ margin-top: 32px; font-size: 12px; color: #94a3b8; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Reporte Diario de Trading — TradIA ({date_target})</h1>
        
        <div class="grid">
            <div class="card" style="border-left-color: {pnl_color};">
                <div class="label">P&L Neto del Día</div>
                <div class="val" style="color: {pnl_color};">{pnl_sign}{net_pnl_day:.2f}€ ({pnl_sign}{net_pnl_pct:.2f}%)</div>
            </div>
            <div class="card">
                <div class="label">Balance en Wallet</div>
                <div class="val">{closing_balance:.2f}€</div>
            </div>
            <div class="card">
                <div class="label">Aciertos (Win Rate)</div>
                <div class="val">{win_rate:.1f}% ({winning_count}G / {losing_count}P)</div>
            </div>
            <div class="card">
                <div class="label">Operaciones / Señales</div>
                <div class="val">{total_trades} ops / {signals_count} señ.</div>
            </div>
            <div class="card">
                <div class="label">Colchón Dinámico (6€)</div>
                <div class="val"><span class="badge {'badge-active' if floor_activated else 'badge-inactive'}">{'BLINDADO' if floor_activated else 'INACTIVO'}</span></div>
            </div>
        </div>

        <h3>Operaciones del Día</h3>
        <table>
            <thead>
                <tr>
                    <th>Activo</th>
                    <th>Hora In</th>
                    <th>Precio In</th>
                    <th>Hora Out</th>
                    <th>Precio Out</th>
                    <th>Invertido</th>
                    <th>P&L (€)</th>
                    <th>P&L (%)</th>
                    <th>Motivo Cierre</th>
                </tr>
            </thead>
            <tbody>
                {table_body}
            </tbody>
        </table>

        <footer>
            Generado automáticamente por TradIA Paper Trading • Sesión cerrada a las 23:00 Madrid
        </footer>
    </div>
</body>
</html>
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)
        return filepath

    def _save_csv_daily_report(self, date_target: str, trades: List[Dict[str, Any]]) -> Path:
        """Guarda un CSV exportable con las operaciones del día."""
        filepath = self.reports_dir / f"daily_trades_{date_target}.csv"
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "symbol", "entry_time", "entry_price", "exit_time", "exit_price",
                "position_size_eur", "total_fees_eur", "net_pnl_eur", "net_pnl_pct", "exit_reasons"
            ])
            for t in trades:
                writer.writerow([
                    t["symbol"],
                    t["entry_time"],
                    t["entry_price"],
                    t["exit_time"],
                    t["exit_price"],
                    t["position_size_usdt"],
                    t["total_fees_usdt"],
                    t["net_pnl_usdt"],
                    t["net_pnl_pct"],
                    t.get("exit_reasons", ""),
                ])
        return filepath

    def generate_final_14day_report(
        self,
        benchmark_prices: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> Dict[str, Any]:
        """Genera el informe final agregado tras el ciclo de simulación (14 días)."""
        wallet = self.db.get_wallet()
        initial_cap = float(wallet["initial_balance"])
        current_cash = float(wallet["cash_balance"])
        trades = self.db.get_trades()
        open_positions = self.db.get_open_positions()
        invested_in_open = sum(float(p["position_cost_usdt"]) for p in open_positions)
        total_equity = current_cash + invested_in_open

        total_net_pnl = total_equity - initial_cap
        total_roi_pct = (total_net_pnl / initial_cap * 100.0) if initial_cap > 0 else 0.0

        total_trades = len(trades)
        winning = [t for t in trades if float(t["net_pnl_usdt"]) > 0]
        losing = [t for t in trades if float(t["net_pnl_usdt"]) <= 0]
        global_win_rate = (len(winning) / total_trades * 100.0) if total_trades > 0 else 0.0

        # Análisis por indicador
        ind_stats: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            entry_reasons = t.get("entry_reasons", [])
            if isinstance(entry_reasons, str):
                try:
                    entry_reasons = json.loads(entry_reasons)
                except Exception:
                    entry_reasons = [entry_reasons]

            pnl = float(t["net_pnl_usdt"])
            for reason in entry_reasons:
                if "RSI" in reason:
                    key = "RSI Sobreventa/Sobrecompra"
                elif "EMA" in reason:
                    key = "Cruce de Medias EMA (9/21)"
                elif "MACD" in reason:
                    key = "Cruce MACD / Señal"
                elif "Bollinger" in reason:
                    key = "Bandas de Bollinger"
                elif "Volumen" in reason:
                    key = "Volumen Anómalo (>1.5x)"
                else:
                    key = "Otras Reglas"

                if key not in ind_stats:
                    ind_stats[key] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
                ind_stats[key]["trades"] += 1
                if pnl > 0:
                    ind_stats[key]["wins"] += 1
                ind_stats[key]["total_pnl"] += pnl

        for k, v in ind_stats.items():
            v["win_rate"] = (v["wins"] / v["trades"] * 100.0) if v["trades"] > 0 else 0.0
            v["avg_pnl"] = (v["total_pnl"] / v["trades"]) if v["trades"] > 0 else 0.0

        # Comparativa Buy & Hold
        buy_and_hold_results: Dict[str, float] = {}
        if benchmark_prices:
            for sym, p_info in benchmark_prices.items():
                p_start = p_info.get("start", 0)
                p_end = p_info.get("end", 0)
                if p_start > 0:
                    bh_return = ((p_end - p_start) / p_start) * 100.0
                    buy_and_hold_results[sym] = bh_return

        recommendations = []
        for k, v in ind_stats.items():
            if v["trades"] >= 2:
                if v["win_rate"] < 40.0:
                    recommendations.append(
                        f"🔴 {k} mostró baja efectividad ({v['win_rate']:.1f}% aciertos, "
                        f"P&L: ${v['total_pnl']:+.2f}€). Se sugiere calibrar al alza su exigencia."
                    )
                elif v["win_rate"] >= 55.0:
                    recommendations.append(
                        f"🟢 {k} mostró alta consistencia ({v['win_rate']:.1f}% aciertos, "
                        f"P&L: ${v['total_pnl']:+.2f}€). Buen candidato para mantener."
                    )

        if not recommendations:
            recommendations.append("ℹ️ Operaciones insuficientes para conclusiones definitivas.")

        pnl_emoji = "🟢" if total_net_pnl >= 0 else "🔴"
        pnl_sign = "+" if total_net_pnl >= 0 else ""
        lines = [
            "=" * 76,
            " 🏆 INFORME FINAL DE PAPER TRADING (14 DÍAS) — TRADIA",
            "=" * 76,
            f"• Capital Inicial:         {initial_cap:.2f}€",
            f"• Balance Final Simulado:  {total_equity:.2f}€ (Efectivo: {current_cash:.2f}€)",
            f"• P&L Neto Acumulado:      {pnl_emoji} {pnl_sign}{total_net_pnl:.2f}€ ({pnl_sign}{total_roi_pct:.2f}%)",
            f"• Total Operaciones:       {total_trades} ({len(winning)}G / {len(losing)}P)",
            f"• Win Rate Global:         {global_win_rate:.1f}%",
            "-" * 76,
        ]

        if buy_and_hold_results:
            lines.append("📊 COMPARATIVA VS. ESTRATEGIA BUY & HOLD:")
            bh_table = []
            for sym, bh_ret in buy_and_hold_results.items():
                alpha = total_roi_pct - bh_ret
                bh_table.append([
                    sym,
                    f"{bh_ret:+.2f}%",
                    f"{total_roi_pct:+.2f}%",
                    f"{alpha:+.2f}%",
                ])
            lines.append(tabulate(
                bh_table,
                headers=["Activo", "Buy & Hold", "TradIA Simulado", "Diferencial (Alfa)"],
                tablefmt="simple",
            ))
            lines.append("-" * 76)

        if ind_stats:
            lines.append("📌 EFECTIVIDAD POR INDICADOR TÉCNICO:")
            ind_table = []
            for ind_name, st in sorted(ind_stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
                ind_table.append([
                    ind_name,
                    st["trades"],
                    f"{st['win_rate']:.1f}%",
                    f"${st['avg_pnl']:+.2f}€",
                    f"${st['total_pnl']:+.2f}€",
                ])
            lines.append(tabulate(
                ind_table,
                headers=["Indicador", "Trades", "Win Rate %", "P&L Medio", "P&L Total"],
                tablefmt="simple",
            ))
            lines.append("-" * 76)

        lines.append("💡 RECOMENDACIONES DE AJUSTE (SUPERVISIÓN HUMANA):")
        for rec in recommendations:
            lines.append(f"  {rec}")
        lines.append("")
        lines.append("⚠️ NOTA: El sistema no modifica sus parámetros por sí mismo.")
        lines.append("   Revisa estos datos y decide si ajustar los umbrales en 'config/config.yaml'.")
        lines.append("=" * 76)

        report_text = "\n".join(lines)

        return {
            "initial_capital": initial_cap,
            "final_balance": current_cash,
            "total_net_pnl": total_net_pnl,
            "total_roi_pct": total_roi_pct,
            "total_trades": total_trades,
            "win_rate_pct": global_win_rate,
            "buy_and_hold": buy_and_hold_results,
            "indicator_stats": ind_stats,
            "recommendations": recommendations,
            "report_text": report_text,
        }
