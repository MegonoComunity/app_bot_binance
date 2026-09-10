import unittest

from core.order_manager import _get_average_fill_price
from core.order_manager import close_profitable_position
from core.risk_manager import (
    calculate_account_pnl_percent,
    calculate_position_pnl_percent,
    calculate_risk_margin,
    count_open_positions,
)
from indicators.smart_buy import find_frequent_open_close_level, is_near_frequent_level
from config.settings import BotSettings


class SafetyFoundationTests(unittest.TestCase):
    def test_risk_margin_uses_stop_distance(self):
        margin = calculate_risk_margin(1000, 100, 99, 10, 1)
        self.assertEqual(margin, 100.0)

    def test_position_limit_counts_long_and_short_together(self):
        positions = [
            {"positionAmt": "1"},
            {"positionAmt": "-2"},
            {"positionAmt": "0"},
        ]
        self.assertEqual(count_open_positions(positions), 2)

    def test_fill_price_prefers_average_fill(self):
        self.assertEqual(_get_average_fill_price({"avgPrice": "101.25"}, 100), 101.25)

    def test_fill_price_calculates_from_fills(self):
        order = {"fills": [{"price": "100", "qty": "2"}, {"price": "102", "qty": "1"}]}
        self.assertAlmostEqual(_get_average_fill_price(order, 90), 100.6666666667)

    def test_smart_buy_finds_frequent_open_close_level(self):
        daily_data = []
        for index in range(20):
            daily_data.append({
                "open": 100 if index < 8 else 110 + index,
                "close": 100.2 if index < 8 else 115 + index,
            })

        import pandas as pd
        level = find_frequent_open_close_level(pd.DataFrame(daily_data))
        self.assertEqual(level["touches"], 16)
        self.assertTrue(is_near_frequent_level(100.3, level))

    def test_dynamic_settings_require_valid_values(self):
        settings = BotSettings()
        with self.assertRaises(ValueError):
            settings.update_rsi(14, 80, 70)
        with self.assertRaises(ValueError):
            settings.update_scanner_mode("unknown")
        with self.assertRaises(ValueError):
            settings.update_analysis_lookback_days(19)

    def test_running_pnl_percent_uses_initial_margin(self):
        position = {
            "positionAmt": "2",
            "entryPrice": "100",
            "leverage": "10",
            "unrealizedProfit": "5",
        }
        self.assertEqual(calculate_position_pnl_percent(position), 25.0)
        self.assertEqual(calculate_account_pnl_percent(5, 1000), 0.5)


if __name__ == "__main__":
    unittest.main()