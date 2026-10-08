"""Käyttöliittymän uudelleensuoritusten ja paikallisten lähetysestojen testit."""

from pathlib import Path
from types import SimpleNamespace
import base64
import json
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import requests
from streamlit.testing.v1 import AppTest

from validator_core import HETU_CHECK_CHARS


APP = Path(__file__).resolve().parents[1] / "SHKanta-www-validator.py"
FRAME = """<RCMR_IN200002FI01 xmlns="urn:hl7-org:v3">
  <controlActProcess><reasonCode code="SP1"/>
    <subject><clinicalDocument><id root="1.2.3"/><setId root="1.2.4"/>
    </clinicalDocument></subject>
  </controlActProcess>
</RCMR_IN200002FI01>"""
DOCUMENT = """<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument classCode="DOCCLIN" xmlns="urn:hl7-org:v3">
  <id root="1.2.3"/><setId root="1.2.4"/>
</ClinicalDocument>"""
PERSONAL_HETU = "010100A002" + HETU_CHECK_CHARS[int("010100002") % 31]
PERSONAL_DOCUMENT = DOCUMENT.replace(
    "</ClinicalDocument>",
    f'<recordTarget><patientRole><id extension="{PERSONAL_HETU}"/>'
    '<patient><name><given>Mallinimi</given><family>Esimerkki</family></name>'
    '<birthTime value="20000101"/></patient></patientRole></recordTarget>'
    '</ClinicalDocument>',
)
JSON_CONTENTS = (
    {
        "etunimet": ["Mallinimi"],
        "sukunimi": "Esimerkki",
        "muu": "Ääkköstesti & <asiakirja>",
        "rivit": [{"arvo": "Säilyvä sisältö"}],
    },
    {"kuvaus": "Toinen jäljelle jäävä sisältö"},
)
REPLACED_JSON_CONTENTS = (
    {
        **JSON_CONTENTS[0],
        "etunimet": ["feikkinimi"],
        "sukunimi": "feikkinimi",
    },
    JSON_CONTENTS[1],
)


def json_document(*contents):
    """Liittää synteettiset JSON-sisällöt asiakirjaan Base64-koodattuina elementteinä."""
    elements = []
    for value in contents:
        encoded = base64.b64encode(
            json.dumps(value, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        elements.append(
            '<f:json xmlns:f="urn:hl7finland" representation="B64">'
            f'{encoded}</f:json>'
        )
    return PERSONAL_DOCUMENT.replace(
        "</ClinicalDocument>", "".join(elements) + "</ClinicalDocument>"
    )


def reply(status=400, content_type="application/json", body='{"description":"1: virhe;2: toinen"}'):
    """Muodostaa HTTP-vastauksen korvikkeen käyttöliittymätesteille."""
    return SimpleNamespace(status_code=status, headers={"Content-Type": content_type}, text=body)


class ValidationUiTests(unittest.TestCase):
    def open_text_inputs(self, frame=FRAME, document=DOCUMENT):
        """Avaa käyttöliittymän ja täyttää molemmat XML-syötteet tekstikenttiin."""
        app = AppTest.from_file(APP, default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        app.radio(key="kehys_tapa").set_value("📝 Teksti")
        app.radio(key="asiakirja_tapa").set_value("📝 Teksti")
        app.run()
        app.text_area(key="kehys_teksti").set_value(frame)
        app.text_area(key="asiakirja_teksti").set_value(document)
        return app

    def test_missing_inputs_do_not_send(self):
        """Varmistaa, ettei puuttuvilla syötteillä lähetetä HTTP-pyyntöä."""
        with patch("requests.post") as post:
            app = AppTest.from_file(APP, default_timeout=10).run()
            app.button[0].click().run()
        post.assert_not_called()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)

    def test_oid_mismatch_stops_before_post(self):
        """Varmistaa, että OID-ristiriita estää käyttöliittymän HTTP-lähetyksen."""
        with patch("requests.post") as post:
            app = self.open_text_inputs(document=DOCUMENT.replace('root="1.2.3"', 'root="1.2.9"'))
            app.button[0].click().run()
        post.assert_not_called()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)

    def test_success_payload_has_single_xml_declaration(self):
        """Varmistaa onnistuneen pyynnön yhden XML-alkumäärittelyn ja onnistumisviestin."""
        with patch("requests.post", return_value=reply(200, "", "")) as post:
            app = self.open_text_inputs()
            app.button[0].click().run()
            app.run()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(app.exception), 0)
        sent = post.call_args.kwargs["json"]["asiakirjaXml"]
        self.assertEqual(ET.fromstring(sent).tag, "{urn:hl7-org:v3}ClinicalDocument")
        self.assertLessEqual(sent.count("<?xml"), 1)
        self.assertTrue(any("validoitu onnistuneesti" in entry.value for entry in app.success))

    def test_reports_persist_and_changed_input_marks_result_stale(self):
        """Varmistaa raporttien säilymisen ja muuttuneen syötteen vanhentumismerkinnän."""
        with patch("requests.post", return_value=reply()) as post:
            app = self.open_text_inputs()
            app.button[0].click().run()
            first = app.code[1].value
            app.run()
            self.assertEqual(app.code[1].value, first)
            self.assertTrue(all(not report.proto.wrap_lines for report in app.code))
            app.checkbox(key="rivitys").check().run()
            self.assertEqual(app.code[1].value, first)
            self.assertTrue(all(report.proto.wrap_lines for report in app.code))
            app.checkbox(key="rivitys").uncheck().run()
            self.assertTrue(all(not report.proto.wrap_lines for report in app.code))
            app.text_area(key="asiakirja_teksti").set_value(DOCUMENT + "\n").run()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.code[1].value, first)
        self.assertTrue(any("Syötteet ovat muuttuneet" in entry.value for entry in app.warning))
        self.assertTrue(app.get("download_button"))

    def test_second_validation_replaces_report_values(self):
        """Varmistaa, että uusi validointi korvaa aiemman raportin."""
        with patch("requests.post", side_effect=[reply(), reply(body='{"description":"uusi virhe"}')]) as post:
            app = self.open_text_inputs()
            app.button[0].click().run()
            app.button[0].click().run()
        self.assertEqual(post.call_count, 2)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("uusi virhe", app.code[1].value)
        self.assertIn("uusi virhe", app.code[0].value)

    def test_external_html_is_displayed_as_plain_text(self):
        """Varmistaa ulkoisen HTML-vastauksen näyttämisen pelkkänä tekstinä."""
        html = '<script>window.alert("unsafe")</script><h1>Palvelinvirhe</h1>'
        with patch("requests.post", return_value=reply(502, "TEXT/HTML; charset=utf-8", html)):
            app = self.open_text_inputs()
            app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.code[0].value, html)
        self.assertEqual(len(app.get("iframe")), 0)
        self.assertTrue(app.get("download_button"))

    def test_request_failure_is_retained_on_rerun(self):
        """Varmistaa lähetysvirheen säilymisen käyttöliittymän uudelleensuorituksessa."""
        with patch("requests.post", side_effect=requests.Timeout("test timeout")) as post:
            app = self.open_text_inputs()
            app.button[0].click().run()
            app.run()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("test timeout" in entry.value for entry in app.error))

    def test_replacement_is_local_and_requires_confirmation(self):
        """Varmistaa, että korvaus ja latausten muodostus eivät lähetä verkkopyyntöä."""
        with patch("requests.post") as post:
            app = self.open_text_inputs(document=PERSONAL_DOCUMENT)
            app.button(key="replace_personal_data").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.button(key="validate_replaced").disabled)
            self.assertFalse(app.checkbox(key="replacement_confirmed").value)
            self.assertEqual(len(app.get("download_button")), 2)
            prepared = app.session_state["replaced_inputs"]
            self.assertNotIn(PERSONAL_HETU, prepared["document_xml"])
            self.assertIn("010181-900C", prepared["document_xml"])
            self.assertEqual(PERSONAL_DOCUMENT, app.text_area(key="asiakirja_teksti").value)
            app.checkbox(key="rivitys").check().run()
            self.assertTrue(all(entry.proto.wrap_lines for entry in app.code))
            self.assertEqual(prepared["document_xml"], app.session_state["replaced_inputs"]["document_xml"])
        post.assert_not_called()

    def test_confirmed_replacement_sends_only_replaced_inputs(self):
        """Tarkistaa, että vahvistettu validointi lähettää korvatut XML:t vain kerran."""
        with patch("requests.post", return_value=reply(200, "", "")) as post:
            app = self.open_text_inputs(document=PERSONAL_DOCUMENT)
            app.button(key="replace_personal_data").click().run()
            app.checkbox(key="replacement_confirmed").check().run()
            post.assert_not_called()
            self.assertFalse(app.button(key="validate_replaced").disabled)
            app.button(key="validate_replaced").click().run()
            app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(post.call_count, 1)
        payload = post.call_args.kwargs["json"]
        self.assertNotIn(PERSONAL_HETU, payload["asiakirjaXml"])
        self.assertIn("010181-900C", payload["asiakirjaXml"])
        self.assertIn("feikkinimi", payload["asiakirjaXml"])
        self.assertEqual("SP1", payload["palveluPyynto"])
        self.assertEqual(PERSONAL_DOCUMENT, app.text_area(key="asiakirja_teksti").value)
        self.assertTrue(app.session_state["validation_result"]["replaced"])
        self.assertTrue(any("korvatuilla sanomilla" in entry.value for entry in app.caption))

    def test_replaced_json_is_readable_in_document_preview(self):
        """Näyttää jokaisen puretun JSON-sisällön sisennettynä asiakirjan esikatselussa."""
        source = json_document(*JSON_CONTENTS)
        with patch("requests.post") as post:
            app = self.open_text_inputs(document=source)
            app.button(key="replace_personal_data").click().run()
        post.assert_not_called()
        self.assertEqual(len(app.exception), 0)
        inspection_xml = app.session_state["replaced_inputs"]["document_xml"]
        self.assertTrue(any(entry.value == inspection_xml for entry in app.code))
        elements = ET.fromstring(inspection_xml).findall(".//{urn:hl7finland}json")
        self.assertEqual(len(elements), len(JSON_CONTENTS))
        for element, expected in zip(elements, REPLACED_JSON_CONTENTS):
            self.assertNotEqual("B64", element.get("representation"))
            self.assertEqual(json.loads(element.text), expected)
            self.assertIn("\n  ", element.text)
        self.assertIn("Ääkköstesti & <asiakirja>", inspection_xml)
        self.assertEqual(source, app.text_area(key="asiakirja_teksti").value)
        self.assertTrue(app.button(key="validate_replaced").disabled)

    def test_confirmed_inspection_document_encodes_all_json_before_sending(self):
        """Koodaa tarkistetun asiakirjan kaikki JSONit lähetykseen ja säilyttää niiden sisällöt."""
        with patch("requests.post", return_value=reply(200, "", "")) as post:
            app = self.open_text_inputs(document=json_document(*JSON_CONTENTS))
            app.button(key="replace_personal_data").click().run()
            inspection_xml = app.session_state["replaced_inputs"]["document_xml"]
            app.checkbox(key="replacement_confirmed").check().run()
            post.assert_not_called()
            app.button(key="validate_replaced").click().run()
        self.assertEqual(len(app.exception), 0)
        post.assert_called_once()
        sent = ET.fromstring(post.call_args.kwargs["json"]["asiakirjaXml"])
        elements = sent.findall(".//{urn:hl7finland}json")
        self.assertEqual(len(elements), len(REPLACED_JSON_CONTENTS))
        for element, expected in zip(elements, REPLACED_JSON_CONTENTS):
            decoded = base64.b64decode(element.text, validate=True).decode("utf-8")
            self.assertEqual(json.loads(decoded), expected)
        self.assertEqual(sent.find("{urn:hl7-org:v3}id").get("root"), "1.2.3")
        self.assertEqual(sent.find("{urn:hl7-org:v3}setId").get("root"), "1.2.4")
        self.assertEqual(inspection_xml, app.session_state["replaced_inputs"]["document_xml"])
        self.assertTrue(any(entry.value == inspection_xml for entry in app.code))

    def test_inspection_document_can_be_supplied_to_original_validation(self):
        """Sallii tarkistus-XML:n uudelleensyötön ja koodaa sen JSONit myös tavallisessa validoinnissa."""
        with patch("requests.post", return_value=reply(200, "", "")) as post:
            app = self.open_text_inputs(document=json_document(*JSON_CONTENTS))
            app.button(key="replace_personal_data").click().run()
            inspection_xml = app.session_state["replaced_inputs"]["document_xml"]
            app.text_area(key="asiakirja_teksti").set_value(inspection_xml).run()
            app.button(key="validate_original").click().run()
        self.assertEqual(len(app.exception), 0)
        post.assert_called_once()
        sent = ET.fromstring(post.call_args.kwargs["json"]["asiakirjaXml"])
        elements = sent.findall(".//{urn:hl7finland}json")
        self.assertEqual(len(elements), len(REPLACED_JSON_CONTENTS))
        for element, expected in zip(elements, REPLACED_JSON_CONTENTS):
            decoded = base64.b64decode(element.text, validate=True).decode("utf-8")
            self.assertEqual(json.loads(decoded), expected)
        self.assertFalse(app.session_state["validation_result"]["replaced"])
        self.assertEqual(inspection_xml, app.text_area(key="asiakirja_teksti").value)

    def test_unknown_personal_hetu_in_json_stops_confirmed_send(self):
        """Pysäyttää lähetyksen, jos tarkistetun JSONin tuntemattomaan kenttään jää henkilötunnus."""
        with patch("requests.post") as post:
            app = self.open_text_inputs(document=json_document({"muu": {"merkinta": PERSONAL_HETU}}))
            app.button(key="replace_personal_data").click().run()
            self.assertIn(PERSONAL_HETU, app.session_state["replaced_inputs"]["document_xml"])
            app.checkbox(key="replacement_confirmed").check().run()
            app.button(key="validate_replaced").click().run()
        self.assertEqual(len(app.exception), 0)
        post.assert_not_called()
        self.assertTrue(any("testihenkilötunnuksia" in entry.value for entry in app.error))
        self.assertIsNone(app.session_state["validation_result"]["response"])

    def test_version_history_opens_without_changing_validation_state(self):
        """Avaa version 3.3 muutoshistorian ja säilyttää kopiot, vahvistuksen sekä validointituloksen."""
        with patch("requests.post", return_value=reply()) as post:
            app = self.open_text_inputs(document=json_document(*JSON_CONTENTS))
            app.button(key="replace_personal_data").click().run()
            app.checkbox(key="replacement_confirmed").check().run()
            app.button(key="validate_replaced").click().run()
            saved_inputs = app.session_state["replaced_inputs"]
            saved_result = app.session_state["validation_result"]
            previous_report = [entry.value for entry in app.code]
            app.button(key="version_history").click().run()
        self.assertEqual(len(app.exception), 0)
        post.assert_called_once()
        self.assertTrue(any("3.3" in entry.value for entry in app.markdown))
        self.assertEqual(["Versio 3.3", "Versio 3.2", "Versio 3.0"], [entry.label for entry in app.expander])
        self.assertTrue(app.expander[0].proto.expanded)
        self.assertEqual(saved_inputs, app.session_state["replaced_inputs"])
        self.assertEqual(saved_result, app.session_state["validation_result"])
        self.assertTrue(app.checkbox(key="replacement_confirmed").value)
        self.assertFalse(app.button(key="validate_replaced").disabled)
        self.assertEqual(previous_report, [entry.value for entry in app.code])

    def test_changed_inputs_invalidate_replacements_and_confirmation(self):
        """Estää vanhan korvauskopion lähettämisen alkuperäisten syötteiden muuttuessa."""
        with patch("requests.post") as post:
            app = self.open_text_inputs(document=PERSONAL_DOCUMENT)
            app.button(key="replace_personal_data").click().run()
            app.checkbox(key="replacement_confirmed").check().run()
            app.text_area(key="asiakirja_teksti").set_value(PERSONAL_DOCUMENT + "\n").run()
            self.assertEqual(len(app.exception), 0)
            self.assertNotIn("replaced_inputs", app.session_state)
            self.assertFalse(app.get("download_button"))
            self.assertFalse(any(entry.key == "validate_replaced" for entry in app.button))
            app.button(key="replace_personal_data").click().run()
            self.assertFalse(app.checkbox(key="replacement_confirmed").value)
            self.assertTrue(app.button(key="validate_replaced").disabled)
        post.assert_not_called()

    def test_unreplaced_hetu_blocks_confirmed_replacement_before_post(self):
        """Estää vahvistetun lähetyksen, kun henkilötunnus jää tuntemattomaan XML-kenttään."""
        document = DOCUMENT.replace(
            "</ClinicalDocument>",
            f"<huomautus>{PERSONAL_HETU}</huomautus></ClinicalDocument>",
        )
        with patch("requests.post") as post:
            app = self.open_text_inputs(document=document)
            app.button(key="replace_personal_data").click().run()
            prepared = app.session_state["replaced_inputs"]
            self.assertIn(PERSONAL_HETU, prepared["document_xml"])
            app.checkbox(key="replacement_confirmed").check().run()
            self.assertFalse(app.button(key="validate_replaced").disabled)
            app.button(key="validate_replaced").click().run()
        post.assert_not_called()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("testihenkilötunnuksia" in entry.value for entry in app.error))
        saved = app.session_state["validation_result"]
        self.assertTrue(saved["replaced"])
        self.assertIn(PERSONAL_HETU, saved["error"])
        self.assertIsNone(saved["response"])

    def test_recreating_replacements_resets_confirmation(self):
        """Vaatii uuden tarkistusvahvistuksen myös samojen syötteiden uudessa korvauksessa."""
        with patch("requests.post") as post:
            app = self.open_text_inputs(document=PERSONAL_DOCUMENT)
            app.button(key="replace_personal_data").click().run()
            app.checkbox(key="replacement_confirmed").check().run()
            app.button(key="replace_personal_data").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertFalse(app.checkbox(key="replacement_confirmed").value)
        self.assertTrue(app.button(key="validate_replaced").disabled)
        post.assert_not_called()

    def test_replacement_errors_are_local_and_do_not_leave_old_copies(self):
        """Näyttää virheellisen XML:n korvausvirheen eikä säilytä aiempaa lähetyskopiota."""
        with patch("requests.post") as post:
            app = self.open_text_inputs(document=PERSONAL_DOCUMENT)
            app.button(key="replace_personal_data").click().run()
            app.text_area(key="asiakirja_teksti").set_value("<ClinicalDocument>").run()
            app.button(key="replace_personal_data").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("korvaus pysäytetty" in entry.value for entry in app.error))
        self.assertNotIn("replaced_inputs", app.session_state)
        self.assertFalse(app.get("download_button"))
        post.assert_not_called()

    def test_missing_replacement_inputs_do_not_send(self):
        """Pysäyttää korvauksen, kun siirtokehys tai asiakirja puuttuu."""
        with patch("requests.post") as post:
            app = AppTest.from_file(APP, default_timeout=10).run()
            app.button(key="replace_personal_data").click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("Syötä molemmat XML-sanomat" in entry.value for entry in app.error))
        self.assertFalse(app.get("download_button"))
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
