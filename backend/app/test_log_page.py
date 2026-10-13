"""Unit tests for log paging (RealRunner.read_log_page / _page_bounds)."""

import os
import tempfile
import unittest
from pathlib import Path

from app.test_support import use_temp_data_dir

use_temp_data_dir()

from app.jobs.real_runner import RealRunner  # noqa: E402


class LogPageTest(unittest.TestCase):
    def test_bounds(self):
        pb = RealRunner._page_bounds
        self.assertEqual(pb(0, 1000, 0), (1, 1, 1, 0))
        self.assertEqual(pb(2500, 1000, 0), (3, 3, 2001, 2500))     # 0 = last page
        self.assertEqual(pb(2500, 1000, 1), (1, 3, 1, 1000))
        self.assertEqual(pb(2000, 1000, 0), (2, 2, 1001, 2000))
        self.assertEqual(pb(2500, 1000, 9), (3, 3, 2001, 2500))     # out of range -> last

    def test_local_read(self):
        runner = RealRunner.__new__(RealRunner)
        runner._server = lambda name: {"host": "127.0.0.1"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.log"
            path.write_text("\n".join(f"line{i}" for i in range(1, 2501)), encoding="utf-8")
            text, cur, pages, total = runner.read_log_page("x", path, 1000, 2)
            self.assertEqual((cur, pages, total), (2, 3, 2500))
            self.assertEqual(text.splitlines()[0], "line1001")
            self.assertEqual(text.splitlines()[-1], "line2000")
            text, cur, _, _ = runner.read_log_page("x", path, 1000, 0)
            self.assertEqual((cur, text.splitlines()[-1]), (3, "line2500"))
            self.assertIsNone(runner.read_log_page("x", Path(tmp) / "missing.log", 1000, 0))


if __name__ == "__main__":
    unittest.main()
