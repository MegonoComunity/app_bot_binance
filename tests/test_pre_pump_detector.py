import unittest
import pandas as pd
import numpy as np
from indicators.pre_pump_detector import detect_explosive_pre_pump

class TestPrePumpDetector(unittest.TestCase):
    def test_insufficient_data(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        result = detect_explosive_pre_pump(df, symbol="BTCUSDT")
        self.assertFalse(result["is_alert"])
        self.assertEqual(result["tier"], "NONE")

    def test_explosive_pump_ignition(self):
        # Buat data sintetis: 30 candle sideway rapat (BBW < 2%), diikuti 1 candle ledakan volume 6x dan breakout
        closes = [100.0 + (i % 2) * 0.1 for i in range(35)]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        opens = [c - 0.05 for c in closes]
        volumes = [1000.0 for _ in range(35)]

        # Candle ke-36: Detonasi breakout + volume spike 6x
        closes.append(108.0) # Breakout +8%
        highs.append(108.5)
        lows.append(99.9)
        opens.append(100.1) # Marubozu green
        volumes.append(7000.0) # 7x volume

        df = pd.DataFrame({
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })

        result = detect_explosive_pre_pump(df, symbol="SOLUSDT", volume_multiplier_trigger=3.0)
        self.assertTrue(result["is_alert"])
        self.assertEqual(result["tier"], "PUMP_IGNITION_ATH")
        self.assertGreaterEqual(result["score"], 70.0)
        self.assertGreaterEqual(result["rvol"], 4.0)
        self.assertIn("roi_20x_tp3", result)
        self.assertEqual(result["roi_20x_tp3"], "+1,000.0%")

if __name__ == "__main__":
    unittest.main()
