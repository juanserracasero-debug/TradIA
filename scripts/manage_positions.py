"""CLI para que el usuario confirme y registre manualmente las posiciones abiertas.

Permite al sistema saber qué operaciones siguió el usuario para avisarle
de cerrarlas obligatoriamente a las 22:45 antes de la noche.

Uso:
    python scripts/manage_positions.py --list
    python scripts/manage_positions.py --confirm-buy BTC/USDT --price 86250 --size 1000
    python scripts/manage_positions.py --close BTC/USDT --price 87500
"""
import argparse
import sys
from pathlib import Path
from tabulate import tabulate

# Configurar stdout y stderr en utf-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from src.database.db import DatabaseManager


def main():
    parser = argparse.ArgumentParser(description="TradIA - Registro Manual de Posiciones del Usuario")
    parser.add_argument("--list", action="store_true", help="Listar posiciones actualmente abiertas por el usuario")
    parser.add_argument("--confirm-buy", type=str, metavar="SYMBOL", help="Confirmar que seguiste la señal y compraste (ej. BTC/USDT)")
    parser.add_argument("--price", type=float, help="Precio real de entrada o salida")
    parser.add_argument("--size", type=float, default=None, help="Importe en USDT invertido")
    parser.add_argument("--notes", type=str, default="", help="Notas u observaciones de la operación")
    parser.add_argument("--close", type=str, metavar="SYMBOL", help="Confirmar el cierre manual de una posición")

    args = parser.parse_args()
    db = DatabaseManager()

    if args.confirm_buy:
        if not args.price:
            print("❌ Error: Debes indicar el precio de entrada con --price (ej. --price 86500)")
            return
        row_id = db.confirm_user_position(
            symbol=args.confirm_buy,
            entry_price=args.price,
            side="BUY",
            size_usdt=args.size,
            notes=args.notes,
        )
        print(f"✅ Posición confirmada y guardada con éxito (ID #{row_id}):")
        print(f"   • Activo: {args.confirm_buy}")
        print(f"   • Precio Entrada: ${args.price:,.2f}")
        if args.size:
            print(f"   • Importe: ${args.size:,.2f} USDT")
        print("   • Estado: ABIERTA (El bot te recordará cerrarla antes de las 23:00)")
        return

    if args.close:
        updated = db.close_user_position(symbol=args.close, close_price=args.price)
        if updated > 0:
            print(f"✅ Posición de {args.close} marcada como CERRADA.")
            if args.price:
                print(f"   • Precio Salida: ${args.price:,.2f}")
        else:
            print(f"⚠️ No se encontró ninguna posición abierta para {args.close}.")
        return

    # Por defecto o con --list: listar posiciones abiertas
    open_pos = db.get_open_user_positions()
    print("=" * 70)
    print(" 📋 POSICIONES ABIERTAS CONFIRMADAS POR EL USUARIO")
    print("=" * 70)
    if not open_pos:
        print("ℹ️ No tienes ninguna posición abierta registrada actualmente.")
        print("   Usa: python scripts/manage_positions.py --confirm-buy BTC/USDT --price PRECIO")
    else:
        table = []
        for p in open_pos:
            size_str = f"${p['size_usdt']:,.2f}" if p["size_usdt"] else "N/A"
            table.append([
                p["id"],
                p["symbol"],
                p["side"],
                f"${p['entry_price']:,.2f}",
                size_str,
                p["entry_time"],
                p["notes"] or "-",
            ])
        print(tabulate(
            table,
            headers=["ID", "Par", "Tipo", "Precio Entrada", "Tamaño USDT", "Fecha/Hora UTC", "Notas"],
            tablefmt="fancy_grid",
        ))
        print("🚨 Estas posiciones recibirán una alerta 'VENDE TODO' a las 22:45 si no las cierras antes.")
    print("=" * 70)


if __name__ == "__main__":
    main()
