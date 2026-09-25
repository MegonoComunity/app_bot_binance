"""
tests/test_smc_snr_channel.py

Unit tests for LnSNRCH.v2 SMC structure, Quasimodo (QML), FVG, and Premium/Discount zones.
"""
import unittest
import pandas as pd
import numpy as np

from indicators.smc_snr_channel import (
    detect_quasimodo_pattern,
    calculate_fair_value_gaps,
    calculate_premium_discount_zones,
    calculate_smc_structure_v2,
)
from core.confluence_engine import calculate_confluence_score


class TestSmcSnrChannel(unittest.TestCase):

    def _generate_mock_df(self, count=100, trend="UP", base_price=100.0):
        np.random.seed(42)
        prices = [base_price]
        for i in range(1, count):
            step = 0.5 if trend == "UP" else (-0.5 if trend == "DOWN" else 0.0)
            noise = np.random.normal(0, 0.2)
            prices.append(max(1.0, prices[-1] + step + noise))

        data = []
        for i, p in enumerate(prices):
            open_p = p - 0.1
            close_p = p + 0.1
            high_p = max(open_p, close_p) + 0.3
            low_p = min(open_p, close_p) - 0.3
            vol = 1000.0
            data.append({
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": vol,
            })
        return pd.DataFrame(data)

    def test_insufficient_data(self):
        df_short = pd.DataFrame([{"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}])
        res = calculate_smc_structure_v2(df_short)
        self.assertFalse(res["is_valid"])

    def test_quasimodo_detection_structure(self):
        df = self._generate_mock_df(count=100, trend="UP", base_price=100.0)
        qml_res = detect_quasimodo_pattern(df, zigzag_len=10)
        self.assertIn("is_detected", qml_res)
        self.assertIn("pattern_type", qml_res)

    def test_fair_value_gaps(self):
        # Create an intentional FVG (huge jump in candle 2 leaving gap above candle 0)
        data = [
            {"open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 100},
            {"open": 102, "high": 106, "low": 101.5, "close": 105.5, "volume": 500},
            {"open": 106, "high": 108, "low": 105.0, "close": 107.5, "volume": 300},  # Low is 105.0 > High 0 (101.0) -> Bullish FVG
        ]
        df_fvg = pd.DataFrame(data)
        fvg_res = calculate_fair_value_gaps(df_fvg)
        self.assertTrue(fvg_res["has_bullish_fvg"])

    def test_premium_discount_zones(self):
        df = self._generate_mock_df(count=50, trend="UP", base_price=100.0)
        zone_res = calculate_premium_discount_zones(df, lookback=50)
        self.assertIn("current_zone", zone_res)
        self.assertIn(zone_res["current_zone"], ["PREMIUM", "DISCOUNT", "EQUILIBRIUM"])

    def test_smc_structure_v2_and_confluence(self):
        df = self._generate_mock_df(count=120, trend="UP", base_price=100.0)
        smc_res = calculate_smc_structure_v2(df)

        self.assertTrue(smc_res["is_valid"])
        self.assertIn("quasimodo", smc_res)
        self.assertIn("zones", smc_res)

        conf_res = calculate_confluence_score(
            df_5m=df,
            side="LONG",
            pattern_name="HAMMER",
            pattern_type="LONG",
            near_support=True,
            htf_trend="UPTREND",
            smc_v2_info=smc_res,
        )
        self.assertIn("smc_structure_v2", conf_res["breakdown"])
        self.assertTrue(conf_res["is_approved"])


if __name__ == "__main__":
    unittest.main()
