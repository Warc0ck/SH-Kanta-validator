"""Response handling regressions without external requests or Streamlit."""

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validator_response import interpret_response  # noqa: E402


class ResponseInterpretationTests(unittest.TestCase):
    def interpret(self, status, content_type, body):
        return interpret_response(status, content_type, body, "SP1")

    def test_empty_200_is_success(self):
        result = self.interpret(200, "", "")
        self.assertTrue(result.success)
        self.assertEqual(200, result.status_code)
        self.assertTrue(result.message)

    def test_whitespace_only_200_is_success(self):
        result = self.interpret(200, "text/plain", " \n\t")
        self.assertTrue(result.success)
        self.assertEqual(" \n\t", result.response_text)

    def test_response_result_is_immutable(self):
        result = self.interpret(200, "", "")
        with self.assertRaises(FrozenInstanceError):
            result.success = False

    def test_nonempty_200_is_not_assumed_success(self):
        for body in ("Unexpected reply", "{}", '{"description":"problem"}'):
            with self.subTest(body=body):
                result = self.interpret(200, "application/json", body)
                self.assertFalse(result.success)
                self.assertTrue(result.response_text)
                self.assertTrue(result.message)

    def test_html_body_preserved_for_download_without_execution(self):
        body = '<html><script>alert("unsafe")</script><body>Virhe</body></html>'
        for content_type in ("text/html; charset=UTF-8", "TEXT/HTML", "application/xhtml+xml"):
            with self.subTest(content_type=content_type):
                result = self.interpret(502, content_type, body)
                self.assertFalse(result.success)
                self.assertEqual(body, result.response_text)
                self.assertEqual("text/html", result.response_mime)
                self.assertTrue(result.response_filename_suffix.endswith(".html"))
                self.assertTrue(result.response_label)
                self.assertFalse(result.formatted_description)

    def test_request_rejected_html_reports_support_id_for_any_status(self):
        body = (
            "<html><head><title>Request Rejected</title></head><body>"
            "The requested URL was rejected. Please consult with your administrator."
            "<br><br>Your support ID is: 17521398180174133305<br><br>"
            "<a href='javascript:history.back();'>[Go Back]</a></body></html>"
        )
        for status in (200, 403, 503):
            with self.subTest(status=status):
                result = self.interpret(status, "text/html; charset=UTF-8", body)
                self.assertFalse(result.success)
                self.assertEqual(
                    f"Palvelu palautti pyynnön estävän Request Rejected -sivun "
                    f"(HTTP {status}). Tukitunniste: 17521398180174133305.",
                    result.message,
                )
                self.assertEqual(body, result.response_text)
                self.assertEqual("text/html", result.response_mime)
                self.assertEqual("html_response.html", result.response_filename_suffix)
                self.assertIsNone(result.formatted_description)

    def test_request_rejected_html_without_support_id_is_preserved(self):
        body = (
            "<html><head><title>Request Rejected</title></head><body>"
            "The requested URL was rejected.</body></html>"
        )
        result = self.interpret(200, "text/html", body)
        self.assertFalse(result.success)
        self.assertEqual(
            "Palvelu palautti pyynnön estävän Request Rejected -sivun (HTTP 200).",
            result.message,
        )
        self.assertEqual(body, result.response_text)
        self.assertIsNone(result.formatted_description)

    def test_generic_html_keeps_existing_message(self):
        for body in (
            "<html><head><title>Server Error</title></head><body>Virhe</body></html>",
            "<html><head><title>Request Rejected</title></head><body>Muu virhe</body></html>",
        ):
            with self.subTest(body=body):
                result = self.interpret(502, "text/html", body)
                self.assertEqual(
                    "Virhe pyynnössä (tilakoodi 502). Palvelin palautti HTML-sisällön.",
                    result.message,
                )
                self.assertEqual(body, result.response_text)
                self.assertFalse(result.success)

    def test_null_or_non_object_json_is_preserved(self):
        values = (None, [], [1, {"description": "item"}], 42, "teksti", True)
        for value in values:
            with self.subTest(value=value):
                result = self.interpret(400, "application/json", json.dumps(value))
                self.assertFalse(result.success)
                self.assertEqual(value, json.loads(result.response_text))
                self.assertEqual("application/json", result.response_mime)
                self.assertTrue(result.response_filename_suffix.endswith(".json"))
                self.assertTrue(result.response_label)
                self.assertFalse(result.formatted_description)

    def test_missing_or_nontext_description_never_crashes(self):
        values = ({}, {"error": "virhe"}, {"description": None}, {"description": 1}, {"description": []})
        for value in values:
            with self.subTest(value=value):
                result = self.interpret(400, "application/problem+json", json.dumps(value))
                self.assertFalse(result.success)
                self.assertEqual(value, json.loads(result.response_text))
                self.assertFalse(result.formatted_description)

    def test_text_description_is_formatted_and_reason_is_included(self):
        value = {"description": "1: Ensimmäinen virhe; 2: Toinen virhe;", "code": "TEST"}
        result = self.interpret(400, "application/json", json.dumps(value))
        self.assertFalse(result.success)
        self.assertIn("Ensimmäinen virhe", result.formatted_description)
        self.assertIn("Toinen virhe", result.formatted_description)
        self.assertIn("SP1", result.formatted_description)
        self.assertIn("\n", result.formatted_description)
        self.assertEqual(value, json.loads(result.response_text))

    def test_malformed_json_and_plain_error_are_preserved(self):
        cases = (
            ("application/json", '{"description":'),
            ("text/plain", "Palvelin ei vastannut oikein. ääkköset"),
            ("", ""),
        )
        for content_type, body in cases:
            with self.subTest(content_type=content_type, body=body):
                result = self.interpret(500, content_type, body)
                self.assertFalse(result.success)
                self.assertEqual(body, result.response_text)
                self.assertEqual("text/plain", result.response_mime)
                self.assertTrue(result.response_filename_suffix.endswith(".txt"))
                self.assertTrue(result.response_label)


if __name__ == "__main__":
    unittest.main()
