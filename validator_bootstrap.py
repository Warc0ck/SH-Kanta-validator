"""Riippuvuuksien tarkistus ja projektin virtuaaliympäristön käynnistysvalikko."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from dataclasses import dataclass


MINIMUM_PYTHON = (3, 10)
APP_FILENAME = "SHKanta-www-validator.py"
_REQUIREMENT_PATTERN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)\s*==\s*([^\s;#]+)")
_PROBE_SCRIPT = """
import importlib
import importlib.metadata
import importlib.util
import json
import sys

requirements = json.loads(sys.argv[1])
problems = []
for distribution, expected in requirements.items():
    try:
        actual = importlib.metadata.version(distribution)
        if actual != expected:
            problems.append(f"{distribution}: versio {actual}, tarvitaan {expected}")
        importlib.import_module(distribution.replace('-', '_'))
    except Exception as error:
        problems.append(f"{distribution}: tuonti tai versiotieto ei toimi ({error})")
print(json.dumps({
    'version': list(sys.version_info[:2]),
    'prefix': sys.prefix,
    'problems': problems,
    'has_pip': importlib.util.find_spec('pip') is not None,
}))
"""


@dataclass(frozen=True)
class EnvironmentCheck:
    """Säilyttää erillisessä Python-prosessissa tarkistetun ympäristön tilan."""

    ready: bool
    problems: tuple[str, ...]
    prefix_matches: bool = False
    python_supported: bool = False
    has_pip: bool = False


def _read_requirements(requirements_path: Path) -> dict[str, str]:
    """Lukee tarkat pakettiversiot ja hylkää ristiriitaiset tai tukemattomat rivit."""
    requirements = {}
    for number, raw_line in enumerate(requirements_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQUIREMENT_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError(f"requirements.txt, rivi {number}: tarvitaan muoto paketti==versio.")
        name, version = match.groups()
        name = re.sub(r"[-_.]+", "-", name).lower()
        if name in requirements and requirements[name] != version:
            raise ValueError(f"requirements.txt: paketille {name} on ristiriitaiset versiot.")
        requirements[name] = version
    if not requirements:
        raise ValueError("requirements.txt ei sisällä tarkistettavia riippuvuuksia.")
    return requirements


def _venv_python(venv_path: Path) -> Path:
    """Palauttaa virtuaaliympäristön Pythonin polun käyttöjärjestelmän mukaan."""
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def _check_environment(
    python_path: Path,
    requirements: dict[str, str],
    venv_path: Path | None = None,
) -> EnvironmentCheck:
    """Varmistaa Pythonin version, pakettien versiot ja toimivat tuonnit kohdeympäristössä."""
    if venv_path is not None and venv_path.is_symlink():
        return EnvironmentCheck(False, ("Projektin .venv on symbolinen linkki; sitä ei muokata.",))
    if not python_path.is_file():
        return EnvironmentCheck(False, (f"Python puuttuu: {python_path}",))
    try:
        result = subprocess.run(
            [str(python_path), "-c", _PROBE_SCRIPT, json.dumps(requirements)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "Pythonin käynnistys epäonnistui."
            return EnvironmentCheck(False, (detail,))
        data = json.loads(result.stdout.strip().splitlines()[-1])
        supported = tuple(data["version"]) >= MINIMUM_PYTHON
        prefix_matches = venv_path is not None and Path(data["prefix"]).resolve() == venv_path.resolve()
        problems = list(data["problems"])
        if not supported:
            problems.insert(0, "Tarvitaan Python 3.10 tai uudempi.")
        if venv_path is not None and not prefix_matches:
            problems.insert(0, "Python ei kuulu projektin .venv-ympäristöön; sitä ei muokata.")
        return EnvironmentCheck(not problems, tuple(problems), prefix_matches, supported, bool(data["has_pip"]))
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError, TypeError) as error:
        return EnvironmentCheck(False, (f"Ympäristön tarkistus epäonnistui: {error}",))


def _run_command(command: list[str], *, environment: dict[str, str] | None = None) -> bool:
    """Suorittaa valmistelukomennon näkyvällä tulosteella ja palauttaa onnistumistiedon."""
    try:
        kwargs = {"check": False}
        if environment is not None:
            kwargs["env"] = environment
        result = subprocess.run(command, **kwargs)
    except OSError as error:
        print(f"Komennon suoritus epäonnistui: {error}")
        return False
    if result.returncode != 0:
        print(f"Komento epäonnistui (paluuarvo {result.returncode}).")
        return False
    return True


def _installation_environment() -> dict[str, str]:
    """Estää pipin kohdeasetuksia ohjaamasta asennusta ulos projektista ja säilyttää verkkoasetukset."""
    location_options = {"PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_USER", "PIP_PYTHON", "PIP_CONFIG_FILE"}
    environment = {name: value for name, value in os.environ.items() if name.upper() not in location_options}
    # Pipin asetustiedostotkin voivat muuttaa asennuskohteen; ympäristön proxy- ja index-asetukset säilyvät.
    environment["PIP_CONFIG_FILE"] = os.devnull
    return environment


def _prepare_venv(venv_path: Path, requirements_path: Path, requirements: dict[str, str]) -> bool:
    """Luo puuttuvan projektivennin ja asentaa riippuvuudet vain varmennettuun ympäristöön."""
    if venv_path.is_symlink():
        print("Projektin .venv on symbolinen linkki; sitä ei muokata.")
        return False
    if not venv_path.exists():
        if sys.version_info[:2] < MINIMUM_PYTHON:
            print("Virtuaaliympäristön luomiseen tarvitaan Python 3.10 tai uudempi.")
            return False
        print(f"Luodaan virtuaaliympäristö: {venv_path}", flush=True)
        if not _run_command([sys.executable, "-m", "venv", str(venv_path)]):
            return False
    elif not venv_path.is_dir():
        print("Projektin .venv on tiedosto. Sitä ei korvata tai poisteta.")
        return False

    python_path = _venv_python(venv_path)
    status = _check_environment(python_path, requirements, venv_path)
    if not status.prefix_matches or not status.python_supported:
        print("Projektin .venv ei ole turvallisesti täydennettävissä tällä käynnistimellä.")
        for problem in status.problems:
            print(f"  {problem}")
        print("Olemassa olevaa ympäristöä ei poisteta. Korjaa se käsin tai käytä valmista nykyistä ympäristöä.")
        return False
    if not status.has_pip:
        print("Valmistellaan pip projektin virtuaaliympäristöön.", flush=True)
        if not _run_command([str(python_path), "-m", "ensurepip", "--upgrade"]):
            return False
    print("Asennetaan requirements.txt:n mukaiset riippuvuudet projektin .venv-ympäristöön.", flush=True)
    if not _run_command(
        [str(python_path), "-m", "pip", "install", "--prefix", str(venv_path), "-r", str(requirements_path)],
        environment=_installation_environment(),
    ):
        return False
    status = _check_environment(python_path, requirements, venv_path)
    if not status.ready:
        print("Ympäristön tarkistus ei onnistunut asennuksen jälkeen:")
        for problem in status.problems:
            print(f"  {problem}")
        return False
    return True


def _exec_streamlit(python_path: Path, app_path: Path, arguments: list[str]) -> None:
    """Korvaa käynnistysprosessin valitun Pythonin Streamlit-prosessilla ja välittää argumentit."""
    print(f"Käynnistetään SHKanta validointityökalu: {python_path}", flush=True)
    command = [str(python_path), "-m", "streamlit", "run", str(app_path), *arguments]
    try:
        os.execv(str(python_path), command)
    except OSError as error:
        print(f"Sovelluksen käynnistys epäonnistui: {error}")
        raise SystemExit(1) from error
    raise SystemExit(0)


def _launch_from_terminal(app_path: Path, arguments: list[str]) -> None:
    """Tarkistaa ympäristöt ja tarjoaa tarvittavat valmistelut suomenkielisessä valikossa."""
    if not app_path.is_file():
        print(f"Sovellustiedostoa ei löydy: {app_path}")
        raise SystemExit(1)
    venv_path = app_path.parent / ".venv"
    requirements_path = app_path.parent / "requirements.txt"
    try:
        requirements = _read_requirements(requirements_path)
    except (OSError, ValueError) as error:
        print(f"Riippuvuuksien lukeminen epäonnistui: {error}")
        raise SystemExit(1) from error

    python_path = _venv_python(venv_path)
    while True:
        venv_status = _check_environment(python_path, requirements, venv_path)
        if venv_status.ready:
            _exec_streamlit(python_path, app_path, arguments)

        current_python = Path(sys.executable)
        current_status = _check_environment(current_python, requirements)
        print("\nSHKanta validointityökalu — ympäristön valmistelu")
        print(f"Projektin ympäristö: {venv_path}")
        for problem in venv_status.problems:
            print(f"  {problem}")
        print("1. Luo/täydennä .venv, asenna riippuvuudet ja käynnistä")
        if current_status.ready:
            print(f"2. Käynnistä nykyisellä valmiilla Python-ympäristöllä ({current_python})")
        else:
            print("Nykyinen Python-ympäristö ei täytä vaatimuksia:")
            for problem in current_status.problems:
                print(f"  {problem}")
        print("0. Lopeta")
        if sys.stdin is None or not sys.stdin.isatty():
            print("Valmistelu vaatii interaktiivisen terminaalin. Riippuvuuksia ei asennettu.")
            raise SystemExit(1)
        try:
            selection = input("Valinta: ").strip()
        except EOFError:
            print("\nSyöte päättyi. Käynnistys lopetettiin.")
            raise SystemExit(0)
        if selection == "0":
            print("Käynnistys lopetettiin.")
            raise SystemExit(0)
        if selection == "1":
            if _prepare_venv(venv_path, requirements_path, requirements):
                _exec_streamlit(python_path, app_path, arguments)
            print("Valmistelu ei onnistunut. Voit yrittää uudelleen tai lopettaa valikosta.")
        elif selection == "2" and current_status.ready:
            _exec_streamlit(current_python, app_path, arguments)
        else:
            print("Valitse jokin valikossa näkyvistä numeroista.")


def launch_from_terminal(app_path: Path, arguments: list[str] | None = None) -> None:
    """Käynnistää sovelluksen tai sen valmisteluvalikon ilman ulkoisia Python-riippuvuuksia."""
    try:
        _launch_from_terminal(Path(app_path).resolve(), list(sys.argv[1:] if arguments is None else arguments))
    except KeyboardInterrupt:
        print("\nKäynnistys keskeytettiin.")
        raise SystemExit(130)


def _main() -> None:
    """Käynnistää moduulin vieressä olevan sovelluksen suoraan terminaalista."""
    launch_from_terminal(Path(__file__).resolve().with_name(APP_FILENAME), sys.argv[1:])


if __name__ == "__main__":
    _main()
