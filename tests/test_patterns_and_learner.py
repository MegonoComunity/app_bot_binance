import unittest
import pandas as pd
from indicators.patterns import (
    check_hammer,
    check_bullish_engulfing,
    check_morning_star,
    check_piercing_line,
    check_tweezer_bottom,
    check_small_bodies_followed_by_green,
    detect_candlestick_patterns,
    is_bull_trap
)
from core.learner import normalize_pattern_category, is_pattern_reliable, record_trade_result

class TestPatternsAndLearner(unittest.TestCase):
    def test_hammer_detection(self):
        # Open: 100, High: 101, Low: 90, Close: 100.5
        # Total range: 11. Body: 0.5 (4.5% of range). Lower shadow: 10 (90.9% of range). Upper: 0.5
        self.assertTrue(check_hammer(100.0, 101.0, 90.0, 100.5))
        # Not hammer (large body)
        self.assertFalse(check_hammer(100.0, 101.0, 90.0, 93.0))

    def test_bullish_engulfing(self):
        # Prev: Open 100, Close 95. Curr: Open 94, Close 101.
        self.assertTrue(check_bullish_engulfing(100.0, 95.0, 94.0, 101.0, prev_vol=100, curr_vol=150))
        # Bearish current candle -> False
        self.assertFalse(check_bullish_engulfing(100.0, 95.0, 101.0, 94.0, prev_vol=100, curr_vol=150))

    def test_piercing_line(self):
        # C1: Open 100, Close 90 (Midpoint 95). C2: Open 89, Close 96.
        self.assertTrue(check_piercing_line(100.0, 90.0, 89.0, 96.0))

    def test_tweezer_bottom(self):
        self.assertTrue(check_tweezer_bottom(100.0, 100.05, 101.0, 103.0, 104.0, 100.5))

    def test_pattern_normalization(self):
        self.assertEqual(
            normalize_pattern_category("Pola Tier-A Bullish Hammer (Vol: 1.64x, HTF: UPTREND)"),
            "Pola Tier-A: Bullish Hammer"
        )
        self.assertEqual(
            normalize_pattern_category("Dormant breakout score 66.7 (vol 8.68x, HTF: UPTREND)"),
            "Setup Breakout: Dormant Squeeze"
        )
        self.assertEqual(
            normalize_pattern_category("Smart Buy level 0.03458778 (9x open/close 20D, HTF: UPTREND)"),
            "Setup Support: Smart Buy Level"
        )
        self.assertEqual(
            normalize_pattern_category("RSI Oversold (28.74) di Lower BB (HTF: UPTREND)"),
            "Setup Reversal: RSI Oversold Lower BB"
        )

if __name__ == '__main__':
    unittest.main()
