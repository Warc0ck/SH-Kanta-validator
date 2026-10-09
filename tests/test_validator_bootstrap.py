"""Käynnistysvalikon testit ilman pakettiasennuksia tai verkkoyhteyksiä."""

from contextlib import ExitStack, redirect_stdout
import importlib.metadata
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import ANY, Mock, call, patch

import validator_bootstrap as bootstrap


class BootstrapTests(unittest.TestCase):
    """Tarkistaa ympäristövalinnan, rajatut asennukset ja virheistä palautumisen."""

    def setUp(self):
        """Luo väliaikaisen projektin ja estää jokaisen todellisen aliprosessin ja käynnistyksen."""
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.project = Path(folder).resolve() / "projekti välilyönnein"
        self.project.mkdir()
        self.app = self.project / bootstrap.APP_FILENAME
        self.app.write_text("# Testisovellus\n", encoding="utf-8")
        self.requirements_path = self.project / "requirements.txt"
        self.requirements_path.write_text("requests==2.32.5\nstreamlit==1.64.0\n", encoding="utf-8")
        self.requirements = {"requests": "2.32.5", "streamlit": "1.64.0"}
        self.venv = self.project / ".venv"
        self.python = bootstrap._venv_python(self.venv)
        self.output = io.StringIO()
        self.stack.enter_context(redirect_stdout(self.output))
        self.process = self.stack.enter_context(patch.object(bootstrap.subprocess, "run"))
        self.execute = self.stack.enter_context(patch.object(bootstrap.os, "execv"))
        self.stack.enter_context(patch.object(bootstrap.sys, "stdin", Mock(isatty=Mock(return_value=True))))
        self.ready = bootstrap.EnvironmentCheck(True, (), True, True, True)
        self.missing = bootstrap.EnvironmentCheck(False, ("Riippuvuudet puuttuvat.",))

    def create_python(self):
        """Luo testivenvin Python-polun ilman oikean ympäristön luomista."""
        self.python.parent.mkdir(parents=True)
        self.python.touch()

    def probe_result(self, *, prefix=None, version=(3, 10), problems=(), has_pip=True):
        """Muodostaa ympäristöä tarkistavan aliprosessin synteettisen vastauksen."""
        data = {"prefix": str(prefix or self.venv), "version": version, "problems": list(problems), "has_pip": has_pip}
        return subprocess.CompletedProcess([], 0, json.dumps(data), "")

    def launch(self, arguments=None, code=0):
        """Käynnistää valikon ja tarkistaa prosessin lopetuskoodin."""
        with self.assertRaises(SystemExit) as stopped:
            bootstrap.launch_from_terminal(self.app, [] if arguments is None else arguments)
        self.assertEqual(stopped.exception.code, code)

    def test_reads_exact_requirement_versions_and_rejects_conflicts(self):
        """Lukee kommentoidut tarkat versiot ja pysäyttää ristiriitaiset riippuvuudet."""
        self.requirements_path.write_text("# Kommentti\nRequests == 2.32.5 # Selite\nstreamlit==1.64.0\n", encoding="utf-8")
        self.assertEqual(bootstrap._read_requirements(self.requirements_path), self.requirements)
        for text in ("requests>=2\n", "requests==2\nrequests==3\n", "# Ei riippuvuuksia\n"):
            with self.subTest(text=text):
                self.requirements_path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    bootstrap._read_requirements(self.requirements_path)

    def test_platform_python_paths(self):
        """Valitsee Windowsin ja POSIX-järjestelmien virtuaaliympäristön Python-polut."""
        with patch.object(bootstrap.os, "name", "nt"):
            self.assertEqual(bootstrap._venv_python(self.venv), self.venv / "Scripts" / "python.exe")
        with patch.object(bootstrap.os, "name", "posix"):
            self.assertEqual(bootstrap._venv_python(self.venv), self.venv / "bin" / "python")

    def test_probe_checks_versions_and_actual_imports(self):
        """Hylkää väärän version ja epäonnistuvan tuonnin myös metatietojen löytyessä."""
        def import_package(name):
            """Simuloi pakettia, jonka metatieto löytyy mutta tuonti epäonnistuu."""
            if name == "streamlit":
                raise ImportError("Testituonti epäonnistui")

        captured = io.StringIO()
        with (
            patch.object(sys, "argv", ["probe", json.dumps(self.requirements)]),
            patch("importlib.metadata.version", side_effect=["2.0.0", "1.64.0"]),
            patch("importlib.import_module", side_effect=import_package) as imports,
            redirect_stdout(captured),
        ):
            exec(bootstrap._PROBE_SCRIPT, {})
        data = json.loads(captured.getvalue())
        self.assertIn("tarvitaan 2.32.5", data["problems"][0])
        self.assertIn("Testituonti epäonnistui", data["problems"][1])
        self.assertEqual(imports.call_args_list, [call("requests"), call("streamlit")])

    def test_probe_handles_missing_distribution(self):
        """Merkitsee puuttuvan paketin korjattavaksi ilman poikkeuksen karkaamista."""
        captured = io.StringIO()
        with (
            patch.object(sys, "argv", ["probe", json.dumps({"requests": "2.32.5"})]),
            patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError("requests")),
            patch("importlib.import_module") as imports,
            redirect_stdout(captured),
        ):
            exec(bootstrap._PROBE_SCRIPT, {})
        self.assertIn("requests", json.loads(captured.getvalue())["problems"][0])
        imports.assert_not_called()

    def test_environment_uses_exact_python_without_resolving_symlink(self):
        """Tarkistaa venvin omalla Python-polulla ja vahvistaa projektin ympäristöön kuulumisen."""
        self.create_python()
        self.process.return_value = self.probe_result()
        self.assertEqual(bootstrap._check_environment(self.python, self.requirements, self.venv), self.ready)
        args, kwargs = self.process.call_args
        self.assertEqual(args[0], [str(self.python), "-c", bootstrap._PROBE_SCRIPT, json.dumps(self.requirements)])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["check"], True)

    def test_environment_rejects_wrong_prefix_old_python_and_mismatched_packages(self):
        """Estää vanhan Pythonin, väärän ympäristön ja väärät pakettiversiot."""
        self.create_python()
        self.process.return_value = self.probe_result(prefix=self.project, version=(3, 9), problems=("requests: versio 2.0.0, tarvitaan 2.32.5",))
        status = bootstrap._check_environment(self.python, self.requirements, self.venv)
        self.assertFalse(status.ready)
        self.assertFalse(status.prefix_matches)
        self.assertFalse(status.python_supported)
        self.assertEqual(len(status.problems), 3)

    def test_environment_handles_failed_process_invalid_response_and_timeout(self):
        """Näyttää epäonnistuneen Pythonin, virheellisen vastauksen ja aikakatkaisun virheinä."""
        self.create_python()
        cases = (
            subprocess.CalledProcessError(1, [str(self.python)], stderr="Python ei toimi"),
            subprocess.CalledProcessError(1, [str(self.python)]),
            subprocess.CompletedProcess([], 0, "ei JSONia", ""),
            subprocess.TimeoutExpired([str(self.python)], 30),
            OSError("Python ei käynnisty"),
        )
        for result in cases:
            with self.subTest(result=result):
                self.process.side_effect = result if isinstance(result, Exception) else None
                self.process.return_value = result
                status = bootstrap._check_environment(self.python, self.requirements, self.venv)
                self.assertFalse(status.ready)
                self.assertTrue(status.problems)

    def test_ready_project_environment_starts_without_menu_even_without_tty(self):
        """Käynnistää valmiin projektivennin suoraan ja säilyttää välilyönnit ja argumentit."""
        arguments = ["--server.port", "8767", "--server.headless=true"]
        with (
            patch.object(bootstrap, "_check_environment", return_value=self.ready),
            patch.object(bootstrap.sys.stdin, "isatty", return_value=False),
            patch("builtins.input") as prompt,
        ):
            self.launch(arguments)
        prompt.assert_not_called()
        self.process.assert_not_called()
        self.execute.assert_called_once_with(str(self.python), [str(self.python), "-m", "streamlit", "run", str(self.app), *arguments])

    def test_valid_current_environment_can_be_selected_without_installation(self):
        """Sallii valmiin nykyisen ympäristön käytön vain käyttäjän valinnalla ilman pipiä."""
        with (
            patch.object(bootstrap, "_check_environment", side_effect=[self.missing, self.ready]),
            patch("builtins.input", return_value="2"),
            patch.object(bootstrap, "_prepare_venv") as prepare,
        ):
            self.launch(["--server.port=8767"])
        prepare.assert_not_called()
        self.process.assert_not_called()
        self.execute.assert_called_once_with(sys.executable, [sys.executable, "-m", "streamlit", "run", str(self.app), "--server.port=8767"])

    def test_invalid_current_environment_is_not_selectable(self):
        """Piilottaa nykyisen ympäristön vaihtoehdon ja hylkää sen numeron vaatimusten puuttuessa."""
        with (
            patch.object(bootstrap, "_check_environment", return_value=self.missing),
            patch("builtins.input", side_effect=["2", "0"]),
        ):
            self.launch()
        self.assertNotIn("2. Käynnistä", self.output.getvalue())
        self.assertIn("Valitse jokin", self.output.getvalue())
        self.execute.assert_not_called()
        self.process.assert_not_called()

    def test_cancel_and_eof_do_not_install_anything(self):
        """Lopettaa valikosta tai syötteen päättyessä asentamatta riippuvuuksia."""
        for choice in ("0", EOFError()):
            with self.subTest(choice=choice):
                with (
                    patch.object(bootstrap, "_check_environment", return_value=self.missing),
                    patch("builtins.input", side_effect=choice if isinstance(choice, Exception) else None, return_value=choice),
                ):
                    self.launch()
        self.execute.assert_not_called()
        self.process.assert_not_called()

    def test_noninteractive_setup_exits_without_prompt_or_installation(self):
        """Poistuu selkeästi ilman asennusta, kun valmistelematon ympäristö ajetaan ilman terminaalia."""
        with (
            patch.object(bootstrap, "_check_environment", return_value=self.missing),
            patch.object(bootstrap.sys.stdin, "isatty", return_value=False),
            patch("builtins.input") as prompt,
        ):
            self.launch(code=1)
        self.assertIn("interaktiivisen terminaalin", self.output.getvalue())
        prompt.assert_not_called()
        self.execute.assert_not_called()
        self.process.assert_not_called()

    def test_missing_stdin_exits_without_prompt_or_installation(self):
        """Käsittelee myös graafisen Python-käynnistyksen, jossa vakiosyötettä ei ole."""
        with (
            patch.object(bootstrap, "_check_environment", return_value=self.missing),
            patch.object(bootstrap.sys, "stdin", None),
            patch("builtins.input") as prompt,
        ):
            self.launch(code=1)
        prompt.assert_not_called()
        self.process.assert_not_called()

    def test_keyboard_interrupt_exits_friendly(self):
        """Muuntaa valikon keskeytyksen ystävälliseksi ilmoitukseksi ja paluuarvoksi 130."""
        with (
            patch.object(bootstrap, "_check_environment", return_value=self.missing),
            patch("builtins.input", side_effect=KeyboardInterrupt),
        ):
            self.launch(code=130)
        self.assertIn("keskeytettiin", self.output.getvalue())
        self.process.assert_not_called()

    def test_creates_project_venv_and_installs_exact_requirements_path(self):
        """Luo venvin nykyisellä Pythonilla ja asentaa vaatimukset yksinomaan sen Pythonilla."""
        incomplete = bootstrap.EnvironmentCheck(False, ("streamlit puuttuu",), True, True, True)
        with (
            patch.object(bootstrap, "_check_environment", side_effect=[incomplete, self.ready]),
            patch.object(bootstrap, "_run_command", return_value=True) as command,
        ):
            self.assertTrue(bootstrap._prepare_venv(self.venv, self.requirements_path, self.requirements))
        self.assertEqual(command.call_args_list, [
            call([sys.executable, "-m", "venv", str(self.venv)]),
            call([str(self.python), "-m", "pip", "install", "--prefix", str(self.venv), "-r", str(self.requirements_path)], environment=ANY),
        ])

    def test_existing_project_venv_bootstraps_pip_before_installing(self):
        """Täydentää olemassa olevan venvin ja lisää puuttuvan pipin ennen riippuvuuksia."""
        self.venv.mkdir()
        incomplete = bootstrap.EnvironmentCheck(False, ("streamlit puuttuu",), True, True, False)
        with (
            patch.object(bootstrap, "_check_environment", side_effect=[incomplete, self.ready]),
            patch.object(bootstrap, "_run_command", return_value=True) as command,
        ):
            self.assertTrue(bootstrap._prepare_venv(self.venv, self.requirements_path, self.requirements))
        self.assertEqual(command.call_args_list, [
            call([str(self.python), "-m", "ensurepip", "--upgrade"]),
            call([str(self.python), "-m", "pip", "install", "--prefix", str(self.venv), "-r", str(self.requirements_path)], environment=ANY),
        ])

    def test_broken_or_foreign_venv_is_never_installed_or_deleted(self):
        """Säilyttää rikkinäisen tai muualle osoittavan venvin ja estää siinä pipin."""
        self.venv.mkdir()
        marker = self.venv / "sailyta.txt"
        marker.write_text("Säilytettävä sisältö", encoding="utf-8")
        for status in (self.missing, bootstrap.EnvironmentCheck(False, ("Väärä ympäristö",), False, True, True)):
            with self.subTest(status=status):
                with (
                    patch.object(bootstrap, "_check_environment", return_value=status),
                    patch.object(bootstrap, "_run_command") as command,
                ):
                    self.assertFalse(bootstrap._prepare_venv(self.venv, self.requirements_path, self.requirements))
                command.assert_not_called()
                self.assertEqual(marker.read_text(encoding="utf-8"), "Säilytettävä sisältö")

    def test_symlink_venv_is_never_followed_for_installation(self):
        """Estää symbolisen .venv-linkin kautta toisen ympäristön tarkistamisen ja muuttamisen."""
        with patch.object(Path, "is_symlink", return_value=True):
            self.assertFalse(bootstrap._check_environment(self.python, self.requirements, self.venv).ready)
            self.assertFalse(bootstrap._prepare_venv(self.venv, self.requirements_path, self.requirements))
        self.process.assert_not_called()

    def test_failed_creation_ensurepip_and_installation_stop_preparation(self):
        """Katkaisee valmistelun epäonnistuneessa vaiheessa käynnistämättä sovellusta."""
        with patch.object(bootstrap, "_run_command", return_value=False) as command:
            self.assertFalse(bootstrap._prepare_venv(self.venv, self.requirements_path, self.requirements))
        command.assert_called_once_with([sys.executable, "-m", "venv", str(self.venv)])
        self.venv.mkdir()
        for has_pip in (False, True):
            with self.subTest(has_pip=has_pip):
                with (
                    patch.object(bootstrap, "_check_environment", return_value=bootstrap.EnvironmentCheck(False, (), True, True, has_pip)),
                    patch.object(bootstrap, "_run_command", return_value=False) as command,
                ):
                    self.assertFalse(bootstrap._prepare_venv(self.venv, self.requirements_path, self.requirements))
                self.assertEqual(command.call_count, 1)
        self.execute.assert_not_called()

    def test_failure_returns_to_menu_for_retry_then_launches(self):
        """Palaa epäonnistuneen valmistelun jälkeen valikkoon ja sallii uuden yrityksen."""
        with (
            patch.object(bootstrap, "_check_environment", return_value=self.missing),
            patch("builtins.input", side_effect=["1", "1"]),
            patch.object(bootstrap, "_prepare_venv", side_effect=[False, True]) as prepare,
        ):
            self.launch()
        self.assertEqual(prepare.call_count, 2)
        self.assertIn("Voit yrittää uudelleen", self.output.getvalue())
        self.execute.assert_called_once()

    def test_installation_is_rechecked_before_launching(self):
        """Estää käynnistyksen, jos riippuvuudet eivät asennuksen jälkeen täytä vaatimuksia."""
        self.venv.mkdir()
        incomplete = bootstrap.EnvironmentCheck(False, ("Väärä Streamlit-versio",), True, True, True)
        with (
            patch.object(bootstrap, "_check_environment", return_value=incomplete),
            patch.object(bootstrap, "_run_command", return_value=True),
        ):
            self.assertFalse(bootstrap._prepare_venv(self.venv, self.requirements_path, self.requirements))
        self.assertIn("asennuksen jälkeen", self.output.getvalue())
        self.execute.assert_not_called()

    def test_old_creator_python_does_not_create_venv(self):
        """Näyttää Python-version vaatimuksen ennen venvin luomista vanhalla Pythonilla."""
        with patch.object(bootstrap.sys, "version_info", (3, 9, 0)):
            self.assertFalse(bootstrap._prepare_venv(self.venv, self.requirements_path, self.requirements))
        self.process.assert_not_called()

    def test_run_command_preserves_visible_subprocess_output(self):
        """Välittää asennusprosessin tulosteen terminaaliin ja käsittelee paluuarvon sekä käynnistysvirheen."""
        command = [str(self.python), "-m", "pip", "install", "-r", str(self.requirements_path)]
        self.process.return_value = subprocess.CompletedProcess(command, 0)
        self.assertTrue(bootstrap._run_command(command))
        self.process.assert_called_once_with(command, shell=False, check=True, env=None)
        self.process.side_effect = subprocess.CalledProcessError(1, command)
        self.assertFalse(bootstrap._run_command(command))
        self.assertIn("Komento epäonnistui (paluuarvo 1).", self.output.getvalue())
        self.process.side_effect = OSError("Testivirhe")
        self.assertFalse(bootstrap._run_command(command))

    def test_pip_location_overrides_are_removed_but_network_settings_remain(self):
        """Estää ympäristön ja pip-asetustiedoston kohdeohitukset säilyttäen proxy- ja index-asetukset."""
        source = {
            "PIP_TARGET": "/toinen/ymparisto", "PIP_PREFIX": "/global", "PIP_ROOT": "/root",
            "PIP_USER": "true", "PIP_PYTHON": "/toinen/python", "PIP_CONFIG_FILE": "/asetukset/pip.conf",
            "PIP_INDEX_URL": "https://packages.example.invalid/simple", "HTTPS_PROXY": "https://proxy.example.invalid",
        }
        with patch.dict(bootstrap.os.environ, source, clear=True):
            environment = bootstrap._installation_environment()
        self.assertEqual(environment, {
            "PIP_CONFIG_FILE": bootstrap.os.devnull,
            "PIP_INDEX_URL": source["PIP_INDEX_URL"], "HTTPS_PROXY": source["HTTPS_PROXY"],
        })
        command = [str(self.python), "-m", "pip", "install"]
        self.process.return_value = subprocess.CompletedProcess(command, 0)
        self.assertTrue(bootstrap._run_command(command, environment=environment))
        self.process.assert_called_once_with(command, shell=False, check=True, env=environment)

    def test_missing_requirements_and_failed_exec_exit_clearly(self):
        """Lopettaa puuttuvaan vaatimuslistaan tai epäonnistuvaan käynnistykseen selkeällä virheellä."""
        self.requirements_path.unlink()
        self.launch(code=1)
        self.process.assert_not_called()
        self.execute.side_effect = OSError("Käynnistys estetty")
        with self.assertRaises(SystemExit) as stopped:
            bootstrap._exec_streamlit(self.python, self.app, [])
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("Käynnistys estetty", self.output.getvalue())

    def test_module_main_uses_adjacent_application_and_cli_arguments(self):
        """Välittää moduulin komentoriviargumentit viereisen sovelluksen käynnistykseen."""
        with (
            patch.object(sys, "argv", ["validator_bootstrap.py", "--server.port=8767"]),
            patch.object(bootstrap, "launch_from_terminal") as launch,
        ):
            bootstrap._main()
        launch.assert_called_once_with(Path(bootstrap.__file__).resolve().with_name(bootstrap.APP_FILENAME), ["--server.port=8767"])


if __name__ == "__main__":
    unittest.main()
