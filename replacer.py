def korvaaja(element, parent_list):
    if element.tag == "id":
        try:
            hetu = element.get("extension")
            if re.match(r"^[0-9]{6}[A-FU-Y\-\+][0-9]{3}[A-Y0-9]$", hetu):
                element.set("extension", "010181-900C")
        except:
            pass
    elif element.tag in ["given", "family"]:
        if "name" in parent_list:
            element.text = "feikkinimi"
    elif element.tag =="birthTime":
        try:
            element.set("value", "19810101")
        except:
            pass

    if len(element) > 0:
        new_parent_list = [a for a in parent_list]
        new_parent_list.append(element.tag)
        for child in element:
            korvaaja(child, new_parent_list)

def json_korvaaja(element, parent):
    if type(element) is dict:
        for key, value in element.items():
            if type(value) is dict:
                json_korvaaja(value, key)
            if type(value) is list:
                json_korvaaja(value, None)
            elif key in ["sukunimi", "etunimet"]:
                element[key] = "feikkinimi"
            elif key == "postinumero":
                element[key] = "12345"
            elif key == "lahiosoite":
                element[key] = "Katuosoite 1"
            elif key == "postitoimipaikka":
                element[key] = "Kaupunki"
            elif key == "puhelinnumero":
                element[key] = "012 345 6789"
            elif key ==  "sahkopostiosoite":
                element[key] = "aaaa@aaaa.com"
            elif key ==  "syntymaaika":
                element[key] = "1981-01-01"
            elif key == "value" and parent in ["henkilotunnus", "tilapainen_yksilointitunnus"]:
                element[key] = "010181-900C"
    if type(element) is list:
        for list_item in element:
            json_korvaaja(list_item, None)

def confirm_sending():
    choice = messagebox.askyesno("Jatketaanko?", "Tarkista siivotut asiakirjat, ettei ne sisällä henkilötietoja.\nLähetetäänkö sanomat Kannan testipalvelimelle?", icon='warning')
    return choice


 root_asiakirja = ET.fromstring(asiakirjaXml)
        print("Juuri-elementti:", root_asiakirja.tag) 
 
        namespace_remover(root_asiakirja)
        namespace_remover(root)
        siivottu_kehys = filedialog.asksaveasfilename(defaultextension=".xml", initialfile=f'{kehys_file[:-4]}_anonymisoitu.xml', filetypes=[("XML files", "*.xml")])
        uusi_puu_kehys = ET.ElementTree(root)
        siivottu_asiakirja = filedialog.asksaveasfilename(defaultextension=".xml", initialfile=f'{asiakirja_file[:-4]}_anonymisoitu.xml', filetypes=[("XML files", "*.xml")])
        uusi_puu_asiakirja = ET.ElementTree(root_asiakirja)

        # etsitään json ja tehdään korvaukset
        json_elementti = root_asiakirja.find('.//{urn:hl7finland}json')
        json_teksti = json_elementti.text
        json_teksti = _b64_normalize(json_teksti)
        json_teksti = base64.b64decode(json_teksti)
        json_dict = json.loads(json_teksti)

        json_korvaaja(json_dict, None)

        siivottu_json = json.dumps(json_dict, ensure_ascii=False)
        siivottu_json = siivottu_json.encode("utf-8")
        siivottu_json = base64.b64encode(siivottu_json)
        siivottu_json = siivottu_json.decode("utf-8")
        json_elementti.text = siivottu_json

        # korvataan näyttömuoto vakiostringillä
        mahdollinen_nayttomuoto_elementti = root_asiakirja.findall('.//text')
        for text_elementti in mahdollinen_nayttomuoto_elementti:
            try:
                if text_elementti.get("mediaType") == "application/xhtml+xml":
                    text_elementti.text = "PGRpdiBjbGFzcz0ic29jLWRvY3VtZW50IiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMTk5OS94aHRtbCIvPg"
                    break
            except:
                continue

        # tehään korvaukset xml:iin ja kirjoitetaan uudet tiedostot
        korvaaja(root, [])
        korvaaja(root_asiakirja, [])

        uusi_puu_asiakirja.write(siivottu_asiakirja, encoding="unicode")
        uusi_puu_kehys.write(siivottu_kehys, encoding="unicode")

    except ET.ParseError as e:
        messagebox.showerror("Virhe", f"Palvelupyyntö koodia ei löydy: {e}")
        return

    # koodia talteen säätöä varten
    
    # lisäsäätöä nimiavaruuksien ja ääkkösten korjaamiseksi
    with open(siivottu_asiakirja, 'r') as f:
        rivit = f.readlines()

    with open(siivottu_asiakirja, "w") as f:
        f.write("<ClinicalDocument xmlns=\"urn:hl7-org:v3\" xsi:schemaLocation=\"urn:hl7-org:v3 ../../cda/CDA_Fi_sos.xsd\" xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\">\n")
        for rivi in rivit[1:]:
            if "<ns0:localSocialHeader" in rivi:
                rivi = "<ep2:localSocialHeader xmlns:ep2=\"urn:hl7finland\">"
            elif "<ns0:json" in rivi:
                rivi = rivi.replace("<ns0:json>", "<ep2:json xmlns:ep2=\"urn:hl7finland\">")
                rivi = rivi.replace("</ns0:json>", "</ep2:json>")
            else:
                rivi = rivi.replace("<ns0:", "<ep2:")
                rivi = rivi.replace("</ns0:", "</ep2:")
            f.write(rivi)
            
     with open(siivottu_asiakirja, 'r', encoding="utf-8") as f:
        asiakirja = f.read()
        asiakirja = replace_special_characters(asiakirja)
        # print(asiakirja)
        # print("---")
        # print(asiakirjaXml)

    with open(siivottu_asiakirja, 'w', encoding="cp1252") as f:
    # with open("siivottu_asiakirja_testi.xml", 'w', encoding="cp1252") as f:
        f.write(asiakirja)


    with open(siivottu_kehys, 'r') as f:
        rivit = f.readlines()

    with open(siivottu_kehys, "w") as f:
        RCMR = rivit[0][0:18]
        f.write(f"{RCMR} xmlns=\"urn:hl7-org:v3\" ITSVersion=\"XML_1.0\">\n")
        # f.write("<RCMR_IN200002FI01 xmlns=\"urn:hl7-org:v3\" ITSVersion=\"XML_1.0\">\n")
        for rivi in rivit[1:]:
            f.write(rivi)

    with open(siivottu_kehys, 'r', encoding="utf-8") as f:
        kehys = f.read()
        kehys = replace_special_characters(kehys)

    with open(siivottu_kehys, "w", encoding="cp1252") as f:
        f.write(kehys)


    # POST-pyynnöllä lähetettävät tiedot
    data = {
        "messageId": "1234567890",
        "palveluPyynto": reason_code,
        "level": "1",
        "siirtokehysXml": kehys,
        # "siirtokehysXml": siirtokehysXml,
        "asiakirjaXml": f"<?xml version=\"1.0\" encoding=\"utf-16\"?> {asiakirja}"
        # "asiakirjaXml": f"<?xml version=\"1.0\" encoding=\"utf-16\"?> {asiakirjaXml}"
    }

    # Kysy lähetetäänkö tiedot eteenpäin.
    user_choice = confirm_sending()
    if not user_choice:
        exit()
        

