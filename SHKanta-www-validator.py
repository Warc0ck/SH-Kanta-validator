#!/usr/bin/env python3
"""Streamlit-käyttöliittymä SOSH Kanta -sanomien validointiin."""

from datetime import datetime
from hashlib import sha256
from pathlib import Path
import os
import sys

try:
    import requests
    import streamlit as st
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Riippuvuus puuttuu. Asenna riippuvuudet komennolla "
        "python -m pip install -r requirements.txt ja käynnistä sovellus "
        "komennolla python SHKanta-www-validator.py."
    ) from exc

from validator_core import prepare_validation_request
from validator_response import interpret_response

VALIDATOR_URL = "http://shvalidaattori.at.kanta.fi/shark-validointi/validoi/asiakirja/tulos"
RESULT_KEY = "validation_result"


def _input_signature(frame_source, document_source):
    """Liittää tallennetun tuloksen täsmälleen käytettyihin syötteisiin."""
    digest = sha256()
    for source in (frame_source, document_source):
        value = source.encode("utf-8") if isinstance(source, str) else (source or b"")
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _run_validation(frame_source, document_source, signature, source_names):
    """Tekee paikalliset tarkistukset ennen yhtä HTTP-pyyntöä."""
    saved = {
        "signature": signature,
        "source_names": source_names,
        "file_date": datetime.now().strftime("%d%m%y"),
        "checks": [],
        "reason_code": None,
        "error": None,
        "response": None,
    }
    if not frame_source or not document_source:
        saved["error"] = "Syötä molemmat XML-sanomat ennen validoinnin aloittamista."
        return saved
    try:
        payload, checks = prepare_validation_request(frame_source, document_source)
    except ValueError as exc:
        saved["error"] = f"⛔ Validointi pysäytetty: {exc}"
        return saved

    saved["checks"] = checks
    saved["reason_code"] = payload["palveluPyynto"]
    with st.spinner("Lähetetään sanomaa validaattorille..."):
        try:
            response = requests.post(VALIDATOR_URL, json=payload, timeout=30)
        except requests.RequestException as exc:
            saved["error"] = f"Virhe lähetettäessä sanomaa: {exc}"
            return saved
    saved["response"] = interpret_response(
        response.status_code,
        response.headers.get("Content-Type", ""),
        response.text,
        saved["reason_code"],
    )
    return saved


def _render_result(saved, current_signature):
    """Näyttää myös aiemmalla suorituksella muodostetut raportit."""
    if saved["signature"] != current_signature:
        st.warning(
            "Syötteet ovat muuttuneet. Alla näkyvä tulos koskee edellisiä syötteitä. "
            "Suorita validointi uudelleen päivitetylle aineistolle."
        )
    sources = " / ".join(name for name in saved["source_names"] if name)
    if sources:
        st.caption(f"Validoidut syötteet: {sources}")
    for label, value in saved["checks"]:
        st.success(f"✅ ClinicalDocument {label} (@root) täsmää: `{value}`")
    if saved["reason_code"]:
        st.info(f"Palvelupyyntö (reasonCode): **{saved['reason_code']}**")
    if saved["error"]:
        st.error(saved["error"])
        return

    result = saved["response"]
    if result is None:
        return
    if result.success:
        st.success(result.message)
        return
    st.error(result.message)
    wrap_lines = st.session_state.get("rivitys", False)
    if result.formatted_description:
        st.subheader("Muotoiltu virhekuvaus")
        st.caption("Virheen tiedot")
        st.code(
            result.formatted_description, language=None, height=300,
            wrap_lines=wrap_lines,
        )
        st.download_button(
            "Lataa muotoiltu virhekuvaus (.txt)",
            data=result.formatted_description,
            file_name=f"{saved['file_date']}_formatted_error_description.txt",
            mime="text/plain", on_click="ignore", key="download_description",
        )
    st.caption("Alkuperäinen vastaus")
    st.code(
        result.response_text, language=None, height=250,
        wrap_lines=wrap_lines,
    )
    st.download_button(
        result.response_label, data=result.response_text,
        file_name=f"{saved['file_date']}_{result.response_filename_suffix}",
        mime=result.response_mime, on_click="ignore", key="download_response",
    )


# === Apufunktio: XML-syöte tiedostona tai liitettynä tekstinä ===
def xml_syote(otsikko: str, key: str):
    with st.container(border=True):
        st.markdown(f"**{otsikko}**")

        tapa = st.radio(
            "Syöttötapa",
            ["📁 Tiedosto", "📝 Teksti"],
            horizontal=True,
            key=f"{key}_tapa",
            label_visibility="collapsed",
        )

        if tapa == "📁 Tiedosto":
            tiedosto = st.file_uploader(
                f"{otsikko} – tiedosto",
                type=["xml"],
                key=f"{key}_tiedosto",
                label_visibility="collapsed",
            )
            if tiedosto is not None:
                return tiedosto.getvalue(), tiedosto.name
        else:
            teksti = st.text_area(
                f"{otsikko} – teksti",
                height=200,
                placeholder="Liitä XML tähän (Ctrl+V).",
                key=f"{key}_teksti",
                label_visibility="collapsed",
            )
            if teksti.strip():
                return teksti, f"liitetty teksti ({key})"

    return None, None

# === Rivityksen valintaruutu ===
def rivitysvalinta():
    rivita = st.checkbox(
        "Rivitä teksti",
        value=False,
        key="rivitys",
    )

    if rivita:
        tyyli = "white-space: pre-wrap !important; overflow-wrap: anywhere !important;"
    else:
        tyyli = "white-space: pre !important; overflow-wrap: normal !important;"

    st.markdown(
        f"<style>[data-testid='stTextArea'] textarea {{ {tyyli} }}</style>",
        unsafe_allow_html=True,
    )

# === Päätoiminto rakennettu Streamlit-verkkokäyttöliittymäksi ===
def main():
    st.set_page_config(page_title="SOSH Kanta validointityökalu 3.2", layout="centered")

    footer_html = """
    <style>
    .custom-footer {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        background-color: #262730;
        color: #f1f1f1;
        text-align: center;
        padding: 8px 0;
        font-size: 13px;
        border-top: 1px solid #262730;
        z-index: 9999;
    }
    .tooltip-container {
        position: relative;
        display: inline-block;
        cursor: pointer;
        margin-left: 10px;
        color: #0066cc;
        font-weight: bold;
    }
    .tooltip-box {
        visibility: hidden;
        width: 340px;
        background-color: #2b2b2b;
        color: #ffffff;
        text-align: left;
        border-radius: 6px;
        padding: 12px;
        position: absolute;
        z-index: 10000;
        bottom: 140%;
        left: 50%;
        transform: translateX(-50%);
        opacity: 0;
        transition: opacity 0.3s, visibility 0.3s;
        font-size: 12px;
        line-height: 1.5;
        box-shadow: 0px 4px 12px rgba(0,0,0,0.3);
        font-weight: normal;
    }
    .tooltip-box ul {
        margin: 5px 0 0 15px;
        padding: 0;
    }
    .tooltip-container:hover .tooltip-box {
        visibility: visible;
        opacity: 1;
    }
    .block-container,
    [data-testid="stMainBlockContainer"] {
        padding-bottom: 60px;
        max-width: 1200px !important;
    }
    [data-testid="stTextArea"] textarea {
        font-family: Consolas, "Courier New", monospace !important;
        font-size: 13px !important;
        line-height: 1.4 !important;
    }
    /* Palkit vain ylivuotavalle sisällölle, teeman alkuperäisellä peukalovärillä. */
    [data-testid="stCode"] > pre,
    [data-testid="stTextArea"] textarea {
        overflow: auto !important;
        scrollbar-width: thin !important;
        scrollbar-color: color-mix(in srgb, currentColor 40%, transparent) transparent !important;
    }
    /* Kiinteä koko estää macOS:n palkkien piiloutumisen. Standardityylit
       nollataan tässä, jotta ne eivät ohita WebKit-palkkien kokoa ja värejä. */
    @supports selector(::-webkit-scrollbar) {
        [data-testid="stCode"] > pre,
        [data-testid="stTextArea"] textarea {
            scrollbar-width: auto !important;
            scrollbar-color: auto !important;
        }
        [data-testid="stCode"] > pre::-webkit-scrollbar,
        [data-testid="stTextArea"] textarea::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        [data-testid="stCode"] > pre::-webkit-scrollbar-thumb,
        [data-testid="stTextArea"] textarea::-webkit-scrollbar-thumb {
            background: color-mix(in srgb, currentColor 40%, transparent);
            border-radius: 9999px;
        }
        [data-testid="stCode"] > pre::-webkit-scrollbar-track,
        [data-testid="stCode"] > pre::-webkit-scrollbar-corner,
        [data-testid="stTextArea"] textarea::-webkit-scrollbar-track,
        [data-testid="stTextArea"] textarea::-webkit-scrollbar-corner {
            background: transparent;
        }
    }

    </style>
    <div class="custom-footer">
        Tomi Vesala, 2024. Kanta-sanomien validointityökalu. Ei virallinen Kanta-tuote.<br/>
        Päivitetty 08.10.2026
        <span class="tooltip-container">
            ℹ️ Versiopäivitykset (v3.2)
            <div class="tooltip-box">
                <strong>Versiossa 3.2 tehdyt muutokset:</strong>
                <ul>
                    <li>Korjattu henkilötunnusten tunnistus ja XML:n käsittely.</li>
                    <li>Lisätty lähetyksen estävät tunniste- ja palvelupyyntötarkistukset.</li>
                    <li>Validointitulokset ja ladattavat raportit säilyvät näkymässä.</li>
                </ul>
                <strong>Versiossa 3.0 tehdyt muutokset:</strong>
                <ul>
                    <li>Siirretty verkkoselaimessa toimivaksi (Streamlit).</li>
                    <li><strong>Base64/JSON/nonXMLBody-tarkistus:</strong> Etsii henkilötunnuksia XML:stä ja koodatuista tekstisisällöistä.</li>
                    <li><strong>OID-tarkistus:</strong> Vertailee kehyksen ja asiakirjan <code>id/@root</code> ja <code>setId/@root</code> tunnisteet.</li>
                </ul>
            </div>
        </span>
    </div>
    """
    st.markdown(f"""<h1 style=\"text-align: center;\">SOSH Kanta validointityökalu 3.2</h1>{footer_html}""", unsafe_allow_html=True)

    # st.title("SOSH Kanta validointityökalu 3.2")
    
    st.divider()
        
    st.markdown("""
    **Valitse:**
    1. Interface message xml (kehys)
    2. Trace message xml tai DocumentXML (asiakirja)
    3. Suorita validointi painamalla painiketta
    4. Tarkastele tuloksia ja lataa vastaukset tarvittaessa
    """)

    st.divider()
    rivitysvalinta()

    # === Tiedostojensyöttökentät ===
    # XML-syötteet: kummallekin erikseen tiedosto tai liitetty teksti
    siirtokehys_source, kehys_nimi = xml_syote("1. Interface message xml (kehys)", "kehys")
    asiakirja_source, asiakirja_filename = xml_syote("2. Trace message xml tai DocumentXML (asiakirja)", "asiakirja")

    signature = _input_signature(siirtokehys_source, asiakirja_source)
    if st.button("Suorita validointi", type="primary"):
        st.session_state[RESULT_KEY] = _run_validation(
            siirtokehys_source, asiakirja_source, signature,
            (kehys_nimi, asiakirja_filename),
        )

    saved = st.session_state.get(RESULT_KEY)
    if saved is not None:
        _render_result(saved, signature)


def _launch():
    """Käynnistää Streamlitin vain suoraan Pythonista ajettaessa."""
    if st.runtime.exists():
        main()
        return
    command = [
        sys.executable, "-m", "streamlit", "run",
        str(Path(__file__).resolve()), *sys.argv[1:],
    ]
    # Korvaa käynnistysohjelma Streamlitillä, jotta Ctrl+C pysäyttää yhden
    # prosessin ilman odottavan Python-prosessin KeyboardInterrupt-jälkeä.
    os.execv(sys.executable, command)


if __name__ == "__main__":
    _launch()
