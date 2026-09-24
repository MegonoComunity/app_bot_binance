import unittest
from core.risk_manager import evaluate_auto_breakeven


class TestAutoBreakEven(unittest.TestCase):
    def test_long_auto_breakeven_triggered(self):
        # Case 1: LONG entry 100, current ROI +10.0%, trigger at 8.0%
        res = evaluate_auto_breakeven(
            current_roi_percent=10.0,
            entry_price=100.0,
            side="LONG",
            be_activation_roi=8.0,
            fee_buffer_percent=0.1,
        )
        self.assertTrue(res["should_move_to_be"])
        # Entry 100 + 0.1% buffer = 100.1
        self.assertAlmostEqual(res["new_sl_price"], 100.1, places=3)
        self.assertIn("Break-Even", res["reason"])

    def test_long_auto_breakeven_not_triggered_yet(self):
        # Case 2: LONG entry 100, current ROI 6.5% (belum capai 8%)
        res = evaluate_auto_breakeven(
            current_roi_percent=6.5,
            entry_price=100.0,
            side="LONG",
            be_activation_roi=8.0,
            fee_buffer_percent=0.1,
        )
        self.assertFalse(res["should_move_to_be"])
        self.assertIsNone(res["new_sl_price"])

    def test_short_auto_breakeven_triggered(self):
        # Case 3: SHORT entry 100, current ROI 9.0%, trigger at 8.0%
        res = evaluate_auto_breakeven(
            current_roi_percent=9.0,
            entry_price=100.0,
            side="SHORT",
            be_activation_roi=8.0,
            fee_buffer_percent=0.1,
        )
        self.assertTrue(res["should_move_to_be"])
        # Entry 100 - 0.1% buffer = 99.9
        self.assertAlmostEqual(res["new_sl_price"], 99.9, places=3)


if __name__ == "__main__":
    unittest.main()
