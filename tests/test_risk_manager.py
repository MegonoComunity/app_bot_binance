import unittest
from core.risk_manager import (
    calculate_risk_margin,
    count_open_positions,
    calculate_position_pnl_percent,
    evaluate_time_based_exit,
)


class TestRiskManager(unittest.TestCase):
    def test_evaluate_time_based_exit_cut_loss(self):
        # Case 1: Hold 4.5 jam, loss -6% -> Harus close (Cut Loss)
        should_close, exit_type, reason = evaluate_time_based_exit(hold_duration_hours=4.5, roi_percent=-6.0)
        self.assertTrue(should_close)
        self.assertEqual(exit_type, "SAFETY_CUT_LOSS_4H")

        # Case 2: Hold 3.5 jam, loss -6% -> Belum 4 jam -> Jangan close
        should_close, exit_type, reason = evaluate_time_based_exit(hold_duration_hours=3.5, roi_percent=-6.0)
        self.assertFalse(should_close)

        # Case 3: Hold 5.0 jam, loss -2% -> Belum tembus -5% -> Jangan close
        should_close, exit_type, reason = evaluate_time_based_exit(hold_duration_hours=5.0, roi_percent=-2.0)
        self.assertFalse(should_close)

    def test_evaluate_time_based_exit_take_profit(self):
        # Case 4: Hold 8.5 jam, profit +22% -> Harus close (Take Profit)
        should_close, exit_type, reason = evaluate_time_based_exit(hold_duration_hours=8.5, roi_percent=22.0)
        self.assertTrue(should_close)
        self.assertEqual(exit_type, "SAFETY_PROFIT_LOCK_8H")

        # Case 5: Hold 6.0 jam, profit +25% -> Belum 8 jam -> Jangan close via time exit
        should_close, exit_type, reason = evaluate_time_based_exit(hold_duration_hours=6.0, roi_percent=25.0)
        self.assertFalse(should_close)

        # Case 6: Hold 9.0 jam, profit +10% -> Belum tembus +20% -> Jangan close
        should_close, exit_type, reason = evaluate_time_based_exit(hold_duration_hours=9.0, roi_percent=10.0)
        self.assertFalse(should_close)

    def test_calculate_position_pnl_percent_with_margin(self):
        # Bitunix returns direct margin
        position = {
            "position_amt": "-7825",
            "entry_price": "0.0043966",
            "leverage": "75",
            "unrealized_pnl": "0.38",
            "margin": "0.4586",
        }
        pnl_pct = calculate_position_pnl_percent(position)
        self.assertAlmostEqual(pnl_pct, 82.86, places=1)


if __name__ == "__main__":
    unittest.main()
