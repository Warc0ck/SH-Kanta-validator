"""Local regressions for request preparation; all identifiers are synthetic."""

import base64
import html
from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validator_core import (  # noqa: E402
    HETU_CHECK_CHARS,
    HL7_NAMESPACE,
    _to_text_helper,
    ensure_clinical_document_xml,
    extract_base64_contents,
    extract_clinical_doc_identifiers,
    find_non_test_hetus,
    is_valid_finnish_hetu,
    prepare_validation_request,
)
import validator_core  # noqa: E402


def synthetic_hetu(date="010100", marker="A", individual=2):
    """Calculate a fixture instead of copying any person's identifier."""
    number = f"{individual:03d}"
    return f"{date}{marker}{number}{HETU_CHECK_CHARS[int(date + number) % 31]}"


def synthetic_hetu_with_check(character):
    for individual in range(2, 900):
        candidate = synthetic_hetu(individual=individual)
        if candidate[-1] == character:
            return candidate
    raise AssertionError(f"No synthetic fixture for check character {character}")


def b64(text, encoding="utf-8"):
    if isinstance(text, str):
        text = text.encode(encoding)
    return base64.b64encode(text).decode("ascii")


def document(id_root="1.2.3.4", set_root="1.2.3.5", body=""):
    return (
        f'<ClinicalDocument xmlns="{HL7_NAMESPACE}">'
        f'<id root="{id_root}"/><setId root="{set_root}"/>'
        f"{body}</ClinicalDocument>"
    )


def frame(id_root="1.2.3.4", set_root="1.2.3.5", reason='code="SP1"', body=""):
    return (
        f'<MCCI_IN000001UV01 xmlns="{HL7_NAMESPACE}">'
        f"<controlActProcess><reasonCode {reason}/>"
        f'<subject><clinicalDocument><id root="{id_root}"/>'
        f'<setId root="{set_root}"/></clinicalDocument></subject>'
        f"{body}</controlActProcess></MCCI_IN000001UV01>"
    )


def resolved_xsi_types(source):
    """Read QName attribute meanings, including nested namespace scopes."""
    pending = []
    scopes = []
    scope = {}
    meanings = []
    type_attribute = "{http://www.w3.org/2001/XMLSchema-instance}type"
    for event, item in ET.iterparse(StringIO(source), events=("start-ns", "start", "end")):
        if event == "start-ns":
            pending.append(item)
        elif event == "start":
            scopes.append(scope)
            scope = dict(scope)
            scope.update(pending)
            pending.clear()
            value = item.get(type_attribute)
            if value:
                prefix, local_name = value.split(":", 1)
                meanings.append((scope.get(prefix), local_name))
        else:
            scope = scopes.pop()
    return meanings


class HetuValidationTests(unittest.TestCase):
    def test_u_and_v_check_characters_are_valid_and_detected(self):
        for check in "UV":
            with self.subTest(check=check):
                hetu = synthetic_hetu_with_check(check)
                self.assertTrue(is_valid_finnish_hetu(hetu))
                self.assertEqual({hetu}, set(find_non_test_hetus(hetu)))

    def test_lowercase_markers_and_check_characters_are_detected(self):
        hetu = synthetic_hetu_with_check("V")
        self.assertTrue(is_valid_finnish_hetu(hetu.lower()))
        self.assertEqual({hetu}, {value.upper() for value in find_non_test_hetus(hetu.lower())})

    def test_calendar_and_century_are_checked(self):
        cases = (
            ("290200", "A", True),  # 2000 is a leap year.
            ("290200", "-", False),  # 1900 is not a leap year.
            ("290200", "+", False),  # 1800 is not a leap year.
            ("290204", "Y", True),
            ("310200", "A", False),
            ("310400", "A", False),
            ("000100", "A", False),
            ("011300", "A", False),
        )
        for date, marker, valid in cases:
            with self.subTest(date=date, marker=marker):
                self.assertEqual(valid, is_valid_finnish_hetu(synthetic_hetu(date, marker)))

    def test_current_century_markers(self):
        for marker in "ABCDEF-YXWVU+":
            with self.subTest(marker=marker):
                self.assertTrue(is_valid_finnish_hetu(synthetic_hetu(marker=marker)))

    def test_incorrect_check_and_reserved_individual_numbers_rejected(self):
        hetu = synthetic_hetu()
        bad_check = "0" if hetu[-1] != "0" else "1"
        self.assertFalse(is_valid_finnish_hetu(hetu[:-1] + bad_check))
        for individual in (0, 1):
            self.assertFalse(is_valid_finnish_hetu(synthetic_hetu(individual=individual)))

    def test_9xx_test_identifiers_are_allowed(self):
        for individual in (900, 999):
            with self.subTest(individual=individual):
                hetu = synthetic_hetu(individual=individual)
                self.assertTrue(is_valid_finnish_hetu(hetu))
                self.assertEqual([], find_non_test_hetus(document(body=f"<text>{hetu}</text>")))
                prepare_validation_request(frame(), document(body=f"<text>{hetu}</text>"))


class StructuredHetuDetectionTests(unittest.TestCase):
    def setUp(self):
        self.hetu = synthetic_hetu_with_check("V")

    def assert_detected(self, source):
        self.assertIn(self.hetu, {value.upper() for value in find_non_test_hetus(source)})

    def test_xml_character_references_in_attributes_and_text(self):
        encoded = self.hetu.replace("A", "&#65;", 1)
        for source in (
            f'<root value="{encoded}"/>',
            f"<root><text>{encoded}</text></root>",
        ):
            with self.subTest(source=source):
                self.assert_detected(source)

    def test_leading_xml_comments_and_processing_instructions_do_not_hide_entities(self):
        encoded = self.hetu.replace("A", "&#65;", 1)
        doc = document(body=f'<patient identifier="{encoded}"/>')
        for prefix in ('<!--validator test comment-->\n', '<?xml-stylesheet href="test.css"?>\n'):
            with self.subTest(prefix=prefix):
                source = prefix + doc
                self.assert_detected(source)
                with self.assertRaises(ValueError):
                    prepare_validation_request(frame(), source)

    def test_json_unicode_escapes(self):
        encoded = self.hetu.replace("A", r"\u0041", 1)
        self.assert_detected('{"identifier":"' + encoded + '"}')

    def test_json_scalar_string_unicode_escapes(self):
        encoded = self.hetu.replace("A", r"\u0041", 1)
        self.assert_detected('"' + encoded + '"')
        self.assert_detected('<root><text>"' + encoded + '"</text></root>')

    def test_short_base64_identifier_in_json(self):
        encoded = b64(self.hetu)
        self.assertEqual(16, len(encoded))
        self.assert_detected(json.dumps({"data": encoded}))
        self.assertIn(self.hetu, extract_base64_contents(json.dumps({"data": encoded})))

    def test_known_xml_base64_fields_and_cdata(self):
        encoded = b64(self.hetu)
        sources = (
            f'<root><text representation="B64">{encoded}</text></root>',
            f'<root><nonXMLBody><text representation="B64">{encoded}</text></nonXMLBody></root>',
            f'<root><value representation="B64">{encoded}</value></root>',
            f'<root><text representation="B64"><![CDATA[{encoded}]]></text></root>',
        )
        for source in sources:
            with self.subTest(source=source):
                self.assert_detected(source)

    def test_utf16_base64_identifier(self):
        for encoding in ("utf-16", "utf-16le", "utf-16be"):
            with self.subTest(encoding=encoding):
                self.assert_detected(json.dumps({"data": b64(self.hetu, encoding)}))

    def test_nested_base64_json_and_cdata(self):
        wrapped = json.dumps({"data": b64(self.hetu)})
        self.assert_detected(f'<root><text representation="B64">{b64(wrapped)}</text></root>')
        self.assert_detected(f"<root><![CDATA[{wrapped}]]></root>")
        self.assert_detected(f"<root>&lt;![CDATA[{wrapped}]]&gt;</root>")

    def test_uninspectable_explicit_binary_base64_blocks_request(self):
        binary = b64(b"\x00\xff\x89\x00")
        doc = document(body=f'<text representation="B64">{binary}</text>')
        with self.assertRaises(ValueError):
            prepare_validation_request(frame(), doc)

    def test_binary_media_type_inherited_from_nonxmlbody_blocks_request(self):
        encoded = b64("benign ASCII attachment")
        body = (
            '<nonXMLBody mediaType="application/pdf">'
            f'<text representation="B64">{encoded}</text></nonXMLBody>'
        )
        with self.assertRaises(ValueError):
            prepare_validation_request(frame(), document(body=body))

    def test_malformed_explicit_base64_is_not_silently_cleaned(self):
        encoded = "%" + b64(self.hetu)
        doc = document(body=f'<text representation="B64">{encoded}</text>')
        with self.assertRaises(ValueError):
            prepare_validation_request(frame(), doc)

    def test_depth_limit_never_silently_hides_identifier(self):
        wrapped = self.hetu
        for _ in range(8):
            wrapped = json.dumps({"data": b64(wrapped)})
        try:
            found = find_non_test_hetus(wrapped)
        except ValueError:
            return  # Refusing an uninspectable payload is a safe outcome.
        self.assertIn(self.hetu, {value.upper() for value in found})

    def test_attachment_count_limit_blocks_incomplete_inspection(self):
        source = json.dumps({
            "first": {"encoding": "B64", "data": b64("benign attachment one")},
            "second": {"encoding": "B64", "data": b64(self.hetu)},
        })
        with patch.object(validator_core, "MAX_BASE64_BLOCKS", 1):
            with self.assertRaises(ValueError):
                find_non_test_hetus(source)

    def test_expanded_text_and_attachment_size_limits_block_inspection(self):
        source = "<root><text>benign text content</text></root>"
        with patch.object(validator_core, "MAX_EXPANDED_CHARS", len(source) + 1):
            with self.assertRaises(ValueError):
                find_non_test_hetus(source)
        source = f'<root><text representation="B64">{b64(self.hetu)}</text></root>'
        with patch.object(validator_core, "MAX_DECODED_BYTES", 4):
            with self.assertRaises(ValueError):
                find_non_test_hetus(source)


class ClinicalDocumentTests(unittest.TestCase):
    def test_attribute_order_and_newlines_do_not_change_recognition(self):
        source = (
            '<ClinicalDocument classCode="DOCCLIN"\n'
            f' xmlns="{HL7_NAMESPACE}"><id root="1.2.3.4"/>'
            '<setId root="1.2.3.5"/></ClinicalDocument>'
        )
        parsed = ET.fromstring(ensure_clinical_document_xml(source, verbose=False))
        self.assertEqual(f"{{{HL7_NAMESPACE}}}ClinicalDocument", parsed.tag)

    def test_namespace_inherited_from_wrapper(self):
        source = (
            f'<wrapper xmlns="{HL7_NAMESPACE}"><ClinicalDocument>'
            '<id root="1.2.3.4"/><setId root="1.2.3.5"/>'
            "</ClinicalDocument></wrapper>"
        )
        parsed = ET.fromstring(ensure_clinical_document_xml(source, verbose=False))
        self.assertEqual(f"{{{HL7_NAMESPACE}}}ClinicalDocument", parsed.tag)
        self.assertEqual(("1.2.3.4", "1.2.3.5"), extract_clinical_doc_identifiers(source))

    def test_wrapper_tail_text_is_excluded_from_standalone_document(self):
        source = (
            f'<wrapper xmlns="{HL7_NAMESPACE}"><ClinicalDocument>'
            '<id root="1.2.3.4"/><setId root="1.2.3.5"/>'
            "</ClinicalDocument>wrapper trailing text</wrapper>"
        )
        result = ensure_clinical_document_xml(source, verbose=False)
        parsed = ET.fromstring(result)
        self.assertEqual(f"{{{HL7_NAMESPACE}}}ClinicalDocument", parsed.tag)
        self.assertNotIn("wrapper trailing text", result)

    def test_incidental_xml_like_trace_text_does_not_block_valid_base64_document(self):
        original = document(body="<text>ääkköset</text>")
        source = (
            "<trace><message>&lt;not-an-xml</message>"
            f'<text representation="B64">{b64(original)}</text></trace>'
        )
        result = ensure_clinical_document_xml(source, verbose=False)
        parsed = ET.fromstring(result)
        self.assertEqual(f"{{{HL7_NAMESPACE}}}ClinicalDocument", parsed.tag)
        self.assertEqual("ääkköset", parsed.find(f"{{{HL7_NAMESPACE}}}text").text)

    def test_embedded_document_preserves_unqualified_children_and_rebound_qnames(self):
        source = (
            f'<trace xmlns="{HL7_NAMESPACE}" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:type="urn:outer-type"><ClinicalDocument>'
            '<id root="1.2.3.4"/><setId root="1.2.3.5"/>'
            '<free xmlns=""><x/></free>'
            '<value xsi:type="type:Outer"/>'
            '<section xmlns:type="urn:inner-type"><value xsi:type="type:Inner"/></section>'
            '<value xsi:type="type:After"/>'
            '</ClinicalDocument></trace>'
        )
        result = ensure_clinical_document_xml(source, verbose=False)
        parsed = ET.fromstring(result)
        free = parsed.find("free")
        self.assertIsNotNone(free)
        self.assertIsNotNone(free.find("x"))
        self.assertIsNone(parsed.find(f"{{{HL7_NAMESPACE}}}free"))
        expected_types = [
            ("urn:outer-type", "Outer"),
            ("urn:inner-type", "Inner"),
            ("urn:outer-type", "After"),
        ]
        self.assertEqual(expected_types, resolved_xsi_types(source))
        self.assertEqual(expected_types, resolved_xsi_types(result))

    def test_self_closing_embedded_document_is_standalone_and_parseable(self):
        source = f'<trace xmlns="{HL7_NAMESPACE}"><ClinicalDocument/>wrapper tail</trace>'
        result = ensure_clinical_document_xml(source, verbose=False)
        parsed = ET.fromstring(result)
        self.assertEqual(f"{{{HL7_NAMESPACE}}}ClinicalDocument", parsed.tag)
        self.assertNotIn("wrapper tail", result)

    def test_malformed_wrong_namespace_and_false_names_are_rejected(self):
        sources = (
            f'<ClinicalDocument xmlns="{HL7_NAMESPACE}">',
            f'<!-- <ClinicalDocument xmlns="{HL7_NAMESPACE}"/> -->',
            '<ClinicalDocument xmlns="urn:wrong"/>',
            f'<NotClinicalDocument xmlns="{HL7_NAMESPACE}"/>',
            "<ClinicalDocument/>",
            f"<wrapper>{document()}{document()}</wrapper>",
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    ensure_clinical_document_xml(source, verbose=False)

    def test_document_base64_and_actual_or_escaped_cdata(self):
        original = document(body="<text>ääkköset</text>")
        sources = (
            b64(original),
            html.escape(original),
            f"<![CDATA[{original}]]>",
            f"&lt;![CDATA[{original}]]&gt;",
            f"&amp;lt;![CDATA[{original}]]&amp;gt;",
            f"<![CDATA[{b64(original)}]]>",
        )
        for source in sources:
            with self.subTest(source=source):
                parsed = ET.fromstring(ensure_clinical_document_xml(source, verbose=False))
                self.assertEqual("ääkköset", parsed.find(f"{{{HL7_NAMESPACE}}}text").text)

    def test_base64_with_arbitrary_garbage_is_rejected(self):
        with self.assertRaises(ValueError):
            ensure_clinical_document_xml("!" + b64(document()) + "!", verbose=False)

    def test_existing_base64_input_variants_remain_supported(self):
        original = document(body="<text>ääkköset 🙂</text>")
        encoded = b64(original)
        wrapped = "\n".join(encoded[index:index + 64] for index in range(0, len(encoded), 64))
        sources = (
            encoded.rstrip("="),
            base64.urlsafe_b64encode(original.encode("utf-8")).decode("ascii").rstrip("="),
            wrapped,
            wrapped.replace("\n", r"\n"),
        )
        for source in sources:
            with self.subTest(source=source):
                parsed = ET.fromstring(ensure_clinical_document_xml(source, verbose=False))
                self.assertEqual("ääkköset 🙂", parsed.find(f"{{{HL7_NAMESPACE}}}text").text)

    def test_declared_iso8859_and_bomless_utf16(self):
        original = document(body="<text>ääkköset</text>")
        iso_doc = ('<?xml version="1.0" encoding="ISO-8859-1"?>' + original).encode("iso-8859-1")
        self.assertIn("ääkköset", _to_text_helper(iso_doc))
        self.assertIn("ääkköset", ensure_clinical_document_xml(iso_doc, verbose=False))
        for encoding in ("utf-16le", "utf-16be"):
            with self.subTest(encoding=encoding):
                source = original.encode(encoding)
                self.assertNotIn("\x00", _to_text_helper(source))
                self.assertIn("ääkköset", ensure_clinical_document_xml(source, verbose=False))

    def test_invalid_or_unknown_encoding_is_rejected(self):
        sources = (
            b'<?xml version="1.0" encoding="UTF-8"?><ClinicalDocument>\xff</ClinicalDocument>',
            b'<?xml version="1.0" encoding="not-a-real-codec"?><ClinicalDocument/>',
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    ensure_clinical_document_xml(source, verbose=False)


class RequestPreparationTests(unittest.TestCase):
    def test_payload_has_unique_uuid_and_single_declaration(self):
        source = '<?xml version="1.0" encoding="UTF-8"?>' + document()
        first, checks = prepare_validation_request(frame(), source)
        second, _ = prepare_validation_request(frame(), source)
        self.assertEqual("SP1", first["palveluPyynto"])
        self.assertEqual("1", first["level"])
        self.assertEqual(str(uuid.UUID(first["messageId"])), first["messageId"])
        self.assertNotEqual(first["messageId"], second["messageId"])
        for field in ("siirtokehysXml", "asiakirjaXml"):
            self.assertEqual(1, first[field].count("<?xml"))
            ET.fromstring(first[field].encode("utf-8"))
        self.assertEqual({"1.2.3.4", "1.2.3.5"}, {value for _, value in checks})

    def test_outgoing_xml_always_has_one_declaration_and_lf_line_endings(self):
        declarations = (
            "",
            '<?xml version="1.0"?>\r\n',
            "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\r\n",
        )
        frame_body = frame(body="<text>kehys\r\nrivi\rloppu</text>")
        doc_body = document(body="<text>asiakirja\r\nrivi\rloppu</text>")
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                inputs = (declaration + frame_body, declaration + doc_body)
                payload, _ = prepare_validation_request(*inputs)
                for field, source in zip(("siirtokehysXml", "asiakirjaXml"), inputs):
                    sent = payload[field]
                    self.assertTrue(sent.startswith("<?xml "))
                    self.assertEqual(1, sent.count("<?xml"))
                    self.assertNotIn("\r", sent)
                    self.assertEqual(ET.canonicalize(source), ET.canonicalize(sent))

    def test_line_normalization_preserves_base64_bytes_and_json_values(self):
        # The encoded attachment's own CRLF bytes must stay unchanged. Only
        # XML whitespace surrounding/wrapping its Base64 text is normalized.
        attachment = '<html xmlns="http://www.w3.org/1999/xhtml">\r\n<body>ääkköset &amp; teksti</body>\r\n</html>'
        encoded = b64(attachment)
        wrapped = "\n".join(encoded[index:index + 24] for index in range(0, len(encoded), 24))
        json_value = {"label": "ääkköset", "values": ["teksti & sisältö", 42, True]}
        body = (
            '\n<component>\n<nonXMLBody>\n'
            f'<text mediaType="application/xhtml+xml" representation="B64">\n{wrapped}\n</text>\n'
            '</nonXMLBody>\n</component>\n'
            f'<value><![CDATA[{json.dumps(json_value, ensure_ascii=False, indent=2)}]]></value>\n'
        )
        for line_ending in ("\r\n", "\r", "\n"):
            with self.subTest(line_ending=repr(line_ending)):
                inputs = (
                    frame(body="\n<text>kehys &amp; rivi</text>\n").replace("\n", line_ending),
                    document(body=body).replace("\n", line_ending),
                )
                payload, _ = prepare_validation_request(*inputs)
                sent_json = json.loads(json.dumps(payload, ensure_ascii=False))
                self.assertEqual(
                    {"messageId", "palveluPyynto", "level", "siirtokehysXml", "asiakirjaXml"},
                    set(sent_json),
                )
                for field, source in zip(("siirtokehysXml", "asiakirjaXml"), inputs):
                    self.assertNotIn("\r", sent_json[field])
                    self.assertEqual(ET.canonicalize(source), ET.canonicalize(sent_json[field]))
                parsed = ET.fromstring(sent_json["asiakirjaXml"])
                encoded_text = parsed.find(f".//{{{HL7_NAMESPACE}}}nonXMLBody/{{{HL7_NAMESPACE}}}text").text
                self.assertEqual(attachment.encode("utf-8"), base64.b64decode(encoded_text))
                self.assertEqual(json_value, json.loads(parsed.find(f"{{{HL7_NAMESPACE}}}value").text))

    def test_outgoing_normalization_preserves_namespace_scopes_and_body_order(self):
        source = (
            f'<ClinicalDocument xmlns="{HL7_NAMESPACE}" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:type="urn:outer-type">\r\n'
            '<id root="1.2.3.4"/><setId root="1.2.3.5"/>\r\n'
            '<value xsi:type="type:Outer">ensimmäinen &amp; teksti</value>\r\n'
            '<section xmlns:type="urn:inner-type"><value xsi:type="type:Inner"/></section>\r\n'
            '<free xmlns=""><x>nimialueeton</x></free>\r\n'
            '<value xsi:type="type:After"><![CDATA[viimeinen <teksti>]]></value>\r\n'
            '</ClinicalDocument>'
        )
        payload, _ = prepare_validation_request(frame(), source)
        sent = payload["asiakirjaXml"]
        self.assertEqual(resolved_xsi_types(source), resolved_xsi_types(sent))
        self.assertEqual(ET.canonicalize(source), ET.canonicalize(sent))
        parsed = ET.fromstring(sent)
        self.assertEqual(
            [f"{{{HL7_NAMESPACE}}}{name}" for name in ("id", "setId", "value", "section")]
            + ["free", f"{{{HL7_NAMESPACE}}}value"],
            [child.tag for child in parsed],
        )
        self.assertEqual("nimialueeton", parsed.find("free/x").text)
        self.assertEqual("viimeinen <teksti>", parsed[-1].text)

    def test_oid_mismatches_and_absent_roots_block_request(self):
        pairs = (
            (frame(id_root="1.2.3.99"), document()),
            (frame(set_root="1.2.3.99"), document()),
            (frame(id_root=""), document()),
            (frame(set_root=""), document()),
            (frame(), document(id_root="")),
            (frame(), document(set_root="")),
            (frame().replace('<id root="1.2.3.4"/>', ""), document()),
            (frame(), document().replace('<setId root="1.2.3.5"/>', "")),
        )
        for frame_source, doc_source in pairs:
            with self.subTest(frame=frame_source, document=doc_source):
                with self.assertRaises(ValueError):
                    prepare_validation_request(frame_source, doc_source)

    def test_reason_code_missing_empty_or_whitespace_blocks_request(self):
        for reason in ("", 'code=""', 'code="   "'):
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    prepare_validation_request(frame(reason=reason), document())
        with self.assertRaises(ValueError):
            prepare_validation_request(frame().replace('<reasonCode code="SP1"/>', ""), document())

    def test_service_reason_code_selected_independently_of_purpose_and_order(self):
        purpose = '<reasonCode code="1" codeSystem="1.2.246.537.6.1289.201901"/>'
        for service_code in ("SP1", "SP17"):
            service = (
                f'<reasonCode code="{service_code}" '
                'codeSystem="1.2.246.537.6.1503.201601"/>'
            )
            for reasons in (purpose + service, service + purpose):
                with self.subTest(service_code=service_code, reasons=reasons):
                    source = frame().replace('<reasonCode code="SP1"/>', reasons)
                    payload, _ = prepare_validation_request(source, document())
                    self.assertEqual(service_code, payload["palveluPyynto"])

    def test_purpose_only_or_missing_service_code_blocks_request(self):
        purpose = '<reasonCode code="1" codeSystem="1.2.246.537.6.1289.201901"/>'
        missing_services = (
            "",
            '<reasonCode codeSystem="1.2.246.537.6.1503.201601"/>',
            '<reasonCode code="" codeSystem="1.2.246.537.6.1503.201601"/>',
            '<reasonCode code="   " codeSystem="1.2.246.537.6.1503.201601"/>',
        )
        for service in missing_services:
            with self.subTest(service=service):
                source = frame().replace('<reasonCode code="SP1"/>', purpose + service)
                with self.assertRaises(ValueError):
                    prepare_validation_request(source, document())

    def test_multiple_service_reason_codes_block_request(self):
        reasons = (
            '<reasonCode code="SP1" codeSystem="1.2.246.537.6.1503.201601"/>'
            '<reasonCode code="1" codeSystem="1.2.246.537.6.1289.201901"/>'
            '<reasonCode code="SP17" codeSystem="1.2.246.537.6.1503.201601"/>'
        )
        source = frame().replace('<reasonCode code="SP1"/>', reasons)
        with self.assertRaises(ValueError):
            prepare_validation_request(source, document())

    def test_malformed_frame_blocks_request(self):
        with self.assertRaises(ValueError):
            prepare_validation_request(frame()[:-1], document())

    def test_input_size_limit_blocks_request(self):
        with patch.object(validator_core, "MAX_INPUT_CHARS", 16):
            with self.assertRaises(ValueError):
                prepare_validation_request(frame(), document())

    def test_non_test_identifier_in_either_input_blocks_request(self):
        hetu = synthetic_hetu_with_check("U")
        pairs = (
            (frame(body=f"<text>{hetu}</text>"), document()),
            (frame(), document(body=f"<text>{hetu}</text>")),
            (frame(), b64(document(body=f"<text>{hetu}</text>"))),
        )
        for frame_source, doc_source in pairs:
            with self.subTest(frame=frame_source, document=doc_source):
                with self.assertRaises(ValueError):
                    prepare_validation_request(frame_source, doc_source)

    def test_latin1_and_utf16_content_survives_utf8_serialization(self):
        doc_text = document(body='<text language="fi">ääkköset &amp; öljy</text>')
        frame_text = frame(body="<text>lähetys</text>")
        for encoding in ("iso-8859-1", "utf-16le", "utf-16be"):
            with self.subTest(encoding=encoding):
                declaration = f'<?xml version="1.0" encoding="{encoding}"?>'
                payload, _ = prepare_validation_request(
                    (declaration + frame_text).encode(encoding),
                    (declaration + doc_text).encode(encoding),
                )
                parsed_doc = ET.fromstring(payload["asiakirjaXml"].encode("utf-8"))
                parsed_frame = ET.fromstring(payload["siirtokehysXml"].encode("utf-8"))
                text = parsed_doc.find(f"{{{HL7_NAMESPACE}}}text")
                self.assertEqual("ääkköset & öljy", text.text)
                self.assertEqual("fi", text.get("language"))
                self.assertEqual("lähetys", parsed_frame.find(f".//{{{HL7_NAMESPACE}}}text").text)


if __name__ == "__main__":
    unittest.main()
