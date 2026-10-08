#!/usr/bin/env python3
"""Streamlit-käyttöliittymä SOSH Kanta -sanomien validointiin."""

from datetime import datetime
from hashlib import sha256
from pathlib import Path
import sys

from validator_bootstrap import launch_from_terminal


def _is_streamlit_runtime():
    """Tunnistaa valmiin Streamlit-ajon tuomatta puuttuvia riippuvuuksia."""
    loaded_streamlit = sys.modules.get("streamlit")
    runtime = getattr(loaded_streamlit, "runtime", None)
    return runtime is not None and runtime.exists()


# Valmistelu tapahtuu ennen kolmannen osapuolen tuonteja vain terminaalista.
if __name__ == "__main__" and not _is_streamlit_runtime():
    launch_from_terminal(Path(__file__).resolve(), sys.argv[1:])
    raise SystemExit(0)

try:
    import requests
    import streamlit as st
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Riippuvuus puuttuu. Käynnistä terminaalista komennolla "
        "python SHKanta-www-validator.py, jotta käynnistysvalikko voi "
        "valmistella virtuaaliympäristön ja asentaa riippuvuudet."
    ) from exc

from validator_core import prepare_validation_request
from validator_replacer import (
    encode_document_json_for_sending,
    prepare_document_for_inspection,
    replace_validation_inputs,
)
from validator_response import interpret_response

VALIDATOR_URL = "http://shvalidaattori.at.kanta.fi/shark-validointi/validoi/asiakirja/tulos"
RESULT_KEY = "validation_result"
REPLACEMENT_KEY = "replaced_inputs"
CONFIRMATION_KEY = "replacement_confirmed"
APP_VERSION = "3.3"
VERSION_HISTORY = (
    ("3.3", (
        "Lisätty henkilötietokenttien paikallinen korvaus XML- ja JSON-sisältöihin sekä XHTML-näyttömuodon tyhjennys.",
        "Korvatut sanomat voi tarkistaa ja ladata XML-tiedostoina. Lähetys vaatii tarkistusvahvistuksen.",
        "Asiakirjan JSON näkyy tarkistus-XML:ssä purettuna ja sisennettynä. Se koodataan Base64-muotoon ennen lähetystä.",
        "Lisätty suomenkieliset funktiokuvaukset ja korvaustoimintojen regressiotestit.",
        "Korjattu footerin näkyminen sekä tekstin rivitys ja vieritys.",
        "Versiopäivitykset avautuvat nyt ikkunaan, jossa voi selata aiempien versioiden muutoksia.",
        "Lisätty terminaalin käynnistysvalikko virtuaaliympäristön luontiin ja riippuvuuksien asennukseen.",
    )),
    ("3.2", (
        "Korjattu henkilötunnusten tunnistus ja XML:n käsittely.",
        "Lisätty lähetyksen estävät tunniste- ja palvelupyyntötarkistukset.",
        "Validointitulokset ja ladattavat raportit säilyvät näkymässä.",
    )),
    ("3.0", (
        "Siirretty verkkoselaimessa toimivaksi Streamlit-sovellukseksi.",
        "Base64/JSON/nonXMLBody-tarkistus etsii henkilötunnuksia XML:stä ja koodatuista tekstisisällöistä.",
        "OID-tarkistus vertailee kehyksen ja asiakirjan id/@root- ja setId/@root-tunnisteita.",
    )),
)


def _input_signature(frame_source, document_source):
    """Liittää tallennetun tuloksen täsmälleen käytettyihin syötteisiin."""
    digest = sha256()
    for source in (frame_source, document_source):
        value = source.encode("utf-8") if isinstance(source, str) else (source or b"")
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _run_validation(frame_source, document_source, signature, source_names, *, replaced=False):
    """Tekee paikalliset tarkistukset ennen yhtä HTTP-pyyntöä."""
    saved = {
        "signature": signature,
        "source_names": source_names,
        "replaced": replaced,
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
        document_source = encode_document_json_for_sending(document_source)
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
    if saved.get("replaced"):
        st.caption("Validointi tehtiin korvatuilla sanomilla.")
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


def _replacement_filename(source_name, fallback):
    """Muodostaa korvatun XML:n latausnimen alkuperäisen tiedoston nimestä."""
    stem = Path(source_name).stem if source_name and source_name.lower().endswith(".xml") else fallback
    return f"{stem}_korvattu.xml"


def _prepare_replacements(frame_source, document_source, signature, source_names):
    """Muodostaa paikalliset XML-kopiot ja liittää ne alkuperäisiin syötteisiin."""
    if not frame_source or not document_source:
        raise ValueError("Syötä molemmat XML-sanomat ennen henkilötietokenttien korvaamista.")
    frame_xml, document_xml = replace_validation_inputs(frame_source, document_source)
    return {
        "signature": signature,
        "frame_xml": frame_xml,
        "document_xml": prepare_document_for_inspection(document_xml),
        "source_names": (
            _replacement_filename(source_names[0], "kehys"),
            _replacement_filename(source_names[1], "asiakirja"),
        ),
    }


def confirm_sending():
    """Pyytää käyttäjää vahvistamaan korvattujen sanomien tarkistuksen ennen lähetystä."""
    return st.checkbox(
        "Olen tarkistanut korvatut sanomat ja varmistanut, ettei niissä ole henkilötietoja.",
        key=CONFIRMATION_KEY,
    )


def _render_replacements(replaced, current_signature):
    """Näyttää korvatut XML:t ja tarjoaa latauksen sekä vahvistetun validoinnin."""
    with st.container(border=True):
        st.subheader("Korvatut sanomat")
        st.warning(
            "Korvaus koskee tunnettuja henkilötietokenttiä. "
            "Tarkista myös vapaateksti ja liitteet ennen lähettämistä."
        )
        st.caption(
            "Asiakirjan JSON näkyy tarkistus-XML:ssä purettuna ja sisennettynä. "
            "JSON koodataan automaattisesti Base64-muotoon ennen validointipalveluun lähettämistä."
        )
        wrap_lines = st.session_state.get("rivitys", False)
        for label, field, filename, key in (
            ("Siirtokehys", "frame_xml", replaced["source_names"][0], "download_replaced_frame"),
            ("Asiakirja", "document_xml", replaced["source_names"][1], "download_replaced_document"),
        ):
            st.caption(label)
            st.code(replaced[field], language="xml", height=250, wrap_lines=wrap_lines)
            st.download_button(
                f"Lataa korvattu {label.lower()} (.xml)",
                data=replaced[field].encode("utf-8"), file_name=filename,
                mime="application/xml", on_click="ignore", key=key,
            )
        confirmed = confirm_sending()
        if st.button("Validoi korvatut sanomat", key="validate_replaced", disabled=not confirmed):
            if confirmed and replaced["signature"] == current_signature:
                st.session_state[RESULT_KEY] = _run_validation(
                    replaced["frame_xml"], replaced["document_xml"], current_signature,
                    replaced["source_names"], replaced=True,
                )


@st.dialog("Versiopäivitykset", width="large")
def _show_version_history():
    """Avaa selattavan muutoshistorian uusimmasta versiosta vanhimpaan."""
    st.caption(f"Nykyinen versio {APP_VERSION}. Avaa aiempi versio nähdäksesi sen muutokset.")
    for version, changes in VERSION_HISTORY:
        with st.expander(f"Versio {version}", expanded=version == APP_VERSION):
            st.markdown("\n".join(f"- {change}" for change in changes))


def _render_footer():
    """Näyttää kiinteän footerin ja muutoshistoriaikkunan avaavan painikkeen."""
    with st.container(key="app_footer", horizontal_alignment="center", gap="xxsmall"):
        st.caption(
            "Tomi Vesala, 2024. Kanta-sanomien validointityökalu. Ei virallinen Kanta-tuote.",
            text_alignment="center",
        )
        with st.container(
            horizontal=True, horizontal_alignment="center", vertical_alignment="center", gap="small",
        ):
            st.caption("Päivitetty 08.10.2026", width="content")
            history_clicked = st.button(
                f"Versiopäivitykset (v{APP_VERSION})", key="version_history",
                type="tertiary", icon=":material/history:",
            )
    if history_clicked:
        _show_version_history()


# === Apufunktio: XML-syöte tiedostona tai liitettynä tekstinä ===
def xml_syote(otsikko: str, key: str):
    """Lukee yhden XML-syötteen tiedostona tai tekstinä ja palauttaa sen nimen."""
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
    """Tarjoaa rivitysvalinnan ja soveltaa sen XML-tekstikenttiin."""
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
    """Rakentaa validoinnin ja paikallisen henkilötietokenttien korvauksen näkymän."""
    st.set_page_config(page_title=f"SOSH Kanta validointityökalu {APP_VERSION}", layout="centered")

    footer_html = """
    <style>
    .st-key-app_footer {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        background-color: #262730;
        color: #f1f1f1;
        text-align: center;
        padding: 6px 12px;
        font-size: 13px;
        border-top: 1px solid #262730;
        z-index: 50;
        box-sizing: border-box;
    }
    .st-key-app_footer [data-testid="stCaptionContainer"],
    .st-key-app_footer button {
        color: #f1f1f1;
    }
    .block-container,
    [data-testid="stMainBlockContainer"] {
        padding-bottom: 120px;
        max-width: 1200px !important;
    }
    [data-testid="stTextArea"] textarea {
        font-family: Consolas, "Courier New", monospace !important;
        font-size: 13px !important;
        line-height: 1.4 !important;
    }
    /* Palkit vain ylivuotavalle sisällölle, teeman alkuperäisellä värillä. */
    [data-testid="stCode"] > pre,
    [data-testid="stTextArea"] textarea {
        overflow: auto !important;
        scrollbar-width: thin !important;
        scrollbar-color: color-mix(in srgb, currentColor 40%, transparent) transparent !important;
    }
    /* Kiinteä koko estää palkkien piiloutumisen. Standardityylit
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
    """
    st.markdown(f"<h1 style=\"text-align: center;\">SOSH Kanta validointityökalu {APP_VERSION}</h1>", unsafe_allow_html=True)
    
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
    replaced = st.session_state.get(REPLACEMENT_KEY)
    if replaced is not None and replaced["signature"] != signature:
        st.session_state.pop(REPLACEMENT_KEY)
        st.session_state[CONFIRMATION_KEY] = False
        st.info("Syötteet ovat muuttuneet. Muodosta korvatut sanomat uudelleen.")

    with st.container(horizontal=True):
        if st.button("Suorita validointi", type="primary", key="validate_original"):
            st.session_state[RESULT_KEY] = _run_validation(
                siirtokehys_source, asiakirja_source, signature,
                (kehys_nimi, asiakirja_filename),
            )
        replace_clicked = st.button("Korvaa henkilötietokentät", key="replace_personal_data")

    if replace_clicked:
        st.session_state.pop(REPLACEMENT_KEY, None)
        st.session_state[CONFIRMATION_KEY] = False
        try:
            st.session_state[REPLACEMENT_KEY] = _prepare_replacements(
                siirtokehys_source, asiakirja_source, signature,
                (kehys_nimi, asiakirja_filename),
            )
        except ValueError as exc:
            st.error(f"Henkilötietokenttien korvaus pysäytetty: {exc}")

    replaced = st.session_state.get(REPLACEMENT_KEY)
    if replaced is not None:
        _render_replacements(replaced, signature)

    saved = st.session_state.get(RESULT_KEY)
    if saved is not None:
        _render_result(saved, signature)

    # Renderöi tyylit erikseen, jotta CSS:n tyhjät rivit eivät katkaise niitä.
    st.html(footer_html)
    _render_footer()


def _launch():
    """Avaa sovelluksen Streamlit-ajossa tai ohjaa terminaalin valmisteluun."""
    if st.runtime.exists():
        main()
        return
    launch_from_terminal(Path(__file__).resolve(), sys.argv[1:])


if __name__ == "__main__":
    _launch()
