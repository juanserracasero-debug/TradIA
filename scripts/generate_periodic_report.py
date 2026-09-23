"""CLI para generar el informe periódico quincenal de supervisión humana.

Uso:
    python scripts/generate_periodic_report.py
    python scripts/generate_periodic_report.py --days 14
    python scripts/generate_periodic_report.py --days 30
"""
import argparse
import sys
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
from src.reports.periodic import PeriodicReportGenerator


def main():
    parser = argparse.ArgumentParser(description="TradIA - Informe Periódico de Rendimiento para Supervisión Humana")
    parser.add_argument("--days", type=int, default=14, help="Número de días a evaluar (por defecto 14)")
    args = parser.parse_args()

    config = load_config()
    db = DatabaseManager()
    reporter = PeriodicReportGenerator(config=config, db_manager=db)

    report_data = reporter.generate_report(days=args.days)
    print(report_data["report_text"])


if __name__ == "__main__":
    main()
