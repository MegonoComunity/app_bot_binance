import unittest
from unittest.mock import patch, MagicMock
import asyncio
from core.auto_updater import run_git_command, check_for_git_updates, smart_git_pull_and_heal


class TestAutoUpdater(unittest.TestCase):
    def test_run_git_command(self):
        code, stdout, stderr = run_git_command(["version"])
        self.assertEqual(code, 0)
        self.assertIn("git version", stdout.lower())

    @patch("core.auto_updater.run_git_command")
    def test_check_for_git_updates_no_update(self, mock_git):
        # Return hash yang sama
        mock_git.side_effect = [
            (0, "", ""),           # fetch
            (0, "abcdef1234", ""),  # local HEAD
            (0, "abcdef1234", ""),  # remote HEAD
        ]
        res = asyncio.run(check_for_git_updates("main"))
        self.assertFalse(res["has_update"])
        self.assertEqual(res["behind_count"], 0)

    @patch("core.auto_updater.run_git_command")
    def test_check_for_git_updates_with_update(self, mock_git):
        # Return hash yang berbeda
        mock_git.side_effect = [
            (0, "", ""),                  # fetch
            (0, "localhash123", ""),       # local HEAD
            (0, "remotehash456", ""),      # remote HEAD
            (0, "2", ""),                 # behind count
            (0, "abc Fix bug\ndef Add ML", "") # commit log
        ]
        res = asyncio.run(check_for_git_updates("main"))
        self.assertTrue(res["has_update"])
        self.assertEqual(res["behind_count"], 2)
        self.assertEqual(len(res["commits"]), 2)

    @patch("subprocess.run")
    @patch("core.auto_updater.run_git_command")
    def test_smart_git_pull_and_heal(self, mock_git, mock_subprocess):
        mock_git.side_effect = [
            (0, "", ""),                                    # git status --porcelain
            (0, "Updating abc..def\nFast-forward", ""),     # git pull
        ]
        mock_sub_res = MagicMock()
        mock_sub_res.returncode = 0
        mock_sub_res.stdout = "Ran 50 tests in 1s\nOK"
        mock_sub_res.stderr = ""
        mock_subprocess.return_value = mock_sub_res

        res = asyncio.run(smart_git_pull_and_heal("main"))
        self.assertTrue(res["success"])
        self.assertIn("Fast-forward", res["pull_output"])


if __name__ == "__main__":
    unittest.main()
