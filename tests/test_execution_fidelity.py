import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.exchanges.base import BaseExchange
from core.order_manager import place_long_order, place_short_order, check_exchange_active_position
from config.settings import BotSettings
from core.risk_manager import calculate_dynamic_atr_targets


class TestExecutionFidelity(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_client = AsyncMock(spec=BaseExchange)
        self.mock_client.exchange_name = "BITUNIX"
        self.mock_client.get_open_positions = AsyncMock(return_value=[])
        self.mock_client.get_symbol_price = AsyncMock(return_value=100.0)
        self.mock_client.set_leverage = AsyncMock(return_value=10)
        self.mock_client.set_margin_type = AsyncMock()
        self.mock_client.get_symbol_precision = AsyncMock(return_value={"qty": 2, "price": 2, "min_qty": 0.01})
        self.mock_client.place_order = AsyncMock(return_value={"orderId": "12345", "status": "FILLED", "avgPrice": "99.60"})
        self.mock_client.get_order = AsyncMock(return_value={"orderId": "12345", "status": "FILLED", "avgPrice": "99.60"})
        self.mock_client.cancel_order = AsyncMock(return_value={"status": "success"})

    async def test_smart_limit_order_long_execution(self):
        """Test Limit Order LONG berhasil dipasang pada harga diskon pullback."""
        res = await place_long_order(
            client=self.mock_client,
            symbol="SOLUSDT",
            current_price=100.0,
            margin_usdt=10.0,
            leverage=10,
            use_limit=True,
            limit_timeout=5,
            limit_price=99.60,
        )
        self.assertEqual(res["status"], "success")
        self.mock_client.place_order.assert_called_once()
        call_kwargs = self.mock_client.place_order.call_args.kwargs
        self.assertEqual(call_kwargs["order_type"], "LIMIT")
        self.assertEqual(call_kwargs["price"], 99.60)
        self.assertEqual(call_kwargs["side"], "BUY")

    async def test_smart_limit_order_short_execution(self):
        """Test Limit Order SHORT berhasil dipasang pada harga diskon pullback."""
        self.mock_client.place_order = AsyncMock(return_value={"orderId": "12346", "status": "FILLED", "avgPrice": "100.40"})
        self.mock_client.get_order = AsyncMock(return_value={"orderId": "12346", "status": "FILLED", "avgPrice": "100.40"})
        res = await place_short_order(
            client=self.mock_client,
            symbol="SOLUSDT",
            current_price=100.0,
            margin_usdt=10.0,
            leverage=10,
            use_limit=True,
            limit_timeout=5,
            limit_price=100.40,
        )
        self.assertEqual(res["status"], "success")
        self.mock_client.place_order.assert_called_once()
        call_kwargs = self.mock_client.place_order.call_args.kwargs
        self.assertEqual(call_kwargs["order_type"], "LIMIT")
        self.assertEqual(call_kwargs["price"], 100.40)
        self.assertEqual(call_kwargs["side"], "SELL")

    async def test_anti_double_entry_lock(self):
        """Test bot menolak order jika simbol sudah memiliki posisi aktif di exchange."""
        self.mock_client.get_open_positions = AsyncMock(return_value=[
            {"symbol": "SOLUSDT", "position_amt": 1.0, "side": "LONG"}
        ])
        res = await place_long_order(
            client=self.mock_client,
            symbol="SOLUSDT",
            current_price=100.0,
            margin_usdt=10.0,
            leverage=10,
        )
        self.assertEqual(res["status"], "error")
        self.assertIn("already active", res["message"])

    def test_dynamic_atr_targets_calculation(self):
        """Test dynamic ATR Stop Loss calculation adapting to volatility."""
        # Test volatilitas tinggi (ATR = 3.0 pada harga 100)
        targets_high_vol = calculate_dynamic_atr_targets(
            entry_price=100.0,
            atr_value=3.0,
            side="LONG",
            leverage=10,
            config_tp_percent=40.0,
            config_sl_percent=25.0,
            atr_sl_mult=1.5,
        )
        # 3.0 * 1.5 = 4.5% SL distance
        self.assertAlmostEqual(targets_high_vol["sl_price"], 95.50, places=1)
        self.assertTrue(targets_high_vol["tp_price"] > 100.0)


if __name__ == "__main__":
    unittest.main()
