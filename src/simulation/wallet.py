"""Cartera y Wallet Ficticio para Simulación (Paper Trading).

Características clave:
- Capital inicial de 10.0€ / USDT.
- Exclusividad global: Solo 1 posición abierta a la vez en toda la cartera.
- Tamaño: 100% del capital operable.
- Colchón dinámico: Inactivo al inicio (opera con los 10€); en cuanto el balance
  alcanza >= 12.0€, se blindan automáticamente 6.0€ que nunca se arriesgan.
- Circuit breaker: Si estando el colchón activo el balance cae de 12.0€ (o de 6.0€ al inicio),
  se detiene la operativa automáticamente para revisión manual.
- Comisión (0.1%) y deslizamiento simulado (0.05%).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.config import SimulationConfig
from src.database.db import DatabaseManager

logger = logging.getLogger(__name__)


class SimulatedWallet:
    """Gestiona la cartera ficticia, el colchón dinámico y la ejecución de órdenes simuladas."""

    def __init__(
        self,
        db: DatabaseManager,
        config: SimulationConfig,
        on_circuit_breaker: Optional[Callable[[str, float, float], None]] = None,
    ):
        self.db = db
        self.config = config
        self.on_circuit_breaker = on_circuit_breaker
        self._ensure_wallet_initialized()

    def _ensure_wallet_initialized(self) -> None:
        """Inicializa el wallet en base de datos si no existe."""
        self.db.init_wallet(initial_balance=self.config.initial_balance_usdt)

    def get_portfolio_status(self, current_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Calcula el estado consolidado: efectivo, valor de posiciones y total."""
        wallet = self.db.get_wallet()
        cash = float(wallet["cash_balance"])
        positions = self.db.get_open_positions()

        invested_cost = 0.0
        current_market_value = 0.0

        for pos in positions:
            cost = float(pos["position_cost_usdt"])
            invested_cost += cost
            sym = pos["symbol"]
            qty = float(pos["qty"])
            if current_prices and sym in current_prices:
                current_market_value += qty * float(current_prices[sym])
            else:
                current_market_value += cost

        total_equity = cash + current_market_value
        unrealized_pnl = current_market_value - invested_cost
        unrealized_pnl_pct = (unrealized_pnl / invested_cost * 100.0) if invested_cost > 0 else 0.0

        return {
            "cash_balance": cash,
            "invested_cost": invested_cost,
            "current_market_value": current_market_value,
            "total_equity": total_equity,
            "initial_balance": float(wallet["initial_balance"]),
            "floor_activated": bool(wallet.get("floor_activated", 0)),
            "is_halted": bool(wallet.get("is_halted", 0)),
            "halt_reason": wallet.get("halt_reason", ""),
            "reserve_floor_eur": self.config.reserve_floor_eur,
            "min_order_size_eur": self.config.min_order_size_eur,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "open_positions_count": len(positions),
            "start_date": wallet["start_date"],
        }

    def open_simulated_buy(
        self,
        symbol: str,
        signal_price: float,
        timestamp: datetime,
        reasons: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Simula la apertura de una posición larga al precio de la señal.

        Aplica:
        1. Regla de exclusividad global (máx. 1 posición abierta en toda la cartera).
        2. Lógica de colchón dinámico y comprobación de orden mínima.
        3. Deslizamiento (+slippage) y comisión de compra.
        """
        wallet = self.db.get_wallet()

        # 1. Comprobar si el sistema está pausado por circuit breaker
        if bool(wallet.get("is_halted", 0)):
            logger.warning(
                f"[SIMULACIÓN PAUSADA] Operaciones bloqueadas por circuit breaker: {wallet.get('halt_reason')}. "
                f"Ignorando señal de {symbol}."
            )
            return None

        # 2. Exclusividad global: solo 1 posición abierta en toda la cartera
        all_open_positions = self.db.get_open_positions()
        if len(all_open_positions) > 0:
            open_symbols = [p["symbol"] for p in all_open_positions]
            logger.info(
                f"[SIMULACIÓN] Ya existe una posición abierta ({', '.join(open_symbols)}). "
                f"Regla de exclusividad: máximo 1 posición simultánea. Señal de {symbol} omitida."
            )
            return None

        cash_balance = float(wallet["cash_balance"])
        floor_activated = bool(wallet.get("floor_activated", 0))

        # 3. Comprobar activación dinámica del colchón (si balance alcanza >= 12.0€)
        if not floor_activated and cash_balance >= self.config.floor_activation_threshold_eur:
            logger.info(
                f"🛡️ [COLCHÓN ACTIVADO] El balance (${cash_balance:.2f}€) ha alcanzado el umbral "
                f"de activación (${self.config.floor_activation_threshold_eur:.2f}€). "
                f"A partir de ahora ${self.config.reserve_floor_eur:.2f}€ quedan blindados e intocables."
            )
            self.db.set_floor_activated(True)
            floor_activated = True

        # 4. Calcular capital operable
        if floor_activated:
            capital_operable = cash_balance - self.config.reserve_floor_eur
            # Circuit breaker: el margen sobre el colchón no alcanza el mínimo de orden (6€)
            if capital_operable < self.config.min_order_size_eur:
                reason = (
                    f"Margen operable ({capital_operable:.2f}€) inferior al mínimo de orden "
                    f"({self.config.min_order_size_eur:.2f}€) respetando el colchón ({self.config.reserve_floor_eur:.2f}€)."
                )
                logger.error(f"🚨 [CIRCUIT BREAKER] {reason} Pausando sistema.")
                self.db.set_system_halted(True, reason=reason)
                if self.on_circuit_breaker:
                    self.on_circuit_breaker(reason, cash_balance, self.config.reserve_floor_eur)
                return None
        else:
            capital_operable = cash_balance
            # Antes de activar colchón: si el capital cae por debajo de 6€, no llega a orden mínima
            if capital_operable < self.config.min_order_size_eur:
                reason = (
                    f"Balance disponible ({capital_operable:.2f}€) inferior al mínimo de orden "
                    f"({self.config.min_order_size_eur:.2f}€)."
                )
                logger.error(f"⚠️ [CAPITAL INSUFICIENTE] {reason} Pausando sistema.")
                self.db.set_system_halted(True, reason=reason)
                if self.on_circuit_breaker:
                    self.on_circuit_breaker(reason, cash_balance, 0.0)
                return None

        # 5. Tamaño de posición: 100% del capital operable
        position_size_eur = capital_operable * (self.config.position_size_pct / 100.0)

        # 6. Deslizamiento simulado (+slippage)
        slippage_factor = 1.0 + (self.config.slippage_pct / 100.0)
        execution_price = signal_price * slippage_factor

        # 7. Comisión de compra (0.1%)
        fee_pct = self.config.fee_pct / 100.0
        entry_fee = position_size_eur * fee_pct
        total_deduction = position_size_eur + entry_fee

        if capital_operable < total_deduction:
            # Ajustar para que coste + comisión no exceda el capital operable
            position_size_eur = capital_operable / (1.0 + fee_pct)
            entry_fee = position_size_eur * fee_pct
            total_deduction = capital_operable

        qty = position_size_eur / execution_price

        # 8. Descontar efectivo del wallet y guardar posición
        new_cash = cash_balance - total_deduction
        self.db.update_wallet_cash(new_cash)

        ts_str = timestamp.isoformat()
        self.db.add_position(
            symbol=symbol,
            qty=qty,
            entry_price=execution_price,
            entry_time=ts_str,
            entry_reasons=reasons,
            entry_fee=entry_fee,
            position_cost_usdt=position_size_eur,
        )

        logger.info(
            f"[SIMULACIÓN - COMPRA] {symbol} | Invertidos: {position_size_eur:.2f}€ | "
            f"Precio: ${execution_price:,.2f} | Cantidad: {qty:.6f} | "
            f"Colchón activo: {floor_activated} | Efectivo restante: {new_cash:.2f}€"
        )

        return {
            "action": "BUY",
            "symbol": symbol,
            "entry_price": execution_price,
            "signal_price": signal_price,
            "qty": qty,
            "position_size_usdt": position_size_eur,
            "entry_fee": entry_fee,
            "timestamp": ts_str,
            "reasons": reasons,
            "capital_operable": capital_operable,
            "floor_activated": floor_activated,
        }

    def close_simulated_position(
        self,
        symbol: str,
        signal_price: float,
        timestamp: datetime,
        exit_reasons: List[str],
        exit_tag: str = "SELL_SIGNAL",
    ) -> Optional[Dict[str, Any]]:
        """Simula el cierre de una posición abierta.

        Aplica deslizamiento (-slippage) y comisión de venta.
        Calcula P&L neto honesto y explícito.
        """
        pos = self.db.get_open_position(symbol)
        if not pos:
            return None

        qty = float(pos["qty"])
        entry_price = float(pos["entry_price"])
        entry_cost = float(pos["position_cost_usdt"])
        entry_fee = float(pos["entry_fee"])

        # 1. Deslizamiento simulado de salida (-slippage)
        slippage_factor = 1.0 - (self.config.slippage_pct / 100.0)
        execution_price = signal_price * slippage_factor

        # 2. Valor bruto de venta y comisión de salida (0.1%)
        gross_exit_value = qty * execution_price
        fee_pct = self.config.fee_pct / 100.0
        exit_fee = gross_exit_value * fee_pct
        net_exit_cash = gross_exit_value - exit_fee
        total_fees = entry_fee + exit_fee

        # 3. P&L honesto y sin maquillar
        gross_pnl = gross_exit_value - entry_cost
        net_pnl = net_exit_cash - entry_cost
        net_pnl_pct = (net_pnl / entry_cost) * 100.0 if entry_cost > 0 else 0.0

        # 4. Actualizar wallet
        wallet = self.db.get_wallet()
        current_cash = float(wallet["cash_balance"])
        new_cash = current_cash + net_exit_cash
        self.db.update_wallet_cash(new_cash)

        # 5. Comprobar colchón dinámico y circuit breaker tras la venta
        floor_activated = bool(wallet.get("floor_activated", 0))
        if floor_activated:
            capital_operable = new_cash - self.config.reserve_floor_eur
            if capital_operable < self.config.min_order_size_eur:
                reason = (
                    f"Margen operable ({capital_operable:.2f}€) inferior al mínimo de orden "
                    f"({self.config.min_order_size_eur:.2f}€) respetando el colchón ({self.config.reserve_floor_eur:.2f}€)."
                )
                logger.error(f"🚨 [CIRCUIT BREAKER TRAS VENTA] {reason} Pausando sistema.")
                self.db.set_system_halted(True, reason=reason)
                if self.on_circuit_breaker:
                    self.on_circuit_breaker(reason, new_cash, self.config.reserve_floor_eur)
        elif not floor_activated and new_cash >= self.config.floor_activation_threshold_eur:
            logger.info(
                f"🛡️ [COLCHÓN ACTIVADO TRAS OPERACIÓN] El balance ha alcanzado {new_cash:.2f}€ "
                f"(>= {self.config.floor_activation_threshold_eur:.2f}€). Colchón de 6.0€ blindado."
            )
            self.db.set_floor_activated(True)
            floor_activated = True
        elif not floor_activated and new_cash < self.config.min_order_size_eur:
            reason = (
                f"Balance disponible ({new_cash:.2f}€) inferior al mínimo de orden "
                f"({self.config.min_order_size_eur:.2f}€)."
            )
            logger.error(f"⚠️ [CAPITAL INSUFICIENTE TRAS VENTA] {reason} Pausando sistema.")
            self.db.set_system_halted(True, reason=reason)
            if self.on_circuit_breaker:
                self.on_circuit_breaker(reason, new_cash, 0.0)

        # 6. Archivar trade en SQLite y eliminar posición abierta
        ts_str = timestamp.isoformat()
        all_exit_reasons = [f"Tipo de salida: {exit_tag}"] + exit_reasons

        trade_record = {
            "symbol": symbol,
            "action": "BUY",
            "entry_time": pos["entry_time"],
            "exit_time": ts_str,
            "entry_price": entry_price,
            "exit_price": execution_price,
            "qty": qty,
            "position_size_usdt": entry_cost,
            "total_fees_usdt": total_fees,
            "gross_pnl_usdt": gross_pnl,
            "net_pnl_usdt": net_pnl,
            "net_pnl_pct": net_pnl_pct,
            "entry_reasons": pos["entry_reasons"],
            "exit_reasons": all_exit_reasons,
            "net_exit_cash": net_exit_cash,
        }

        self.db.record_closed_trade(trade_record)
        self.db.remove_position(symbol)

        logger.info(
            f"[SIMULACIÓN - VENTA] {symbol} ({exit_tag}) | Recibidos: {net_exit_cash:.2f}€ | "
            f"P&L Neto: {net_pnl:+.2f}€ ({net_pnl_pct:+.2f}%) | Nuevo Efectivo: {new_cash:.2f}€"
        )

        return trade_record

    def force_close_all(
        self,
        current_prices: Dict[str, float],
        timestamp: datetime,
        reason: str = "CIERRE_FORZADO_2245",
    ) -> List[Dict[str, Any]]:
        """Cierra obligatoriamente la posición abierta antes de las 23:00."""
        closed_trades = []
        open_positions = self.db.get_open_positions()

        for pos in open_positions:
            sym = pos["symbol"]
            curr_p = current_prices.get(sym, float(pos["entry_price"]))
            trade = self.close_simulated_position(
                symbol=sym,
                signal_price=curr_p,
                timestamp=timestamp,
                exit_reasons=["Cierre forzado 22:45 para evitar exposición nocturna"],
                exit_tag=reason,
            )
            if trade:
                closed_trades.append(trade)

        return closed_trades
