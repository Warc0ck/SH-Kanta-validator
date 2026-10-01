import importlib
import subprocess
import sys

# Lista riippuvuuksista
required_packages = ["requests", "json", "re", "datetime", "xml.etree.ElementTree", "base64", "streamlit"]

def check_and_install(packages):
    for package in packages:
        try:
            importlib.import_module(package)
        except ImportError:
            print(f"{package} puuttuu. Asennetaan...")
            subprocess.run([sys.executable, "-m", "pip", "install", package])

if __name__ == "__main__":
    check_and_install(required_packages)

import requests
import json
import xml.etree.ElementTree as ET
import re
from datetime import datetime
import base64
import os
import streamlit as st # Käytetään suoritukseen Streamlit-kirjastoa, joka tarjoaa web-käyttöliittymän.
from streamlit.web import cli as stcli # Streamlit CLI:n käyttö mahdollistaa sovelluksen ajamisen komentoriviltä.

# === Apufunktio tavujen muuntamiseksi tekstiksi eri merkistökoodauksilla ===
def _to_text_helper(b):
    if isinstance(b, bytes):
        for enc in ("utf-8", "utf-16", "utf-16le", "utf-16be", "latin-1"):
            try:
                return b.decode(enc)
            except UnicodeDecodeError:
                continue
        return b.decode("utf-8", errors="replace")
    return str(b) if b is not None else ""

# === Funktio Base64-koodattujen lohkojen (nonXMLBody, JSON jne.) etsimiseen ja purkamiseen hetu-tarkistukseen ===
def extract_base64_contents(xml_text: str) -> list:
    """
    Etsii XML- ja tekstiaineistosta Base64-koodatut lohkot (esim. nonXMLBody, JSON ja B64-elementit)
    ja palauttaa ne purettuna tekstinä.
    """
    decoded_texts = []
    
    # Etsitään XML-elementeistä teksti (esim. <text representation="B64"> tai <nonXMLBody>...)
    b64_tag_pattern = re.compile(
        r'<(?:[a-zA-Z0-9_]+:)?(?:nonXMLBody|text|value|data)[^>]*>(.*?)</(?:[a-zA-Z0-9_]+:)?(?:nonXMLBody|text|value|data)>', 
        re.DOTALL | re.IGNORECASE
    )
    
    # Etsitään myös yleisiä pitkiä Base64-merkkijonoja tekstistä
    b64_string_pattern = re.compile(r'(?:[A-Za-z0-9+/]{4}){6,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?')

    candidates = set()
    for match in b64_tag_pattern.finditer(xml_text):
        raw_val = match.group(1).strip()
        # Jos sisältää CDATA, poistetaan CDATA-kääre
        cdata_match = re.search(r'<!\[CDATA\[(.*?)\]\]>', raw_val, re.DOTALL)
        if cdata_match:
            raw_val = cdata_match.group(1).strip()
        # Poistetaan mahdolliset sisäiset XML-tägit
        clean_val = re.sub(r'<[^>]+>', '', raw_val).strip()
        if clean_val:
            candidates.add(clean_val)

    for match in b64_string_pattern.finditer(xml_text):
        candidates.add(match.group(0).strip())

    for cand in candidates:
        cleaned_cand = "".join(cand.split())
        if len(cleaned_cand) < 16 or len(cleaned_cand) % 4 != 0:
            continue
        try:
            decoded_bytes = base64.b64decode(cleaned_cand, validate=True)
            dec_text = _to_text_helper(decoded_bytes)
            if dec_text and len(dec_text) > 5:
                decoded_texts.append(dec_text)
        except Exception:
            continue

    return decoded_texts

# === Funktio muun kuin 9-alkuisen henkilötunnuksen tunnistamiseen ===
def find_non_test_hetus(xml_text: str) -> list:
    """
    Etsii tekstistä sekä sen sisällä olevista Base64-koodatuista osioista (kuten nonXMLBody ja JSON)
    henkilötunnukset, joiden loppuosa ei ala numerolla 9 (eli alkaa numerolla 0-8).
    Testihenkilötunnuksissa loppuosa alkaa aina numerolla 9 (900-999).
    """
    hetu_pattern = re.compile(
        r"\b(0[1-9]|[12][0-9]|3[01])(0[1-9]|1[0-2])\d{2}[-+ABCDEFYX]([0-8]\d{2})[0-9A-FHJ-NPR-TW-Z]\b",
        re.IGNORECASE
    )
    
    # 1. Etsitään suoraan raakatekstistä
    found_hetus = set(match.group(0) for match in hetu_pattern.finditer(xml_text))

    # 2. Etsitään Base64-koodatuista osioista (nonXMLBody, JSON jne.)
    base64_payloads = extract_base64_contents(xml_text)
    for payload in base64_payloads:
        for match in hetu_pattern.finditer(payload):
            found_hetus.add(match.group(0))

    return list(found_hetus)

# === Funktio ClinicalDocument id/setId @root -tunnisteiden lukemiseen ===
def extract_clinical_doc_identifiers(xml_text: str):
    """
    Etsii XML-tekstistä ClinicalDocument- / clinicalDocument -elementin id:n ja setId:n @root-arvot.
    Tukee sekä suoraa dokumenttia että siirtokehyksen sisällä olevaa dokumenttia.
    """
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None, None

    clin_doc = None
    # Muunnetaan tägin nimi pieniksi kirjaimiksi vertailua varten (.lower()), jolloin sekä "ClinicalDocument" että "clinicalDocument" täsmäävät.
    if root.tag.lower().endswith("clinicaldocument"):
        clin_doc = root
    else:
        for elem in root.iter():
            if elem.tag.lower().endswith("clinicaldocument"):
                clin_doc = elem
                break

    if clin_doc is None:
        return None, None

    id_root = None
    set_id_root = None

    for child in clin_doc:
        tag_name = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag_name == "id":
            id_root = child.get("root")
        elif tag_name == "setId":
            set_id_root = child.get("root")

    return id_root, set_id_root

# === Apufuntio tarkistaa löytyykö ClinicalDocument xmlns -alkuinen XML-elementti ===
_CLINICALDOC_REGEX = re.compile(r"<[^>]*ClinicalDocument xmlns\b", re.IGNORECASE)

# === Funktio ClinicalDocument-XML:n varmistamiseen ===
def ensure_clinical_document_xml(asiakirjaXml, source_filename: str = None, verbose: bool = True):
    """
    Palauttaa ClinicalDocument-XML:n tekstinä.
    - Jos suoraan tekstissä löytyy <...ClinicalDocument...>, palauttaa sellaisenaan.
    - Muuten yrittää Base64-dekoodauksen useilla strategioilla (URL-safe, padding-korjaus).
    - CDATA-lohkon sisältö otetaan ulos ennen tarkistusta.
    Heittää ValueError, jos ei löydy.
    """   
    def _clean_text(s: str) -> str:
        return s.replace("\ufeff", "").strip()

    def _looks_like_xml(s: str) -> bool:
        return bool(_CLINICALDOC_REGEX.search(s))

    def _strip_cdata(s: str) -> str:
        m = re.search(r"&amp;lt;!\[CDATA\[(.*?)\]\]&amp;gt;", s, flags=re.DOTALL) or \
            re.search(r"&lt;!\[CDATA\[(.*?)\]\]&gt;", s, flags=re.DOTALL)
        return m.group(1) if m else s

    # Kutsutaan suoraan globaalia apufunktiota
    text = _clean_text(_to_text_helper(asiakirjaXml))
    text_no_cdata = _strip_cdata(text)
    if _looks_like_xml(text_no_cdata):
        if verbose:
            st.info("ClinicalDocument löytyi suoraan.")
        return text_no_cdata

    if verbose:
        st.info("ClinicalDocument ei löytynyt suoraan. Yritetään Base64-dekoodausta...")

    def _b64_normalize(s: str) -> str:
        s = s.replace(r"\n|\r", "").strip()
        s = re.sub(r"[^A-Za-z0-9\+/_=\-]", "", s)
        s = s.replace("-", "+").replace("_", "/")
        pad = len(s) % 4
        if pad:
            s += "=" * (4 - pad)
        return s

    candidates = []
    norm = _b64_normalize(text)
    if norm:
        candidates.append(("b64-normalized", norm))

    compact = "".join(text.split())
    norm_compact = _b64_normalize(compact)
    if norm_compact and norm_compact != norm:
        candidates.append(("b64-compact", norm_compact))

    if text != text_no_cdata:
        norm_cdata = _b64_normalize(text_no_cdata)
        if norm_cdata:
            candidates.append(("b64-from-cdata", norm_cdata))

    last_errors = []
    for label, cand in candidates:
        try:
            decoded = base64.b64decode(cand, validate=False)
        except Exception as e:
            if source_filename:
                last_errors.append(f"{label}: {source_filename}: b64 decode error: {e}")
            else:
                last_errors.append(f"{label}: b64 decode error: {e}")
            continue

        # Kutsutaan suoraan globaalia apufunktiota
        decoded_text = _clean_text(_to_text_helper(decoded))
        decoded_text = _strip_cdata(decoded_text)

        if _looks_like_xml(decoded_text):
            if verbose:
                st.success(f"ClinicalDocument löytyi Base64-dekoodauksen kautta ({label}).")
            return decoded_text
        else:
            if decoded_text.lstrip().startswith("&lt;") and verbose:
                st.warning(f"Huomio: {label} tuotti XML:ää, mutta 'ClinicalDocument' ei löytynyt.")
            if source_filename:
                last_errors.append(f"{source_filename} ei sisällä ClinicalDocumentia.")
            else:
                last_errors.append(f"XML ei sisällä ClinicalDocumentia.")

    if last_errors:
        dbg = "\n".join(last_errors)
        raise ValueError(
            "ClinicalDocument ei löytynyt asiakirjaXml:stä, ei alkuperäisenä eikä Base64-dekoodattuna.\n"
            f"\n{dbg}"
        )
    else:
        raise ValueError("ClinicalDocumentia ei löytynyt, eikä Base64-dekoodattavia kandidaatteja muodostunut.")

# === Päätoiminto rakennettu Streamlit-verkkokäyttöliittymäksi ===
def main():
    st.set_page_config(page_title="SOSH Kanta validointityökalu 3.0", layout="centered")

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
    .block-container {
        padding-bottom: 60px;
    }
    </style>
    <div class="custom-footer">
        Tomi Vesala, 2024. Kanta-sanomien validointityökalu. Ei virallinen Kanta-tuote.<br/>
        Päivitetty 28.09.2026
    </div>
    """
    st.markdown(footer_html, unsafe_allow_html=True)

    st.title("SOSH Kanta validointityökalu 2.0")
    
    st.markdown("""
    **Valitse:**
    1. Interface message xml (kehys)
    2. Trace message xml tai DocumentXML (asiakirja)
    3. Suorita validointi painamalla painiketta
    4. Tarkastele tuloksia ja lataa vastaukset tarvittaessa
    """)

    st.divider()

    # === Tiedostojensyöttökentät ===
    kehys_file = st.file_uploader("Valitse Interface message xml-tiedosto (kehys)", type=["xml"])
    asiakirja_file = st.file_uploader("Valitse Trace message xml-tiedosto", type=["xml"])

    if st.button("Suorita validointi", type="primary"):
        if not kehys_file or not asiakirja_file:
            st.warning("Valitse molemmat XML-tiedostot ennen validoinnin aloittamista.")
            return

        url = "http://shvalidaattori.at.kanta.fi/shark-validointi/validoi/asiakirja/tulos"
        asiakirja_filename = asiakirja_file.name

        # Luetaan ladattujen tiedostojen raakatavut
        asiakirja_bytes = asiakirja_file.getvalue()
        siirtokehys_bytes = kehys_file.getvalue()

        # Varmistetaan, että asiakirjaXml sisältää ClinicalDocumentin
        try:
            asiakirjaXml = ensure_clinical_document_xml(asiakirja_bytes, source_filename=asiakirja_filename)
        except ValueError as e:
            st.error(f"Virhe asiakirjan käsittelyssä:\n{e}")
            return

        # Tulkitaan siirtokehysXml tekstiksi uudistetulla dekoodauksella
        siirtokehysXml = _to_text_helper(siirtokehys_bytes)

        # Etsitään XML-sisällöstä kaikki henkilötunnukset, jotka eivät ole testitunnuksia
        hetus_kehys = find_non_test_hetus(siirtokehysXml)
        hetus_asiakirja = find_non_test_hetus(asiakirjaXml)
        kaikki_muut_hetus = list(set(hetus_kehys + hetus_asiakirja))

        # Jos löytyi ei-testitunnuksia, näytetään virheilmoitus ja lopetetaan validointi
        if kaikki_muut_hetus:
            st.error("⛔ PYSÄYTETTY: Tiedostoista löytyi henkilötunnuksia, jotka eivät ole testitunnuksia!**")
            st.warning(f"Seuraavien henkilötunnusten loppuosa ei ala numerolla 9: **{', '.join(kaikki_muut_hetus)}**")
            st.info("Varmista tietosuoja ennen pyynnön lähettämistä eteenpäin.")
            return

        # === ClinicalDocument id/setId @root -tarkistus kehyksen ja asiakirjan välillä ===
        kehys_id, kehys_setid = extract_clinical_doc_identifiers(siirtokehysXml)
        asiakirja_id, asiakirja_setid = extract_clinical_doc_identifiers(asiakirjaXml)

        # Tarkistetaan id/@root
        if kehys_id or asiakirja_id:
            if kehys_id and asiakirja_id and kehys_id == asiakirja_id:
                st.success(f"✅ **ClinicalDocument id (@root) täsmää:** `{asiakirja_id}`")
            else:
                st.error(
                    f"⚠️ **VIRHE: ClinicalDocument id (@root) ei täsmää!**\n"
                    f"- Kehys: `{kehys_id}`\n"
                    f"- Asiakirja: `{asiakirja_id}`"
                )

        # Tarkistetaan setId/@root
        if kehys_setid or asiakirja_setid:
            if kehys_setid and asiakirja_setid and kehys_setid == asiakirja_setid:
                st.success(f"✅ **ClinicalDocument setId (@root) täsmää:** `{asiakirja_setid}`")
            else:
                st.error(
                    f"⚠️ **VIRHE: ClinicalDocument setId (@root) ei täsmää!**\n"
                    f"- Kehys: `{kehys_setid}`\n"
                    f"- Asiakirja: `{asiakirja_setid}`"
                )

        # Nimialueiden rekisteröinti
        namespaces = {'': 'urn:hl7-org:v3'}

        # siirtokehysXml:n jäsentäminen reasonCode-attribuutin arvon löytämiseksi
        try:
            root = ET.fromstring(siirtokehysXml)
            control_act_process_element = root.find('.//{urn:hl7-org:v3}controlActProcess', namespaces)

            if control_act_process_element is not None:
                reason_code_element = control_act_process_element.find('{urn:hl7-org:v3}reasonCode')

                if reason_code_element is not None:
                    reason_code = reason_code_element.get('code')
                    st.info(f"Palvelupyyntö (reasonCode): **{reason_code}**")
                else:
                    st.error("reasonCode-elementtiä ei löytynyt. Tarkista XML-rakenne.")
                    return
            else:
                st.error("controlActProcess-elementtiä ei löytynyt. Tarkista XML-rakenne.")
                return
        except ET.ParseError as e:
            st.error(f"Palvelupyyntökoodia ei löydy: {e}")
            return

        # POST-pyynnön tiedot, otsikot ja JSON-data sekä lähetetään palvelimelle
        data = {
            "messageId": "1234567890",
            "palveluPyynto": reason_code,
            "level": "1",
            "siirtokehysXml": siirtokehysXml,
            "asiakirjaXml": f"<?xml version=\"1.0\" encoding=\"utf-16\"?> {asiakirjaXml}"
        }

        headers = {
            "Content-Type": "application/json",
        }

        with st.spinner("Lähetetään sanomaa validaattorille..."):
            response = requests.post(url, json=data, headers=headers)

        current_date = datetime.now().strftime("%d%m%y")
        content_type = response.headers.get("Content-Type", "")

        # Jos palvelin palauttaa HTML-sisällön
        if "text/html" in content_type:
            st.error("Palvelin palautti HTML-sisällön.")
            html_content = response.text
            st.components.v1.html(html_content, height=400, scrolling=True)
            
            # === Tiedoston latauspainike selaimessa ===
            st.download_button(
                label="Lataa HTML-vastaus",
                data=html_content,
                file_name=f"{current_date}_html_response.html",
                mime="text/html"
            )
            return

        # Tarkistetaan pyynnön tulos
        if response.status_code == 200:
            formatted_response = "Sanoma validoitu onnistuneesti"
            st.success(f"Pyyntö onnistui: {formatted_response}")
            
            # === Tiedoston latauspainike selaimessa ===
            st.download_button(
                label="Lataa vastaus (JSON)",
                data=formatted_response,
                file_name=f"{current_date}_response.json",
                mime="application/json"
            )
        else:
            try:
                error_response = response.json()
                formatted_error = json.dumps(error_response, indent=4, ensure_ascii=False)
                
                st.error(f"Virhe pyynnössä (Tilakoodi: {response.status_code})")
                
                # Käsitellään raaka vastaus ja muotoillaan virhekuvaus
                if 'description' in error_response:
                    formatted_description = re.sub(r'(?<=\d;)', '\n', error_response['description'])
                    formatted_description = re.sub(r';', r';\n', formatted_description)
                    formatted_description = re.sub(r' +', ' ', formatted_description).strip()
                    formatted_description = re.sub(r'( \d+: )', r'\n\1', formatted_description)
                    formatted_description += f"\n\nPalvelupyyntö: {reason_code}"

                    st.subheader("Muotoiltu virhekuvaus")
                    st.text_area("Virheen tiedot", formatted_description, height=300)

                    # === Latauspainikkeet virheraporteille ===
                    col1, col2 = st.columns(2)
                    with col1:
                        st.download_button(
                            label="Lataa muotoiltu virhekuvaus (.txt)",
                            data=formatted_description,
                            file_name=f"{current_date}_formatted_error_description.txt",
                            mime="text/plain"
                        )
                    with col2:
                        st.download_button(
                            label="Lataa alkuperäinen JSON-virhe",
                            data=formatted_error,
                            file_name=f"{current_date}_error_response.json",
                            mime="application/json"
                        )
            except json.JSONDecodeError:
                st.error(f"Virhe pyynnössä (Tilakoodi: {response.status_code})")
                st.text_area("Vastausteksti", response.text, height=200)
                st.download_button(
                    label="Lataa virhevastaus (.txt)",
                    data=response.text,
                    file_name=f"{current_date}_error_response.txt",
                    mime="text/plain"
                )

if __name__ == "__main__":
    if st.runtime.exists():
        main()
    else:
        sys.argv = ["streamlit", "run", __file__]
        sys.exit(stcli.main())
