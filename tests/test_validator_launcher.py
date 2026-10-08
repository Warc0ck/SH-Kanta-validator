"""Suoran käynnistyksen ja Streamlit-ajon välisen siirtymän testit."""

from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import Mock, patch


APP = Path(__file__).resolve().parents[1] / "SHKanta-www-validator.py"


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Lataa käynnistimen nimiavaruuden testejä varten käynnistämättä sovellusta."""
        cls.namespace = runpy.run_path(str(APP), run_name="validator_launcher_test")

    def test_direct_launch_delegates_setup_and_preserves_arguments(self):
        """Ohjaa suoran käynnistyksen valmistelumoduuliin ja säilyttää argumentit."""
        launch = self.namespace["_launch"]
        arguments = [str(APP), "--server.headless=true", "--server.port=8767"]
        bootstrap = Mock()
        with (
            patch("streamlit.runtime.exists", return_value=False),
            patch.dict(launch.__globals__, {"launch_from_terminal": bootstrap}),
            patch.object(sys, "argv", arguments),
        ):
            launch()
        bootstrap.assert_called_once_with(
            APP, arguments[1:],
        )

    def test_streamlit_runtime_runs_app_without_relaunching(self):
        """Varmistaa sovelluksen käynnistymisen jo olemassa olevassa Streamlit-ajossa."""
        launch = self.namespace["_launch"]
        with (
            patch("streamlit.runtime.exists", return_value=True),
            patch.dict(launch.__globals__, {"main": Mock(), "launch_from_terminal": Mock()}),
            patch("os.execv") as execute,
        ):
            launch()
            launch.__globals__["main"].assert_called_once_with()
            launch.__globals__["launch_from_terminal"].assert_not_called()
        execute.assert_not_called()

    def test_terminal_setup_runs_before_unavailable_third_party_imports(self):
        """Avaa valmistelun, vaikka requestsia tai Streamlitiä ei voi vielä tuoda."""
        arguments = [str(APP), "--server.port=8767"]
        with (
            patch.dict(sys.modules, {"streamlit": None, "requests": None}),
            patch("validator_bootstrap.launch_from_terminal", side_effect=SystemExit(0)) as bootstrap,
            patch.object(sys, "argv", arguments),
            self.assertRaises(SystemExit) as stopped,
        ):
            runpy.run_path(str(APP), run_name="__main__")
        self.assertEqual(stopped.exception.code, 0)
        bootstrap.assert_called_once_with(APP, arguments[1:])

    def test_importing_app_does_not_start_setup(self):
        """Säilyttää tavallisen moduulilatauksen ilman terminaalivalikkoa tai käynnistystä."""
        with patch("validator_bootstrap.launch_from_terminal") as bootstrap:
            runpy.run_path(str(APP), run_name="validator_import_test")
        bootstrap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
