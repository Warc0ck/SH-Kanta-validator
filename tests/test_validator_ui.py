"""Exercise Streamlit reruns and verify that rejected inputs never leave the app."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import requests
from streamlit.testing.v1 import AppTest


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


def reply(status=400, content_type="application/json", body='{"description":"1: virhe;2: toinen"}'):
    return SimpleNamespace(status_code=status, headers={"Content-Type": content_type}, text=body)


class ValidationUiTests(unittest.TestCase):
    def open_text_inputs(self, frame=FRAME, document=DOCUMENT):
        app = AppTest.from_file(APP, default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        app.radio(key="kehys_tapa").set_value("📝 Teksti")
        app.radio(key="asiakirja_tapa").set_value("📝 Teksti")
        app.run()
        app.text_area(key="kehys_teksti").set_value(frame)
        app.text_area(key="asiakirja_teksti").set_value(document)
        return app

    def test_missing_inputs_do_not_send(self):
        with patch("requests.post") as post:
            app = AppTest.from_file(APP, default_timeout=10).run()
            app.button[0].click().run()
        post.assert_not_called()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)

    def test_oid_mismatch_stops_before_post(self):
        with patch("requests.post") as post:
            app = self.open_text_inputs(document=DOCUMENT.replace('root="1.2.3"', 'root="1.2.9"'))
            app.button[0].click().run()
        post.assert_not_called()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(app.error)

    def test_success_payload_has_single_xml_declaration(self):
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
        with patch("requests.post", side_effect=[reply(), reply(body='{"description":"uusi virhe"}')]) as post:
            app = self.open_text_inputs()
            app.button[0].click().run()
            app.button[0].click().run()
        self.assertEqual(post.call_count, 2)
        self.assertEqual(len(app.exception), 0)
        self.assertIn("uusi virhe", app.code[1].value)
        self.assertIn("uusi virhe", app.code[0].value)

    def test_external_html_is_displayed_as_plain_text(self):
        html = '<script>window.alert("unsafe")</script><h1>Palvelinvirhe</h1>'
        with patch("requests.post", return_value=reply(502, "TEXT/HTML; charset=utf-8", html)):
            app = self.open_text_inputs()
            app.button[0].click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.code[0].value, html)
        self.assertEqual(len(app.get("iframe")), 0)
        self.assertTrue(app.get("download_button"))

    def test_request_failure_is_retained_on_rerun(self):
        with patch("requests.post", side_effect=requests.Timeout("test timeout")) as post:
            app = self.open_text_inputs()
            app.button[0].click().run()
            app.run()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("test timeout" in entry.value for entry in app.error))


if __name__ == "__main__":
    unittest.main()
