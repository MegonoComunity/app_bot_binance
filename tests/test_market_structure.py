import unittest
import pandas as pd
import numpy as np
from indicators.market_structure import (
    detect_swing_points,
    analyze_market_structure,
    calculate_anchored_vwap,
    calculate_dynamic_swing_avwap,
    detect_ema21_pullback,
)


class TestMarketStructure(unittest.TestCase):

    def setUp(self):
        # Buat data sintetis bergelombang dengan swing high & low (HL -> HH -> HL -> HH)
        np.random.seed(42)
        n = 60
        trend = np.linspace(100, 130, n)
        cycles = 10 * np.sin(np.linspace(0, 6 * np.pi, n))
        base_prices = trend + cycles
        highs = base_prices + np.random.uniform(0.5, 1.5, n)
        lows = base_prices - np.random.uniform(0.5, 1.5, n)
        closes = base_prices + np.random.uniform(-0.2, 0.2, n)
        volumes = np.random.uniform(1000, 5000, n)

        self.df_uptrend = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="5min"),
            "open": base_prices,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })

    def test_detect_swing_points(self):
        swings = detect_swing_points(self.df_uptrend, window=2)
        self.assertIsInstance(swings, list)
        self.assertTrue(len(swings) > 0)
        self.assertIn("type", swings[0])
        self.assertIn("price", swings[0])

    def test_analyze_market_structure(self):
        res = analyze_market_structure(self.df_uptrend, window=2)
        self.assertIn("regime", res)
        self.assertIn("structure_sequence", res)
        self.assertIn("is_compression", res)

    def test_calculate_dynamic_swing_avwap(self):
        res = calculate_dynamic_swing_avwap(self.df_uptrend, window=2)
        self.assertIn("current_price", res)
        self.assertIn("position_to_avwap", res)

    def test_detect_ema21_pullback(self):
        res = detect_ema21_pullback(self.df_uptrend)
        self.assertIn("is_pullback", res)
        self.assertIn("ema21", res)


if __name__ == "__main__":
    unittest.main()
