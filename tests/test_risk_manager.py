import unittest
from core.risk_manager import (
    calculate_risk_margin,
    count_open_positions,
    calculate_position_pnl_percent,
    evaluate_time_based_exit,
)


class TestRiskManager(unittest.TestCase):
    def test_evaluate_time_based_exit_cut_loss(self):
        # Case 1: Hold 2.5 jam, loss -6% -> Harus close (Cut Loss)
        should_close, reason = evaluate_time_based_exit(hold_duration_hours=2.5, roi_percent=-6.0)
        self.assertTrue(should_close)
        self.assertIn("CUT_LOSS_TIME", reason)

        # Case 2: Hold 1.5 jam, loss -6% -> Belum 2 jam -> Jangan close
        should_close, reason = evaluate_time_based_exit(hold_duration_hours=1.5, roi_percent=-6.0)
        self.assertFalse(should_close)

        # Case 3: Hold 3.0 jam, loss -2% -> Belum tembus -5% -> Jangan close
        should_close, reason = evaluate_time_based_exit(hold_duration_hours=3.0, roi_percent=-2.0)
        self.assertFalse(should_close)

    def test_evaluate_time_based_exit_take_profit(self):
        # Case 4: Hold 4.5 jam, profit +22% -> Harus close (Take Profit)
        should_close, reason = evaluate_time_based_exit(hold_duration_hours=4.5, roi_percent=22.0)
        self.assertTrue(should_close)
        self.assertIn("TAKE_PROFIT_TIME", reason)

        # Case 5: Hold 3.0 jam, profit +25% -> Belum 4 jam -> Jangan close via time exit
        should_close, reason = evaluate_time_based_exit(hold_duration_hours=3.0, roi_percent=25.0)
        self.assertFalse(should_close)

        # Case 6: Hold 5.0 jam, profit +10% -> Belum tembus +20% -> Jangan close
        should_close, reason = evaluate_time_based_exit(hold_duration_hours=5.0, roi_percent=10.0)
        self.assertFalse(should_close)


if __name__ == "__main__":
    unittest.main()
