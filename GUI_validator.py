import importlib
import subprocess
import sys

# Lista riippuvuuksista
required_packages = ["requests", "tkinter", "json", "re", "datetime", "xml.etree.ElementTree", "datetime", "base64"]

def check_and_install(packages):
    for package in packages:
        try:
            importlib.import_module(package)
            # print(f"{package} on jo asennettu.")
        except ImportError:
            print(f"{package} puuttuu. Asennetaan...")
            subprocess.run([sys.executable, "-m", "pip", "install", package])

if __name__ == "__main__":
    check_and_install(required_packages)

import requests
import json
import xml.etree.ElementTree as ET
import re
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime
import base64
import os

# Funktio vastauksen tallentamiseksi tiedostoon
def save_to_file(filename, content):
    with open(filename, 'w') as f:
        f.write(content)

# Funktio tuloksen näyttämiseksi uudessa ikkunassa
def show_result(title, content, window_size="800x600"):
    result_window = tk.Toplevel()
    result_window.title(title)
    result_window.geometry(window_size)
    text_widget = tk.Text(result_window, wrap="word")
    text_widget.insert("1.0", content)
    text_widget.pack(expand=1, fill="both")
    text_widget.config(state="disabled")


# tarkistaa löytyykö ClinicalDocument xmlns, jos ei tekee B64 decode
_CLINICALDOC_REGEX = re.compile(r"<[^>]*ClinicalDocument xmlns\b", re.IGNORECASE)

# Tunnista sekä HTML-escapeattu että aito XML
# _CLINICALDOC_REGEX = re.compile(r"(&lt;[^&gt;]*ClinicalDocument\b|<[^>]*ClinicalDocument\b)", re.IGNORECASE)


def ensure_clinical_document_xml(asiakirjaXml, source_filename: str = None, verbose: bool = True):
    """
    Palauttaa ClinicalDocument-XML:n tekstinä.
    - Jos suoraan tekstissä löytyy <...ClinicalDocument...>, palauttaa sellaisenaan.
    - Muuten yrittää Base64-dekoodauksen useilla strategioilla (URL-safe, padding-korjaus).
    - CDATA-lohkon sisältö otetaan ulos ennen tarkistusta.
    Heittää ValueError, jos ei löydy.
    """   


    def _to_text(b):
        """
        Muuntaa UTF-8-sisällön "ANSI-näkyväksi" (cp1252) 
        UTF-8-tavut tulkitaan cp1252-merkkeinä. Esim. 'ä' -> 'Ã¤', 'ö' -> 'Ã¶', 'å' -> 'Ã¥'.

        Syöte oletetaan UTF-8-muodossa.
        """
        
        # Oletetaan aina UTF-8, ei tarkisteta bytes-tyyppiä.
        # Jos b on bytes, dekoodataan UTF-8:ksi; muuten muutetaan str:ksi.
        text = b.decode("utf-8") if isinstance(b, (bytes, bytearray)) else str(b)
       
        # Muutetaan ANSI-yhteensopivaksi:
        # 'ignore' pudottaa merkit 
        # 'strict' antaa virheen
        ansi_text = text.encode("cp1252", errors="strict").decode("cp1252")
               
        return ansi_text
        
    
    def _clean_text(s: str) -> str:
        return s.replace("\ufeff", "").strip()

    def _looks_like_xml(s: str) -> bool:
        return bool(_CLINICALDOC_REGEX.search(s))

    def _strip_cdata(s: str) -> str:
        # Tue sekä HTML-escapeattu että aito CDATA                                           
        m = re.search(r"&amp;lt;!\[CDATA\[(.*?)\]\]&amp;gt;", s, flags=re.DOTALL) or \
            re.search(r"&lt;!\[CDATA\[(.*?)\]\]&gt;", s, flags=re.DOTALL)
        return m.group(1) if m else s
    text = _clean_text(_to_text(asiakirjaXml))
    text_no_cdata = _strip_cdata(text)
    if _looks_like_xml(text_no_cdata):
        if verbose:
            print("ClinicalDocument löytyi suoraan.")
        return text_no_cdata

    if verbose:
        print("ClinicalDocument ei löytynyt suoraan. Yritetään Base64-dekoodausta...")

    # 2) Base64-normalisointi                         
    def _b64_normalize(s: str) -> str:
        s = s.replace(r"\n|\r", "").strip()
        s = re.sub(r"[^A-Za-z0-9\+/_=\-]", "", s) # siivoa vieraat merkit
        s = s.replace("-", "+").replace("_", "/") # URL-safe -> standard
        pad = len(s) % 4
        if pad:
            s += "=" * (4 - pad) # lisää puuttuva padding
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
        decoded_text = _clean_text(_to_text(decoded))
        decoded_text = _strip_cdata(decoded_text)

        if _looks_like_xml(decoded_text):
            if verbose:
                print(f"ClinicalDocument löytyi Base64-dekoodauksen kautta ({label}).")
            return decoded_text
        else:
            if decoded_text.lstrip().startswith("&lt;") and verbose:
                print(f"Huomio: {label} tuotti XML:ää, mutta 'ClinicalDocument' ei löytynyt.")
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
    
# Pääfunktio käsittelemään skriptin logiikkaa
def main():
    # API-päätepiste
    url = "http://shvalidaattori.at.kanta.fi/shark-validointi/validoi/asiakirja/tulos"

    # Tiedostovalintaikkuna XML-tiedostojen valitsemiseksi
    kehys_file = filedialog.askopenfilename(title="Valitse Interface message xml-tiedosto", filetypes=[("XML files", "*.xml")])
    asiakirja_file = filedialog.askopenfilename(title="Valitse Trace message xml-tiedosto", filetypes=[("XML files", "*.xml")])

    if not kehys_file or not asiakirja_file:
        messagebox.showwarning("Huom", "Tiedostovalinta peruttiin.")
        return

    # tallennetaan asiakirja nimi
    asiakirja_filename = os.path.basename(asiakirja_file)

    with open(asiakirja_file, 'r') as f:
        asiakirjaXml = f.read()

    # Varmistetaan, että asiakirjaXml sisältää ClinicalDocumentin
    try:
        asiakirjaXml = ensure_clinical_document_xml(asiakirjaXml, source_filename=asiakirja_filename)
    except ValueError as e:
        messagebox.showerror("Virhe", str(e))
        return
    
    # siirtokehysXML-tiedoston lukeminen
    with open(kehys_file, 'r') as file1:
        siirtokehysXml = file1.read()

    # Aiemmin ei ollut mahdollisuutta tarkistaa B64 tiedostoa
    # with open(asiakirja_file, 'r') as file2:
    #    asiakirjaXml = file2.read()

    # Nimialueiden rekisteröinti
    namespaces = {'': 'urn:hl7-org:v3'}  # Oletusnimialue

    # siirtokehysXml:n jäsentäminen reasonCode-attribuutin arvon löytämiseksi
    try:
        root = ET.fromstring(siirtokehysXml)
        print("Juuri-elementti:", root.tag)  # Debuggaus: Tulosta juuri-elementti

        # Etsi controlActProcess-elementti
        control_act_process_element = root.find('.//{urn:hl7-org:v3}controlActProcess', namespaces)
        print("controlActProcess-elementti löytyi:", control_act_process_element is not None)  # Debuggaus

        if control_act_process_element is not None:
            # Etsi reasonCode-elementti controlActProcessin sisällä
            reason_code_element = control_act_process_element.find('{urn:hl7-org:v3}reasonCode')
            print("reasonCode-elementti löytyi:", reason_code_element is not None)  # Debuggaus

            if reason_code_element is not None:
                reason_code = reason_code_element.get('code')
                print("Palvelupyyntö: ", reason_code)
            else:
                print("reasonCode-elementtiä ei löytynyt. Tarkista XML-rakenne ja XPath-lauseke.")
        else:
            print("controlActProcess-elementtiä ei löytynyt. Tarkista XML-rakenne ja XPath-lauseke.")
    except ET.ParseError as e:
        messagebox.showerror("Virhe", f"Palvelupyyntö koodia ei löydy: {e}")
        return

    # POST-pyynnöllä lähetettävät tiedot
    data = {
        "messageId": "1234567890",
        "palveluPyynto": reason_code,
        "level": "1",
        "siirtokehysXml": siirtokehysXml,
        "asiakirjaXml": f"<?xml version=\"1.0\" encoding=\"utf-16\"?> {asiakirjaXml}"
    }
    
    # print(data) # Debuggaus: Tulosta POST-pyyntö

    # Otsikot
    headers = {
        "Content-Type": "application/json",
    }

    # POST-pyynnön lähettäminen
    response = requests.post(url, json=data, headers=headers)
    # print(response) # Debuggaus: Tulosta response

    # Nykyisen päivämäärän saaminen, tiedoston tallentamista varten
    current_date = datetime.now().strftime("%d%m%y")


    # Tarkistetaan Content-Type ennen varsinaista käsittelyä
    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type:
        print("Palvelin palautti HTML-sisällön.")
        html_content = response.text
        error_html_filename = filedialog.asksaveasfilename(defaultextension=".html", initialfile=f'{current_date}_html_response.html', filetypes=[("HTML files", "*.html")])
        if error_html_filename:
            save_to_file(error_html_filename, html_content)
        show_result("HTML-vastaus", html_content)
        return  # Ei jatketa JSON-käsittelyyn

    # Tarkistetaan, oliko pyyntö onnistunut
    if response.status_code == 200:
        formatted_response = "Sanoma validoitu onnistuneesti"
        print("Pyyntö onnistui:", formatted_response)

        # Tallenna vastaus tiedostoon
        response_filename = filedialog.asksaveasfilename(defaultextension=".json", initialfile=f'{current_date}_response.json', filetypes=[("JSON files", "*.json")])
        if response_filename:
            save_to_file(response_filename, formatted_response)

        show_result("Pyyntö onnistui", formatted_response)
    else:
        # Siistitty virhevastaus
        try:
            error_response = response.json()
            formatted_error = json.dumps(error_response, indent=4, ensure_ascii=False)
            print("Virhe pyynnössä:", response.status_code, formatted_error)

            # Tallenna virhevastaus tiedostoon
            error_filename = filedialog.asksaveasfilename(defaultextension=".json", initialfile=f'{current_date}_error_response.json', filetypes=[("JSON files", "*.json")])
            if error_filename:
                save_to_file(error_filename, formatted_error)

            # Muotoile virhekuvaus
            if 'description' in error_response:
                formatted_description = re.sub(r'(?<=\d;)', '\n', error_response['description'])
                formatted_description = re.sub(r';', r';\n', formatted_description)
                formatted_description = re.sub(r' +', ' ', formatted_description).strip()
                formatted_description = re.sub(r'( \d+: )', r'\n\1', formatted_description)
                formatted_description += f"\n\nPalvelupyyntö: {reason_code}"  # Lisää reason_code muotoiltuun virhekuvaukseen
                print("Muotoiltu virhekuvaus:\n", formatted_description)
                error_description_filename = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=f'{current_date}_formatted_error_description.txt', filetypes=[("Text files", "*.txt")])
                if error_description_filename:
                    save_to_file(error_description_filename, formatted_description)
                show_result("Virhe pyynnössä", formatted_description)
            
        except json.JSONDecodeError:
            print("Virhe pyynnössä:", response.status_code, response.text)
            error_text_filename = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=f'{current_date}_error_response.txt', filetypes=[("Text files", "*.txt")])
            if error_text_filename:
                save_to_file(error_text_filename, response.text)
            messagebox.showerror("Virhe", f"Virhe pyynnössä:\n{response.text}")

# Luo pääikkuna
root = tk.Tk()
root.title("SOSH Kanta validointityökalu 2.0")
root.geometry("600x300")

# Lisää ohjeteksti
# Ylempi rivi
instructions_top = tk.Label(root, text="Valitse XML-tiedostot aloittaaksesi validoinnin.", font=("Helvetica", 14))
instructions_top.pack(pady=(20, 5))

# Alempi rivi
instructions_bottom = tk.Label(
    root, 
    text="Valitse:\n1. interface message xml (kehys)\n2. trace message xml\n3. Tallenna palautunut JSON-muotoinen vastaus\n4. Tallenna luettavassa muodossa oleva virhevastaus\n5. Tallennusten jälkeen avautuu ikkuna,\n   jossa luettavassa muodossa oleva virhevastaus\n\nKohdat 3. ja 4. vapaaehtoisia", 
    font=("Courier New", 12), 
    fg="gray",
    justify="left"
)
instructions_bottom.pack(pady=(0, 20))

# Lisää painike skriptin suorittamiseksi
run_button = tk.Button(
    root, 
    text="Valitse tiedostot", 
    command=main,
    font=("Segoe UI", 12)
)
run_button.pack(pady=20)

# Käynnistä Tkinter-käyttöliittymä
root.mainloop()
