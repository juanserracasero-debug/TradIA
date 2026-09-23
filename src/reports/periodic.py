"""Generador de Informes Periódicos de Rendimiento (Supervisión Humana cada 2 semanas).

Calcula:
1. Cuántas señales dio el sistema (por par y acción).
2. Qué porcentaje habría sido acertado (con TP y SL configurados).
3. Qué indicadores están funcionando mejor o peor (desglose por métrica).
4. Sugerencias objetivas de ajuste para que el USUARIO decida manualmente
   si calibrar umbrales en config.yaml (cero autoajustes ciegos).
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from tabulate import tabulate

from src.config import AppConfig
from src.database.db import DatabaseManager

logger = logging.getLogger(__name__)


class PeriodicReportGenerator:
    """Genera el informe quincenal/mensual para supervisión humana."""

    def __init__(self, config: AppConfig, db_manager: Optional[DatabaseManager] = None):
        self.config = config
        self.db = db_manager or DatabaseManager()

    def generate_report(self, days: int = 14) -> Dict[str, Any]:
        """Genera el informe cuantitativo de los últimos N días."""
        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(days=days)).isoformat()
        end_date = now.isoformat()

        # 1. Obtener trades cerrados de la base de datos
        trades = self.db.get_trades(start_date=start_date, end_date=end_date)
        signals_count = self.db.get_signals_count(start_date=start_date, end_date=end_date)
        wallet = self.db.get_wallet()

        total_trades = len(trades)
        winning = [t for t in trades if float(t["net_pnl_usdt"]) > 0]
        losing = [t for t in trades if float(t["net_pnl_usdt"]) <= 0]
        win_rate = (len(winning) / total_trades * 100.0) if total_trades > 0 else 0.0

        net_pnl_total = sum(float(t["net_pnl_usdt"]) for t in trades)
        fees_total = sum(float(t["total_fees_usdt"]) for t in trades)

        # 2. Desglose de acierto por indicador detonante
        ind_performance: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            reasons = t.get("entry_reasons", [])
            if isinstance(reasons, str):
                try:
                    reasons = json.loads(reasons)
                except Exception:
                    reasons = [reasons]

            pnl = float(t["net_pnl_usdt"])
            is_win = pnl > 0

            for r in reasons:
                if "RSI" in r:
                    cat = "RSI Sobreventa/Sobrecompra"
                elif "EMA" in r:
                    cat = "Cruce de Medias EMA"
                elif "MACD" in r:
                    cat = "Cruce MACD / Señal"
                elif "Bollinger" in r:
                    cat = "Bandas de Bollinger"
                elif "Volumen" in r:
                    cat = "Volumen Anómalo (>1.5x)"
                else:
                    cat = "Otros Indicadores"

                if cat not in ind_performance:
                    ind_performance[cat] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
                ind_performance[cat]["trades"] += 1
                if is_win:
                    ind_performance[cat]["wins"] += 1
                ind_performance[cat]["total_pnl"] += pnl

        for k, v in ind_performance.items():
            v["win_rate"] = (v["wins"] / v["trades"] * 100.0) if v["trades"] > 0 else 0.0
            v["avg_pnl"] = (v["total_pnl"] / v["trades"]) if v["trades"] > 0 else 0.0

        # 3. Formular sugerencias objetivas para que el usuario ajuste manualmente
        suggestions = []
        for ind_name, data in ind_performance.items():
            if data["trades"] >= 2:
                if data["win_rate"] < 40.0:
                    suggestions.append(
                        f"⚠️ BAJA EFECTIVIDAD: '{ind_name}' tiene solo {data['win_rate']:.1f}% de aciertos "
                        f"(P&L: ${data['total_pnl']:+,.2f}). Se recomienda endurecer su umbral en 'config.yaml' "
                        f"(ej. RSI más extremo o mayor ratio de volumen) o reducir su ponderación."
                    )
                elif data["win_rate"] >= 60.0:
                    suggestions.append(
                        f"✅ ALTA EFECTIVIDAD: '{ind_name}' alcanza {data['win_rate']:.1f}% de aciertos "
                        f"(P&L: ${data['total_pnl']:+,.2f}). Indicador fiable en este régimen de mercado."
                    )

        if not suggestions:
            suggestions.append("ℹ️ Operaciones insuficientes en la ventana para conclusiones cuantitativas.")

        # 4. Formatear reporte de texto
        lines = [
            "=" * 76,
            f" 📋 INFORME PERIÓDICO DE RENDIMIENTO — ÚLTIMOS {days} DÍAS (TRADIA)",
            "=" * 76,
            f"• Período evaluado:       {start_date[:10]} -> {end_date[:10]}",
            f"• Señales generadas:      {signals_count}",
            f"• Operaciones ejecutadas:  {total_trades}",
            f"• Operaciones ganadoras:   {len(winning)} ({win_rate:.1f}%)",
            f"• Operaciones perdedoras:  {len(losing)} ({100 - win_rate:.1f}%)",
            f"• P&L Neto Acumulado:      ${net_pnl_total:+,.2f} USDT",
            f"• Total Comisiones:        ${fees_total:.2f} USDT",
            "-" * 76,
        ]

        if ind_performance:
            lines.append("📌 EFECTIVIDAD POR INDICADOR TÉCNICO:")
            ind_rows = []
            for name, st in sorted(ind_performance.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
                ind_rows.append([
                    name,
                    st["trades"],
                    f"{st['win_rate']:.1f}%",
                    f"${st['avg_pnl']:+,.2f}",
                    f"${st['total_pnl']:+,.2f}",
                ])
            lines.append(tabulate(
                ind_rows,
                headers=["Indicador", "Trades", "Win Rate %", "P&L Medio", "P&L Total"],
                tablefmt="simple",
            ))
            lines.append("-" * 76)

        lines.append("💡 RECOMENDACIONES PARA REVISIÓN MANUAL DEL USUARIO:")
        for sug in suggestions:
            lines.append(f"  • {sug}")
        lines.append("")
        lines.append("🛡️ PRINCIPIO DE SEGURIDAD: El bot NUNCA alterará sus reglas automáticamente.")
        lines.append("   Tú eres quien decide si modificar los umbrales en 'config/config.yaml'.")
        lines.append("=" * 76)

        report_text = "\n".join(lines)

        return {
            "days": days,
            "signals_count": signals_count,
            "total_trades": total_trades,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate_pct": win_rate,
            "net_pnl_usdt": net_pnl_total,
            "fees_usdt": fees_total,
            "ind_performance": ind_performance,
            "suggestions": suggestions,
            "report_text": report_text,
        }
