"""Gestor de base de datos Supabase (PostgreSQL en la nube) para TradIA.

Implementa la misma interfaz pública que DatabaseManager:
- Estado del wallet virtual
- Posiciones abiertas y cerradas (trades)
- Señales técnicas
- Instantáneas de rendimiento diario
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

try:
    from supabase import create_client, Client
except ImportError:
    create_client = None
    Client = None

logger = logging.getLogger(__name__)


class SupabaseManager:
    """Implementación de almacenamiento persistente en la nube usando Supabase."""

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
    ):
        if create_client is None:
            raise ImportError(
                "La librería 'supabase' no está instalada. Ejecuta: pip install supabase>=2.30.0"
            )

        self.url = (supabase_url or os.getenv("SUPABASE_URL") or "").strip()
        self.key = (supabase_key or os.getenv("SUPABASE_KEY") or "").strip()

        if not self.url or not self.key:
            raise ValueError(
                "Se requieren SUPABASE_URL y SUPABASE_KEY en las variables de entorno (.env) para SupabaseManager."
            )

        self.client: Client = create_client(self.url, self.key)
        logger.info(f"SupabaseManager conectado exitosamente a {self.url[:30]}...")

    # --- Métodos de Wallet Simulado ---
    def init_wallet(self, initial_balance: float = 10.0, force_reset: bool = False) -> Dict[str, Any]:
        """Inicializa el wallet en Supabase si no existe, o lo reinicia si force_reset=True."""
        now_str = datetime.now(timezone.utc).isoformat()
        res = self.client.table("sim_wallet").select("*").eq("id", 1).execute()

        if not res.data or force_reset:
            if force_reset:
                # Limpiar posiciones y trades previos
                self.client.table("sim_positions").delete().neq("id", -1).execute()
                self.client.table("sim_trades").delete().neq("id", -1).execute()
                self.client.table("sim_daily_snapshots").delete().neq("date", "").execute()

            row = {
                "id": 1,
                "cash_balance": initial_balance,
                "initial_balance": initial_balance,
                "floor_activated": False,
                "is_halted": False,
                "halt_reason": "",
                "start_date": now_str,
                "updated_at": now_str,
            }
            upsert_res = self.client.table("sim_wallet").upsert(row).execute()
            logger.info(f"Wallet de simulación inicializado en Supabase con {initial_balance:.2f}€.")
            return self._normalize_wallet_dict(upsert_res.data[0] if upsert_res.data else row)

        return self._normalize_wallet_dict(res.data[0])

    def get_wallet(self) -> Dict[str, Any]:
        """Recupera el estado actual del wallet desde Supabase."""
        res = self.client.table("sim_wallet").select("*").eq("id", 1).execute()
        if not res.data:
            return self.init_wallet()
        return self._normalize_wallet_dict(res.data[0])

    @staticmethod
    def _normalize_wallet_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normaliza tipos para mantener compatibilidad 100% con SQLite."""
        return {
            "id": int(raw.get("id", 1)),
            "cash_balance": float(raw.get("cash_balance", 10.0)),
            "initial_balance": float(raw.get("initial_balance", 10.0)),
            "floor_activated": 1 if raw.get("floor_activated") else 0,
            "is_halted": 1 if raw.get("is_halted") else 0,
            "halt_reason": str(raw.get("halt_reason") or ""),
            "start_date": str(raw.get("start_date") or ""),
            "updated_at": str(raw.get("updated_at") or ""),
        }

    def update_wallet_cash(self, new_balance: float) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        self.client.table("sim_wallet").update({
            "cash_balance": new_balance,
            "updated_at": now_str,
        }).eq("id", 1).execute()

    def set_floor_activated(self, activated: bool = True) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        self.client.table("sim_wallet").update({
            "floor_activated": bool(activated),
            "updated_at": now_str,
        }).eq("id", 1).execute()

    def set_system_halted(self, halted: bool = True, reason: str = "") -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        self.client.table("sim_wallet").update({
            "is_halted": bool(halted),
            "halt_reason": reason,
            "updated_at": now_str,
        }).eq("id", 1).execute()

    # --- Métodos de Posiciones Abiertas ---
    def get_open_positions(self) -> List[Dict[str, Any]]:
        res = self.client.table("sim_positions").select("*").execute()
        positions = []
        for r in (res.data or []):
            item = dict(r)
            if isinstance(item.get("entry_reasons"), str):
                try:
                    item["entry_reasons"] = json.loads(item["entry_reasons"])
                except Exception:
                    pass
            positions.append(item)
        return positions

    def get_open_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        res = self.client.table("sim_positions").select("*").eq("symbol", symbol).execute()
        if not res.data:
            return None
        item = dict(res.data[0])
        if isinstance(item.get("entry_reasons"), str):
            try:
                item["entry_reasons"] = json.loads(item["entry_reasons"])
            except Exception:
                pass
        return item

    def add_position(
        self,
        symbol: str,
        qty: float,
        entry_price: float,
        entry_time: str,
        entry_reasons: List[str],
        entry_fee: float,
        position_cost_usdt: float,
    ) -> None:
        row = {
            "symbol": symbol,
            "qty": qty,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "entry_reasons": entry_reasons,
            "entry_fee": entry_fee,
            "position_cost_usdt": position_cost_usdt,
        }
        self.client.table("sim_positions").upsert(row, on_conflict="symbol").execute()

    def remove_position(self, symbol: str) -> None:
        self.client.table("sim_positions").delete().eq("symbol", symbol).execute()

    # --- Métodos de Trades Simulados ---
    def record_closed_trade(self, trade_data: Dict[str, Any]) -> int:
        entry_reasons = trade_data.get("entry_reasons", [])
        exit_reasons = trade_data.get("exit_reasons", [])
        row = {
            "symbol": trade_data["symbol"],
            "action": trade_data.get("action", "BUY"),
            "entry_time": trade_data["entry_time"],
            "exit_time": trade_data["exit_time"],
            "entry_price": float(trade_data["entry_price"]),
            "exit_price": float(trade_data["exit_price"]),
            "qty": float(trade_data["qty"]),
            "position_size_usdt": float(trade_data["position_size_usdt"]),
            "total_fees_usdt": float(trade_data.get("total_fees_usdt", 0.0)),
            "gross_pnl_usdt": float(trade_data["gross_pnl_usdt"]),
            "net_pnl_usdt": float(trade_data["net_pnl_usdt"]),
            "net_pnl_pct": float(trade_data["net_pnl_pct"]),
            "entry_reasons": entry_reasons if isinstance(entry_reasons, list) else [str(entry_reasons)],
            "exit_reasons": exit_reasons if isinstance(exit_reasons, list) else [str(exit_reasons)],
        }
        res = self.client.table("sim_trades").insert(row).execute()
        return res.data[0]["id"] if res.data else 1

    def get_trades(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = self.client.table("sim_trades").select("*").order("exit_time", desc=False)
        if start_date:
            query = query.gte("exit_time", start_date)
        if end_date:
            query = query.lte("exit_time", end_date)
        res = query.execute()

        results = []
        for r in (res.data or []):
            item = dict(r)
            if isinstance(item.get("entry_reasons"), str):
                try:
                    item["entry_reasons"] = json.loads(item["entry_reasons"])
                except Exception:
                    pass
            if isinstance(item.get("exit_reasons"), str):
                try:
                    item["exit_reasons"] = json.loads(item["exit_reasons"])
                except Exception:
                    pass
            results.append(item)
        return results

    # --- Instantáneas Diarias ---
    def save_daily_snapshot(
        self,
        date_str: str,
        closing_balance: float,
        net_pnl_usdt: float,
        net_pnl_pct: float,
        trades_count: int,
        win_rate_pct: float,
    ) -> None:
        now_str = datetime.now(timezone.utc).isoformat()
        row = {
            "date": date_str,
            "closing_balance": closing_balance,
            "net_pnl_usdt": net_pnl_usdt,
            "net_pnl_pct": net_pnl_pct,
            "trades_count": trades_count,
            "win_rate_pct": win_rate_pct,
            "created_at": now_str,
        }
        self.client.table("sim_daily_snapshots").upsert(row, on_conflict="date").execute()

    def get_daily_snapshots(self) -> List[Dict[str, Any]]:
        res = self.client.table("sim_daily_snapshots").select("*").order("date", desc=False).execute()
        return res.data or []

    # --- Métodos de Señales ---
    def record_signal(
        self,
        timestamp: datetime,
        symbol: str,
        action: str,
        price: float,
        score: int,
        reasons: List[str],
        metrics: Dict[str, Any],
    ) -> int:
        now_str = datetime.now(timezone.utc).isoformat()
        row = {
            "timestamp": timestamp.isoformat(),
            "symbol": symbol,
            "action": action,
            "price": price,
            "score": score,
            "reasons": reasons,
            "metrics": metrics,
            "created_at": now_str,
        }
        res = self.client.table("signals").insert(row).execute()
        return res.data[0]["id"] if res.data else 1

    def get_signals_count(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> int:
        query = self.client.table("signals").select("id", count="exact")
        if start_date:
            query = query.gte("timestamp", start_date)
        if end_date:
            query = query.lte("timestamp", end_date)
        res = query.execute()
        return res.count if res.count is not None else len(res.data or [])

    # --- Posiciones de Usuario ---
    def confirm_user_position(
        self,
        symbol: str,
        entry_price: float,
        side: str = "BUY",
        size_usdt: Optional[float] = None,
        notes: str = "",
    ) -> int:
        now_str = datetime.now(timezone.utc).isoformat()
        row = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "entry_time": now_str,
            "size_usdt": size_usdt,
            "notes": notes,
            "is_open": 1,
        }
        res = self.client.table("user_confirmed_positions").insert(row).execute()
        return res.data[0]["id"] if res.data else 1

    def get_open_user_positions(self) -> List[Dict[str, Any]]:
        res = self.client.table("user_confirmed_positions").select("*").eq("is_open", 1).execute()
        return res.data or []

    def close_user_position(self, symbol: str, close_price: Optional[float] = None) -> int:
        now_str = datetime.now(timezone.utc).isoformat()
        update_data = {"is_open": 0, "closed_at": now_str}
        if close_price:
            update_data["close_price"] = close_price
        res = self.client.table("user_confirmed_positions").update(update_data).eq("symbol", symbol).eq("is_open", 1).execute()
        return len(res.data or [])
