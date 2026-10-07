"""Verify direct launch handoff and Streamlit runtime dispatch."""

from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import Mock, patch


APP = Path(__file__).resolve().parents[1] / "SHKanta-www-validator.py"


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.namespace = runpy.run_path(str(APP), run_name="validator_launcher_test")

    def test_direct_launch_replaces_process_and_preserves_arguments(self):
        launch = self.namespace["_launch"]
        arguments = [str(APP), "--server.headless=true", "--server.port=8767"]
        with (
            patch("streamlit.runtime.exists", return_value=False),
            patch("os.execv") as execute,
            patch.object(sys, "argv", arguments),
        ):
            launch()
        execute.assert_called_once_with(
            sys.executable,
            [sys.executable, "-m", "streamlit", "run", str(APP), *arguments[1:]],
        )

    def test_streamlit_runtime_runs_app_without_relaunching(self):
        launch = self.namespace["_launch"]
        with (
            patch("streamlit.runtime.exists", return_value=True),
            patch.dict(launch.__globals__, {"main": Mock()}),
            patch("os.execv") as execute,
        ):
            launch()
            launch.__globals__["main"].assert_called_once_with()
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
