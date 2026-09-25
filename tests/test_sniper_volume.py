"""
tests/test_sniper_volume.py

Unit tests for SMC Sniper Elite V17 - RADEN PRECISION Volume Profile & Accumulation indicator.
"""
import unittest
import pandas as pd
import numpy as np

from indicators.sniper_volume import calculate_smc_sniper_volume
from core.confluence_engine import calculate_confluence_score


class TestSniperVolume(unittest.TestCase):

    def _generate_mock_df(self, count=120, trend="UP", base_price=100.0):
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
            # In an accumulation/bullish scenario, green candles have higher volume
            is_green = close_p >= open_p
            vol = 1000.0 + (500.0 if is_green else 100.0)
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
        res = calculate_smc_sniper_volume(df_short)
        self.assertFalse(res["is_valid"])
        self.assertEqual(res["market_state"], "MONITORING")

    def test_sniper_volume_calculation(self):
        df = self._generate_mock_df(count=120, trend="UP", base_price=100.0)
        res = calculate_smc_sniper_volume(df, lookback=100)

        self.assertTrue(res["is_valid"])
        self.assertIn("poc_price", res)
        self.assertIn("buy_power_pct", res)
        self.assertIn("sell_power_pct", res)
        self.assertIn("market_state", res)
        self.assertIn("profile_nodes", res)
        self.assertEqual(len(res["profile_nodes"]), 10)
        self.assertGreater(res["buy_power_pct"], 0)

    def test_accumulation_ready_detection(self):
        # Create a df where prices dropped (below EMA50), but buyer volume is very heavy at the bottom
        df_down = self._generate_mock_df(count=80, trend="DOWN", base_price=200.0)
        
        # Add 30 bars of heavy bottom accumulation (huge buyer volume on green bars)
        acc_data = []
        curr = df_down["close"].iloc[-1]
        for i in range(30):
            o = curr
            c = curr + 0.05
            h = c + 0.1
            l = o - 0.05
            v = 50000.0  # Massive volume
            acc_data.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
            curr = c

        df_acc = pd.concat([df_down, pd.DataFrame(acc_data)], ignore_index=True)
        res = calculate_smc_sniper_volume(df_acc, lookback=60)

        self.assertTrue(res["is_valid"])
        self.assertGreater(res["buy_power_pct"], 50.0)

    def test_confluence_integration_with_sniper(self):
        df = self._generate_mock_df(count=120, trend="UP", base_price=100.0)
        sniper_res = calculate_smc_sniper_volume(df)

        conf_res = calculate_confluence_score(
            df_5m=df,
            side="LONG",
            pattern_name="HAMMER",
            pattern_type="LONG",
            near_support=True,
            htf_trend="UPTREND",
            sniper_info=sniper_res,
        )

        self.assertIn("smc_sniper_volume", conf_res["breakdown"])
        self.assertGreaterEqual(conf_res["score"], 60.0)
        self.assertTrue(conf_res["is_approved"])


if __name__ == "__main__":
    unittest.main()
