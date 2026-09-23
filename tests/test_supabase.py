"""Pruebas unitarias para SupabaseManager utilizando mocks del cliente de Supabase."""
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.database.supabase_client import SupabaseManager


class TestSupabaseManager(unittest.TestCase):

    def setUp(self):
        self.mock_client = MagicMock()
        with patch("src.database.supabase_client.create_client", return_value=self.mock_client):
            self.manager = SupabaseManager(
                supabase_url="https://fake.supabase.co",
                supabase_key="fake-key",
            )

    def test_init_wallet_creates_if_not_exists(self):
        # Simular que no existe fila
        mock_select = MagicMock()
        mock_select.data = []
        self.mock_client.table().select().eq().execute.return_value = mock_select

        mock_upsert = MagicMock()
        mock_upsert.data = [{
            "id": 1,
            "cash_balance": 10.0,
            "initial_balance": 10.0,
            "floor_activated": False,
            "is_halted": False,
            "halt_reason": "",
            "start_date": "2026-09-23T00:00:00Z",
            "updated_at": "2026-09-23T00:00:00Z",
        }]
        self.mock_client.table().upsert().execute.return_value = mock_upsert

        wallet = self.manager.init_wallet(initial_balance=10.0)
        self.assertEqual(wallet["cash_balance"], 10.0)
        self.assertEqual(wallet["floor_activated"], 0)
        self.assertEqual(wallet["is_halted"], 0)

    def test_get_wallet(self):
        mock_select = MagicMock()
        mock_select.data = [{
            "id": 1,
            "cash_balance": 14.50,
            "initial_balance": 10.0,
            "floor_activated": True,
            "is_halted": False,
            "halt_reason": "",
            "start_date": "2026-09-23T00:00:00Z",
            "updated_at": "2026-09-23T12:00:00Z",
        }]
        self.mock_client.table().select().eq().execute.return_value = mock_select

        wallet = self.manager.get_wallet()
        self.assertEqual(wallet["cash_balance"], 14.50)
        self.assertEqual(wallet["floor_activated"], 1)

    def test_update_wallet_cash(self):
        self.manager.update_wallet_cash(12.34)
        self.mock_client.table().update.assert_called()

    def test_set_floor_activated(self):
        self.manager.set_floor_activated(True)
        self.mock_client.table().update.assert_called()

    def test_set_system_halted(self):
        self.manager.set_system_halted(True, reason="Circuit breaker activado")
        self.mock_client.table().update.assert_called()

    def test_positions_crud(self):
        # 1. Add position
        self.manager.add_position(
            symbol="BTC/USDT",
            qty=0.001,
            entry_price=85000.0,
            entry_time="2026-09-23T10:00:00Z",
            entry_reasons=["RSI bajo"],
            entry_fee=0.01,
            position_cost_usdt=8.5,
        )
        self.mock_client.table().upsert.assert_called()

        # 2. Get positions
        mock_select = MagicMock()
        mock_select.data = [{
            "symbol": "BTC/USDT",
            "qty": 0.001,
            "entry_price": 85000.0,
            "entry_time": "2026-09-23T10:00:00Z",
            "entry_reasons": '["RSI bajo"]',
            "entry_fee": 0.01,
            "position_cost_usdt": 8.5,
        }]
        self.mock_client.table().select().execute.return_value = mock_select
        positions = self.manager.get_open_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "BTC/USDT")
        self.assertEqual(positions[0]["entry_reasons"], ["RSI bajo"])

        # 3. Remove position
        self.manager.remove_position("BTC/USDT")
        self.mock_client.table().delete().eq.assert_called()

    def test_record_closed_trade(self):
        mock_insert = MagicMock()
        mock_insert.data = [{"id": 42}]
        self.mock_client.table().insert().execute.return_value = mock_insert

        trade_id = self.manager.record_closed_trade({
            "symbol": "BTC/USDT",
            "action": "BUY",
            "entry_time": "2026-09-23T10:00:00Z",
            "exit_time": "2026-09-23T11:00:00Z",
            "entry_price": 85000.0,
            "exit_price": 87000.0,
            "qty": 0.001,
            "position_size_usdt": 8.5,
            "total_fees_usdt": 0.02,
            "gross_pnl_usdt": 0.20,
            "net_pnl_usdt": 0.18,
            "net_pnl_pct": 2.11,
            "entry_reasons": ["RSI"],
            "exit_reasons": ["TP"],
        })
        self.assertEqual(trade_id, 42)


if __name__ == "__main__":
    unittest.main()
