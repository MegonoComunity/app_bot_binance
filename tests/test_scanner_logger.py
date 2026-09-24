import unittest
from core.scanner_logger import (
    add_scanner_log,
    update_scanner_progress,
    get_scanner_snapshot,
    clear_scanner_logs,
)
from telegram.bot_handler import bot_state


class TestScannerLogger(unittest.TestCase):
    def setUp(self):
        clear_scanner_logs()

    def test_add_and_retrieve_logs(self):
        entry = add_scanner_log("PUMP", "CRVUSDT", "Pre-Pump Radar Terdeteksi", score=85.0)
        self.assertEqual(entry["level"], "PUMP")
        self.assertEqual(entry["symbol"], "CRVUSDT")
        self.assertEqual(entry["score"], 85.0)

        snapshot = get_scanner_snapshot()
        self.assertGreaterEqual(snapshot["total_logs"], 1)
        self.assertEqual(snapshot["logs"][0]["symbol"], "CRVUSDT")

    def test_update_scanner_progress(self):
        update_scanner_progress(
            current_symbol="SOLUSDT",
            scanned_count=20,
            total_coins=80,
            current_batch=2,
            total_batches=8,
            cycle_index=3,
            is_scanning=True,
            status_message="Scanning SOLUSDT",
        )
        status = bot_state["scanner_status"]
        self.assertEqual(status["current_symbol"], "SOLUSDT")
        self.assertEqual(status["scanned_count"], 20)
        self.assertEqual(status["total_coins"], 80)
        self.assertEqual(status["progress_percent"], 25.0)
        self.assertEqual(status["current_cycle"], 3)


if __name__ == "__main__":
    unittest.main()
