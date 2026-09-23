"""Script de migración de datos de SQLite local (data/tradia.db) a Supabase (PostgreSQL en la nube).

Uso:
    python scripts/migrate_sqlite_to_supabase.py          # Migra todos los datos existentes de SQLite a Supabase
    python scripts/migrate_sqlite_to_supabase.py --reset  # Inicializa el wallet en Supabase a 10.0€ limpio
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Configurar stdout y stderr en utf-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

from src.database.db import DatabaseManager
from src.database.supabase_client import SupabaseManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Migrate-Supabase")


def main():
    parser = argparse.ArgumentParser(description="TradIA - Migración de SQLite a Supabase")
    parser.add_argument("--reset", action="store_true", help="Reinicia el wallet en Supabase a 10.0€ limpios sin migrar historial")
    args = parser.parse_args()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    if not supabase_url or not supabase_key:
        print("❌ Error: Debes configurar SUPABASE_URL y SUPABASE_KEY en tu archivo .env.")
        sys.exit(1)

    sp_manager = SupabaseManager(supabase_url=supabase_url, supabase_key=supabase_key)

    if args.reset:
        logger.info("Reiniciando wallet de simulación en Supabase a 10.0€...")
        wallet = sp_manager.init_wallet(initial_balance=10.0, force_reset=True)
        print("✅ Wallet en Supabase inicializado con éxito:")
        print(f"   • Balance efectivo: {wallet['cash_balance']:.2f}€")
        print(f"   • Colchón activo:   {bool(wallet['floor_activated'])}")
        return

    sqlite_db_path = root_dir / "data" / "tradia.db"
    if not sqlite_db_path.exists():
        print(f"ℹ️ No se encontró base de datos local en {sqlite_db_path}. Inicializando wallet en Supabase a 10.0€...")
        sp_manager.init_wallet(initial_balance=10.0)
        return

    local_db = DatabaseManager(db_path=str(sqlite_db_path), use_supabase=False)
    print("=" * 70)
    print(" 🚀 MIGRANDO ESTADO LOCAL DE SQLITE A SUPABASE (POSTGRESQL)")
    print("=" * 70)

    # 1. Migrar Wallet
    local_wallet = local_db.get_wallet()
    sp_manager.client.table("sim_wallet").upsert({
        "id": 1,
        "cash_balance": float(local_wallet["cash_balance"]),
        "initial_balance": float(local_wallet["initial_balance"]),
        "floor_activated": bool(local_wallet.get("floor_activated", 0)),
        "is_halted": bool(local_wallet.get("is_halted", 0)),
        "halt_reason": str(local_wallet.get("halt_reason", "")),
        "start_date": str(local_wallet["start_date"]),
        "updated_at": str(local_wallet["updated_at"]),
    }).execute()
    print(f"✅ Wallet migrado: Balance={local_wallet['cash_balance']:.2f}€ | Colchón={bool(local_wallet.get('floor_activated'))}")

    # 2. Migrar Posiciones Abiertas
    open_positions = local_db.get_open_positions()
    for p in open_positions:
        sp_manager.add_position(
            symbol=p["symbol"],
            qty=float(p["qty"]),
            entry_price=float(p["entry_price"]),
            entry_time=p["entry_time"],
            entry_reasons=p["entry_reasons"] if isinstance(p["entry_reasons"], list) else json.loads(p["entry_reasons"]),
            entry_fee=float(p["entry_fee"]),
            position_cost_usdt=float(p["position_cost_usdt"]),
        )
    print(f"✅ Posiciones abiertas migradas: {len(open_positions)}")

    # 3. Migrar Trades Históricos
    trades = local_db.get_trades()
    for t in trades:
        sp_manager.record_closed_trade(t)
    print(f"✅ Historial de trades migrado: {len(trades)} operaciones")

    # 4. Migrar Snapshots Diarios
    snapshots = local_db.get_daily_snapshots()
    for s in snapshots:
        sp_manager.save_daily_snapshot(
            date_str=s["date"],
            closing_balance=float(s["closing_balance"]),
            net_pnl_usdt=float(s["net_pnl_usdt"]),
            net_pnl_pct=float(s["net_pnl_pct"]),
            trades_count=int(s["trades_count"]),
            win_rate_pct=float(s["win_rate_pct"]),
        )
    print(f"✅ Instantáneas diarias migradas: {len(snapshots)}")

    print("=" * 70)
    print("🎉 Migración a Supabase completada con éxito.")
    print("=" * 70)


if __name__ == "__main__":
    main()
