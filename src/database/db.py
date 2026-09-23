"""Gestor de persistencia híbrido para TradIA (Supabase en la nube / SQLite local).

IMPORTANTE — ARQUITECTURA LOCAL VS PRODUCCIÓN:
- En entorno LOCAL: NO definas SUPABASE_URL ni SUPABASE_KEY en el archivo .env.
  Al omitir estas variables, DatabaseManager selecciona de manera automática
  el backend SQLite local ('data/tradia.db'). Esto permite ejecutar pruebas,
  backtests y suites de tests de forma totalmente aislada sin riesgo de alterar
  o machacar los datos reales/productivos almacenados en la nube.
- En PRODUCCIÓN (GitHub Actions): Las variables SUPABASE_URL y SUPABASE_KEY
  se inyectan a través de los GitHub Repository Secrets, activando de forma
  transparente el backend SupabaseManager (PostgreSQL en Supabase).

Almacena:
- Señales generadas
- Estado del wallet ficticio (paper trading)
- Posiciones abiertas simuladas
- Registro histórico de operaciones cerradas con P&L
- Instantáneas diarias de rendimiento
- Posiciones confirmadas por el usuario
"""
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

try:
    from src.database.supabase_client import SupabaseManager
except ImportError:
    SupabaseManager = None

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Gestiona la conexión y operaciones de persistencia (Supabase en la nube / SQLite local)."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
        use_supabase: Optional[bool] = None,
    ):
        url = (supabase_url or os.getenv("SUPABASE_URL") or "").strip()
        key = (supabase_key or os.getenv("SUPABASE_KEY") or "").strip()

        should_use_supabase = (
            use_supabase
            if use_supabase is not None
            else (bool(url and key and SupabaseManager is not None) and db_path is None)
        )

        if should_use_supabase:
            self._backend = SupabaseManager(supabase_url=url, supabase_key=key)
            self.is_supabase = True
            logger.info("DatabaseManager operando en modo NUBE con Supabase.")
        else:
            self._backend = None
            self.is_supabase = False
            if db_path:
                self.db_path = Path(db_path)
            else:
                self.db_path = Path(__file__).resolve().parent.parent.parent / "data" / "tradia.db"
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()
            logger.info(f"DatabaseManager operando en modo LOCAL con SQLite ({self.db_path}).")

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """Crea las tablas necesarias si no existen."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Señales generadas
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    price REAL NOT NULL,
                    score INTEGER NOT NULL,
                    reasons TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # 2. Wallet virtual con estado de colchón y circuit breaker
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sim_wallet (
                    id INTEGER PRIMARY KEY,
                    cash_balance REAL NOT NULL,
                    initial_balance REAL NOT NULL,
                    floor_activated INTEGER NOT NULL DEFAULT 0,
                    is_halted INTEGER NOT NULL DEFAULT 0,
                    halt_reason TEXT DEFAULT '',
                    start_date TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # 3. Posiciones abiertas simuladas
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sim_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    qty REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_reasons TEXT NOT NULL,
                    entry_fee REAL NOT NULL,
                    position_cost_usdt REAL NOT NULL
                )
            """)

            # 4. Operaciones simuladas cerradas (trades)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sim_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    qty REAL NOT NULL,
                    position_size_usdt REAL NOT NULL,
                    total_fees_usdt REAL NOT NULL,
                    gross_pnl_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_pnl_pct REAL NOT NULL,
                    entry_reasons TEXT NOT NULL,
                    exit_reasons TEXT NOT NULL
                )
            """)

            # 5. Instantáneas diarias de rendimiento
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sim_daily_snapshots (
                    date TEXT PRIMARY KEY,
                    closing_balance REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_pnl_pct REAL NOT NULL,
                    trades_count INTEGER NOT NULL,
                    win_rate_pct REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # 6. Registro de posiciones abiertas confirmadas por el usuario real
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_confirmed_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL DEFAULT 'BUY',
                    entry_price REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    size_usdt REAL,
                    notes TEXT,
                    is_open INTEGER NOT NULL DEFAULT 1,
                    close_price REAL,
                    closed_at TEXT
                )
            """)

            # 7. Control de Estado Operativo y Franjas de Trading Remotas (Dashboard)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_control (
                    id INTEGER PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'active',
                    resume_at TEXT,
                    trading_window_1_start TEXT NOT NULL DEFAULT '08:30',
                    trading_window_1_end TEXT NOT NULL DEFAULT '17:30',
                    trading_window_2_start TEXT NOT NULL DEFAULT '20:30',
                    trading_window_2_end TEXT NOT NULL DEFAULT '23:00',
                    updated_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                INSERT OR IGNORE INTO bot_control
                (id, status, resume_at, trading_window_1_start, trading_window_1_end, trading_window_2_start, trading_window_2_end, updated_at)
                VALUES (1, 'active', NULL, '08:30', '17:30', '20:30', '23:00', datetime('now'))
            """)

            # Migración automática si la tabla sim_wallet ya existía con esquema antiguo
            cursor.execute("PRAGMA table_info(sim_wallet)")
            sim_cols = [row[1] for row in cursor.fetchall()]
            if "floor_activated" not in sim_cols:
                cursor.execute("ALTER TABLE sim_wallet ADD COLUMN floor_activated INTEGER NOT NULL DEFAULT 0")
            if "is_halted" not in sim_cols:
                cursor.execute("ALTER TABLE sim_wallet ADD COLUMN is_halted INTEGER NOT NULL DEFAULT 0")
            if "halt_reason" not in sim_cols:
                cursor.execute("ALTER TABLE sim_wallet ADD COLUMN halt_reason TEXT DEFAULT ''")

            conn.commit()

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
        if self._backend:
            return self._backend.record_signal(timestamp, symbol, action, price, score, reasons, metrics)

        now_str = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO signals (timestamp, symbol, action, price, score, reasons, metrics, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp.isoformat(),
                    symbol,
                    action,
                    price,
                    score,
                    json.dumps(reasons, ensure_ascii=False),
                    json.dumps(metrics),
                    now_str,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_signals_count(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> int:
        if self._backend:
            return self._backend.get_signals_count(start_date, end_date)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            if start_date and end_date:
                cursor.execute(
                    "SELECT COUNT(*) FROM signals WHERE timestamp >= ? AND timestamp <= ?",
                    (start_date, end_date),
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM signals")
            return cursor.fetchone()[0]

    # --- Métodos de Wallet Simulado ---
    def init_wallet(self, initial_balance: float = 10.0, force_reset: bool = False) -> Dict[str, Any]:
        """Inicializa el wallet si no existe, o lo reinicia si force_reset=True."""
        if self._backend:
            return self._backend.init_wallet(initial_balance, force_reset)

        now_str = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT cash_balance, initial_balance, floor_activated, is_halted, halt_reason, start_date, updated_at "
                "FROM sim_wallet WHERE id = 1"
            )
            row = cursor.fetchone()

            if row is None or force_reset:
                cursor.execute("DELETE FROM sim_positions")
                if force_reset:
                    cursor.execute("DELETE FROM sim_trades")
                    cursor.execute("DELETE FROM sim_daily_snapshots")

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO sim_wallet
                    (id, cash_balance, initial_balance, floor_activated, is_halted, halt_reason, start_date, updated_at)
                    VALUES (1, ?, ?, 0, 0, '', ?, ?)
                    """,
                    (initial_balance, initial_balance, now_str, now_str),
                )
                conn.commit()
                return {
                    "cash_balance": initial_balance,
                    "initial_balance": initial_balance,
                    "floor_activated": 0,
                    "is_halted": 0,
                    "halt_reason": "",
                    "start_date": now_str,
                    "updated_at": now_str,
                }
            return dict(row)

    def get_wallet(self) -> Dict[str, Any]:
        if self._backend:
            return self._backend.get_wallet()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT cash_balance, initial_balance, floor_activated, is_halted, halt_reason, start_date, updated_at "
                "FROM sim_wallet WHERE id = 1"
            )
            row = cursor.fetchone()
            if row is None:
                return self.init_wallet()
            return dict(row)

    def update_wallet_cash(self, new_balance: float) -> None:
        if self._backend:
            return self._backend.update_wallet_cash(new_balance)

        now_str = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sim_wallet SET cash_balance = ?, updated_at = ? WHERE id = 1",
                (new_balance, now_str),
            )
            conn.commit()

    def set_floor_activated(self, activated: bool = True) -> None:
        if self._backend:
            return self._backend.set_floor_activated(activated)

        now_str = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sim_wallet SET floor_activated = ?, updated_at = ? WHERE id = 1",
                (1 if activated else 0, now_str),
            )
            conn.commit()

    def set_system_halted(self, halted: bool = True, reason: str = "") -> None:
        if self._backend:
            return self._backend.set_system_halted(halted, reason)

        now_str = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sim_wallet SET is_halted = ?, halt_reason = ?, updated_at = ? WHERE id = 1",
                (1 if halted else 0, reason, now_str),
            )
            conn.commit()

    # --- Métodos de Posiciones Abiertas ---
    def get_open_positions(self) -> List[Dict[str, Any]]:
        if self._backend:
            return self._backend.get_open_positions()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sim_positions")
            return [dict(row) for row in cursor.fetchall()]

    def get_open_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        if self._backend:
            return self._backend.get_open_position(symbol)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sim_positions WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            return dict(row) if row else None

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
        if self._backend:
            return self._backend.add_position(
                symbol, qty, entry_price, entry_time, entry_reasons, entry_fee, position_cost_usdt
            )

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO sim_positions
                (symbol, qty, entry_price, entry_time, entry_reasons, entry_fee, position_cost_usdt)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    qty,
                    entry_price,
                    entry_time,
                    json.dumps(entry_reasons, ensure_ascii=False),
                    entry_fee,
                    position_cost_usdt,
                ),
            )
            conn.commit()

    def remove_position(self, symbol: str) -> None:
        if self._backend:
            return self._backend.remove_position(symbol)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sim_positions WHERE symbol = ?", (symbol,))
            conn.commit()

    # --- Métodos de Trades Simulados ---
    def record_closed_trade(self, trade_data: Dict[str, Any]) -> int:
        if self._backend:
            return self._backend.record_closed_trade(trade_data)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO sim_trades (
                    symbol, action, entry_time, exit_time, entry_price, exit_price,
                    qty, position_size_usdt, total_fees_usdt, gross_pnl_usdt,
                    net_pnl_usdt, net_pnl_pct, entry_reasons, exit_reasons
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_data["symbol"],
                    trade_data.get("action", "BUY"),
                    trade_data["entry_time"],
                    trade_data["exit_time"],
                    trade_data["entry_price"],
                    trade_data["exit_price"],
                    trade_data["qty"],
                    trade_data["position_size_usdt"],
                    trade_data["total_fees_usdt"],
                    trade_data["gross_pnl_usdt"],
                    trade_data["net_pnl_usdt"],
                    trade_data["net_pnl_pct"],
                    json.dumps(trade_data.get("entry_reasons", []), ensure_ascii=False)
                    if isinstance(trade_data.get("entry_reasons"), list)
                    else str(trade_data.get("entry_reasons")),
                    json.dumps(trade_data.get("exit_reasons", []), ensure_ascii=False)
                    if isinstance(trade_data.get("exit_reasons"), list)
                    else str(trade_data.get("exit_reasons")),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_trades(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self._backend:
            return self._backend.get_trades(start_date, end_date)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            if start_date and end_date:
                cursor.execute(
                    "SELECT * FROM sim_trades WHERE exit_time >= ? AND exit_time <= ? ORDER BY exit_time ASC",
                    (start_date, end_date),
                )
            elif start_date:
                cursor.execute(
                    "SELECT * FROM sim_trades WHERE exit_time >= ? ORDER BY exit_time ASC",
                    (start_date,),
                )
            else:
                cursor.execute("SELECT * FROM sim_trades ORDER BY exit_time ASC")

            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                try:
                    item["entry_reasons"] = json.loads(item["entry_reasons"])
                except Exception:
                    pass
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
        if self._backend:
            return self._backend.save_daily_snapshot(
                date_str, closing_balance, net_pnl_usdt, net_pnl_pct, trades_count, win_rate_pct
            )

        now_str = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO sim_daily_snapshots
                (date, closing_balance, net_pnl_usdt, net_pnl_pct, trades_count, win_rate_pct, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (date_str, closing_balance, net_pnl_usdt, net_pnl_pct, trades_count, win_rate_pct, now_str),
            )
            conn.commit()

    def get_daily_snapshots(self) -> List[Dict[str, Any]]:
        if self._backend:
            return self._backend.get_daily_snapshots()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sim_daily_snapshots ORDER BY date ASC")
            return [dict(r) for r in cursor.fetchall()]

    # --- Registro de Posición Abierta por el Usuario Real ---
    def confirm_user_position(
        self,
        symbol: str,
        entry_price: float,
        side: str = "BUY",
        size_usdt: Optional[float] = None,
        notes: str = "",
        timestamp: Optional[datetime] = None,
    ) -> int:
        """Registra que el usuario ejecutó manualmente una señal y tiene una posición abierta."""
        if self._backend:
            return self._backend.confirm_user_position(symbol, entry_price, side, size_usdt, notes)

        ts_str = (timestamp or datetime.now(timezone.utc)).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_confirmed_positions
                (symbol, side, entry_price, entry_time, size_usdt, notes, is_open)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (symbol, side, entry_price, size_str := ts_str, size_usdt, notes),
            )
            conn.commit()
            return cursor.lastrowid

    def get_open_user_positions(self) -> List[Dict[str, Any]]:
        """Devuelve todas las posiciones que el usuario marcó como abiertas."""
        if self._backend:
            return self._backend.get_open_user_positions()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_confirmed_positions WHERE is_open = 1")
            return [dict(r) for r in cursor.fetchall()]

    def close_user_position(
        self,
        symbol: str,
        close_price: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> int:
        """Marca como cerrada la posición abierta del usuario en ese símbolo."""
        if self._backend:
            return self._backend.close_user_position(symbol, close_price)

        ts_str = (timestamp or datetime.now(timezone.utc)).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE user_confirmed_positions
                SET is_open = 0, close_price = ?, closed_at = ?
                WHERE symbol = ? AND is_open = 1
                """,
                (close_price, ts_str, symbol),
            )
            conn.commit()
            return cursor.rowcount

    # --- Métodos de Control del Bot ---
    def get_bot_control(self) -> Dict[str, Any]:
        """Recupera el estado de control operativo del bot (Supabase o SQLite)."""
        if self._backend:
            return self._backend.get_bot_control()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM bot_control WHERE id = 1")
            row = cursor.fetchone()
            if row is None:
                now_str = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO bot_control
                    (id, status, resume_at, trading_window_1_start, trading_window_1_end, trading_window_2_start, trading_window_2_end, updated_at)
                    VALUES (1, 'active', NULL, '08:30', '17:30', '20:30', '23:00', ?)
                    """,
                    (now_str,),
                )
                conn.commit()
                return {
                    "id": 1,
                    "status": "active",
                    "resume_at": None,
                    "trading_window_1_start": "08:30",
                    "trading_window_1_end": "17:30",
                    "trading_window_2_start": "20:30",
                    "trading_window_2_end": "23:00",
                    "updated_at": now_str,
                }
            return dict(row)

    def update_bot_control(
        self,
        status: Optional[str] = None,
        resume_at: Optional[str] = None,
        trading_window_1_start: Optional[str] = None,
        trading_window_1_end: Optional[str] = None,
        trading_window_2_start: Optional[str] = None,
        trading_window_2_end: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Actualiza el estado de control del bot y/o sus franjas horarias."""
        if self._backend:
            return self._backend.update_bot_control(
                status=status,
                resume_at=resume_at,
                trading_window_1_start=trading_window_1_start,
                trading_window_1_end=trading_window_1_end,
                trading_window_2_start=trading_window_2_start,
                trading_window_2_end=trading_window_2_end,
            )

        current = self.get_bot_control()
        new_status = status if status is not None else current["status"]
        new_resume_at = resume_at if status != "active" else None
        new_w1_start = trading_window_1_start or current["trading_window_1_start"]
        new_w1_end = trading_window_1_end or current["trading_window_1_end"]
        new_w2_start = trading_window_2_start or current["trading_window_2_start"]
        new_w2_end = trading_window_2_end or current["trading_window_2_end"]
        now_str = datetime.now(timezone.utc).isoformat()

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bot_control
                SET status = ?, resume_at = ?, trading_window_1_start = ?, trading_window_1_end = ?,
                    trading_window_2_start = ?, trading_window_2_end = ?, updated_at = ?
                WHERE id = 1
                """,
                (new_status, new_resume_at, new_w1_start, new_w1_end, new_w2_start, new_w2_end, now_str),
            )
            conn.commit()

        return self.get_bot_control()
