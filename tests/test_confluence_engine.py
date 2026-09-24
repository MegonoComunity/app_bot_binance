import unittest
import pandas as pd
import numpy as np
from core.confluence_engine import calculate_confluence_score


class TestConfluenceEngine(unittest.TestCase):
    def setUp(self):
        times = pd.date_range("2026-01-01", periods=100, freq="5min")
        self.df_bullish = pd.DataFrame({
            "open": np.linspace(100, 110, 100),
            "high": np.linspace(101, 111, 100),
            "low": np.linspace(99, 109, 100),
            "close": np.linspace(100.5, 110.5, 100),
            "volume": [1000.0] * 99 + [3000.0],
            "RSI": [30.0] * 99 + [32.0],
            "bb_lower": [108.0] * 100,
            "bb_upper": [115.0] * 100,
            "bb_middle": [111.5] * 100,
        }, index=times)

        htf_times = pd.date_range("2026-01-01", periods=100, freq="1h")
        self.df_htf_uptrend = pd.DataFrame({
            "open": np.linspace(90, 110, 100),
            "high": np.linspace(92, 112, 100),
            "low": np.linspace(89, 109, 100),
            "close": np.linspace(91, 111, 100),
            "volume": [5000.0] * 100,
        }, index=htf_times)

    def test_high_confluence_setup_approval(self):
        breakout_info = {"ready": True, "score": 85.0, "volume_spike": 3.0}

        res = calculate_confluence_score(
            df_5m=self.df_bullish,
            df_htf=self.df_htf_uptrend,
            df_daily=None,
            side="LONG",
            pattern_name="Hammer",
            pattern_type="LONG",
            near_support=True,
            near_resistance=False,
            near_lower_bb=True,
            near_upper_bb=False,
            is_oversold=True,
            is_overbought=False,
            vol_ratio=3.0,
            breakout_info=breakout_info,
            htf_trend="UPTREND",
            min_score_threshold=80.0,
        )

        self.assertGreaterEqual(res["score"], 80.0)
        self.assertTrue(res["is_approved"])
        self.assertIn("htf_alignment", res["breakdown"])
        self.assertIn("sr_and_pattern", res["breakdown"])

    def test_low_confluence_rejection(self):
        breakout_info = {"ready": False, "score": 20.0, "volume_spike": 0.8}

        res = calculate_confluence_score(
            df_5m=self.df_bullish,
            df_htf=self.df_htf_uptrend,
            df_daily=None,
            side="LONG",
            pattern_name=None,
            pattern_type=None,
            near_support=False,
            near_resistance=False,
            near_lower_bb=False,
            near_upper_bb=False,
            is_oversold=False,
            is_overbought=False,
            vol_ratio=0.8,
            breakout_info=breakout_info,
            htf_trend="DOWNTREND",  # Counter-trend
            min_score_threshold=80.0,
        )

        self.assertLess(res["score"], 80.0)
        self.assertFalse(res["is_approved"])


if __name__ == "__main__":
    unittest.main()
