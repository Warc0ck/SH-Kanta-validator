"""Korvaustoimintojen regressiotestit käyttävät vain synteettisiä syötteitä."""

import base64
from io import StringIO
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validator_core import (  # noqa: E402
    HETU_CHECK_CHARS,
    HL7_NAMESPACE,
    extract_clinical_doc_identifiers,
    is_valid_finnish_hetu,
    prepare_validation_request,
)
from validator_replacer import (  # noqa: E402
    EMPTY_XHTML,
    REPLACEMENT_HETU,
    encode_document_json_for_sending,
    json_korvaaja,
    korvaaja,
    prepare_document_for_inspection,
    replace_validation_inputs,
)


def synthetic_hetu(individual=2):
    """Laskee testitunnuksen ilman oikean henkilön henkilötunnusta."""
    date = "010100"
    number = f"{individual:03d}"
    return date + "A" + number + HETU_CHECK_CHARS[int(date + number) % 31]


def document(body="", namespaces=""):
    """Muodostaa synteettisen CDA-asiakirjan ja säilytettävät root-tunnisteet."""
    return (
        f'<ClinicalDocument xmlns="{HL7_NAMESPACE}" {namespaces}>'
        '<id root="1.2.3.4"/><setId root="1.2.3.5"/>'
        f"{body}</ClinicalDocument>"
    )


def frame(body=""):
    """Muodostaa synteettisen kehyksen samoilla asiakirjatunnisteilla."""
    return (
        f'<MCCI_IN000001UV01 xmlns="{HL7_NAMESPACE}">'
        '<controlActProcess><reasonCode code="SP1"/>'
        '<subject><clinicalDocument><id root="1.2.3.4"/>'
        '<setId root="1.2.3.5"/></clinicalDocument></subject>'
        f"{body}</controlActProcess></MCCI_IN000001UV01>"
    )


def b64(source):
    """Koodaa testin tekstisisällön UTF-8-Base64:ksi."""
    return base64.b64encode(source.encode("utf-8")).decode("ascii")


def resolved_types(source):
    """Lukee xsi:type-arvojen nimiavaruudet kunkin elementin omassa laajuudessa."""
    pending = []
    scopes = []
    scope = {}
    resolved = []
    for event, item in ET.iterparse(StringIO(source), events=("start-ns", "start", "end")):
        if event == "start-ns":
            pending.append(item)
        elif event == "start":
            scopes.append(scope)
            scope = {**scope, **dict(pending)}
            pending.clear()
            value = item.get("{http://www.w3.org/2001/XMLSchema-instance}type")
            if value:
                prefix, name = value.split(":", 1)
                resolved.append((scope.get(prefix), name))
        else:
            scope = scopes.pop()
    return resolved


class XmlReplacementTests(unittest.TestCase):
    def test_elementtree_helper_handles_namespaces_and_only_named_fields(self):
        """Varmistaa nimiavaruuksien käsittelyn ja korvausten rajauksen."""
        hetu = synthetic_hetu()
        root = ET.fromstring(
            f'<root xmlns="{HL7_NAMESPACE}"><id extension="{hetu}" root="keep"/>'
            '<id extension="ordinary-id"/><name><given>Testi</given><family>Sukunimi</family></name>'
            '<given>Ei nimikenttä</given><birthTime value="20000101"/>'
            '<birthTime nullFlavor="UNK"/></root>'
        )
        korvaaja(root, [])
        ids = root.findall(f"{{{HL7_NAMESPACE}}}id")
        self.assertEqual(REPLACEMENT_HETU, ids[0].get("extension"))
        self.assertEqual("keep", ids[0].get("root"))
        self.assertEqual("ordinary-id", ids[1].get("extension"))
        self.assertEqual(["feikkinimi", "feikkinimi"], [child.text for child in root[2]])
        self.assertEqual("Ei nimikenttä", root[3].text)
        self.assertEqual("19810101", root[4].get("value"))
        self.assertIsNone(root[5].get("value"))

    def test_replacement_hetu_matches_replacement_birth_date(self):
        """Tarkistaa geneerisen henkilötunnuksen kelvollisuuden ja syntymäpäivän."""
        self.assertTrue(is_valid_finnish_hetu(REPLACEMENT_HETU))
        self.assertEqual("010181", REPLACEMENT_HETU[:6])
        self.assertEqual("9", REPLACEMENT_HETU[7])

    def test_syntactic_hetu_is_replaced_even_when_checksum_is_wrong(self):
        """Korvaa myös henkilötunnuksen muotoisen arvon virheellisellä tarkistusmerkillä."""
        candidate = synthetic_hetu()
        bad_check = "0" if candidate[-1] != "0" else "1"
        root = ET.fromstring(f'<id extension="{candidate[:-1]}{bad_check}"/>')
        korvaaja(root)
        self.assertEqual(REPLACEMENT_HETU, root.get("extension"))

    def test_pair_preserves_source_bytes_and_document_identifiers(self):
        """Tarkistaa molempien sanomien korvaukset alkuperäisiä tavuja muuttamatta."""
        body = (
            f'<patient><id extension="{synthetic_hetu()}"/>'
            '<name><given>Ääkköstesti</given><family>Esimerkki</family></name>'
            '<birthTime value="20000101"/></patient>'
        )
        frame_source = frame(body).encode("utf-8")
        document_source = ('<?xml version="1.0" encoding="UTF-16"?>' + document(body)).encode("utf-16")
        original_pair = frame_source, document_source
        replaced_frame, replaced_document = replace_validation_inputs(frame_source, document_source)
        self.assertEqual(original_pair, (frame_source, document_source))
        for source in (replaced_frame, replaced_document):
            self.assertTrue(source.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
            self.assertNotIn(synthetic_hetu(), source)
            self.assertIn(REPLACEMENT_HETU, source)
            self.assertEqual(("1.2.3.4", "1.2.3.5"), extract_clinical_doc_identifiers(source))
        payload, checks = prepare_validation_request(replaced_frame, replaced_document)
        self.assertEqual("SP1", payload["palveluPyynto"])
        self.assertEqual(2, len(checks))

    def test_extracted_document_retains_inherited_qnames_and_namespace_scopes(self):
        """Varmistaa perityt prefixit, uudelleen sidotut prefixit ja xmlns-tyhjennyksen."""
        body = (
            '<value xsi:type="kind:Outer">säilyy</value>'
            '<section xmlns:kind="urn:inner"><value xsi:type="kind:Inner"/></section>'
            '<extension xmlns=""><value xsi:type="kind:Plain"/></extension>'
        )
        source = (
            '<trace xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:kind="urn:outer">'
            + document(body) + '</trace>'
        )
        _, replaced = replace_validation_inputs(frame(), source)
        self.assertEqual(
            [("urn:outer", "Outer"), ("urn:inner", "Inner"), ("urn:outer", "Plain")],
            resolved_types(replaced),
        )
        root = ET.fromstring(replaced)
        self.assertIsNotNone(root.find("extension/value"))
        self.assertIn("säilyy", replaced)

    def test_missing_json_and_narrative_are_valid(self):
        """Sallii asiakirjan, jossa ei ole korvattavia JSON- tai XHTML-liitteitä."""
        replaced_frame, replaced_document = replace_validation_inputs(frame(), document())
        prepare_validation_request(replaced_frame, replaced_document)

    def test_malformed_xml_and_dtd_are_rejected(self):
        """Estää virheellisen XML:n ja ulkoiset määrittelyt jo ennen korvaamista."""
        for source in ("<broken>", '<!DOCTYPE frame [<!ENTITY x "test">]><frame/>'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                replace_validation_inputs(source, document())


class JsonReplacementTests(unittest.TestCase):
    def test_known_json_fields_keep_list_shapes_and_metadata(self):
        """Korvaa nimet, yhteystiedot ja tunnisteet säilyttäen JSON-rakenteen."""
        source = {
            "sukunimi": "Esimerkki",
            "etunimet": ["Testi", "Toinen"],
            "postinumero": {"value": "00100", "codeSystem": "säilyy"},
            "lahiosoite": "Testikatu 2",
            "postitoimipaikka": "Testipaikka",
            "puhelinnumero": ["010 000 0000"],
            "sahkopostiosoite": "fixture@example.invalid",
            "syntymaaika": "2000-01-01",
            "henkilotunnus": [{"value": synthetic_hetu(), "codeSystem": "säilyy"}],
            "tilapainen_yksilointitunnus": {"value": "fixture-id"},
            "muu": "säilyy",
        }
        json_korvaaja(source)
        self.assertEqual("feikkinimi", source["sukunimi"])
        self.assertEqual(["feikkinimi", "feikkinimi"], source["etunimet"])
        self.assertEqual({"value": "12345", "codeSystem": "säilyy"}, source["postinumero"])
        self.assertEqual("Katuosoite 1", source["lahiosoite"])
        self.assertEqual("Kaupunki", source["postitoimipaikka"])
        self.assertEqual(["012 345 6789"], source["puhelinnumero"])
        self.assertEqual("testi@example.invalid", source["sahkopostiosoite"])
        self.assertEqual("1981-01-01", source["syntymaaika"])
        self.assertEqual(REPLACEMENT_HETU, source["henkilotunnus"][0]["value"])
        self.assertEqual("säilyy", source["henkilotunnus"][0]["codeSystem"])
        self.assertEqual(REPLACEMENT_HETU, source["tilapainen_yksilointitunnus"]["value"])
        self.assertEqual("säilyy", source["muu"])

    def test_base64_json_is_normalized_replaced_and_utf8_encoded(self):
        """Käsittelee rivitetyn täytteettömän Base64:n ja säilyttää ääkköset muissa kentissä."""
        fixture = json.dumps({"etunimet": ["Testi"], "muu": "Ääkköstesti"}, ensure_ascii=False)
        encoded = b64(fixture).rstrip("=")
        encoded = encoded[:16] + "\n" + encoded[16:]
        _, replaced = replace_validation_inputs(
            frame(), document(f'<f:json xmlns:f="urn:hl7finland">{encoded}</f:json>')
        )
        element = ET.fromstring(replaced).find("{urn:hl7finland}json")
        decoded = base64.b64decode(element.text, validate=True).decode("utf-8")
        self.assertEqual({"etunimet": ["feikkinimi"], "muu": "Ääkköstesti"}, json.loads(decoded))

    def test_json_in_frame_is_also_replaced(self):
        """Tekee samat liitekorvaukset myös siirtokehyksen JSON-kenttiin."""
        body = f'<f:json xmlns:f="urn:hl7finland">{b64(json.dumps({"sukunimi": "Testi"}))}</f:json>'
        replaced, _ = replace_validation_inputs(frame(body), document())
        encoded = ET.fromstring(replaced).find(f"{{{HL7_NAMESPACE}}}controlActProcess/{{urn:hl7finland}}json").text
        self.assertEqual("feikkinimi", json.loads(base64.b64decode(encoded))["sukunimi"])

    def test_plain_json_retains_plain_representation(self):
        """Säilyttää tekstimuotoisen JSON:n esitystavan korvaamisen jälkeen."""
        _, replaced = replace_validation_inputs(
            frame(), document('<f:json xmlns:f="urn:hl7finland">{"sukunimi":"Testi"}</f:json>')
        )
        text = ET.fromstring(replaced).find("{urn:hl7finland}json").text
        self.assertEqual({"sukunimi": "feikkinimi"}, json.loads(text))

    def test_malformed_json_and_base64_are_rejected(self):
        """Estää korvaustuloksen palauttamisen, jos ilmoitettu liite on virheellinen."""
        for content in ("{invalid}", "%%invalid-base64%%", b64("not JSON")):
            with self.subTest(content=content), self.assertRaises(ValueError):
                replace_validation_inputs(
                    frame(), document(f'<f:json xmlns:f="urn:hl7finland">{content}</f:json>')
                )


class NarrativeReplacementTests(unittest.TestCase):
    def test_all_base64_xhtml_narratives_are_replaced_with_padded_base64(self):
        """Korvaa kaikki XHTML-näyttömuodot ja käyttää standardin mukaista Base64-täytettä."""
        narrative = '<div xmlns="http://www.w3.org/1999/xhtml">Testin vapaata tekstiä</div>'
        body = (
            f'<nonXMLBody mediaType="application/xhtml+xml"><text representation="B64">{b64(narrative)}</text></nonXMLBody>'
            f'<text mediaType="application/xhtml+xml" representation="B64">{b64(narrative)}</text>'
        )
        _, replaced = replace_validation_inputs(frame(), document(body))
        texts = ET.fromstring(replaced).findall(f".//{{{HL7_NAMESPACE}}}text")
        self.assertEqual(2, len(texts))
        for element in texts:
            self.assertEqual(EMPTY_XHTML, base64.b64decode(element.text, validate=True).decode("utf-8"))
            self.assertTrue(element.text.endswith("="))
        self.assertNotIn("Testin vapaata tekstiä", replaced)

    def test_structured_xhtml_is_replaced_with_empty_xhtml_div(self):
        """Tyhjentää rakenteisen XHTML:n säilyttäen div-elementin oikean nimiavaruuden."""
        body = (
            '<text mediaType="application/xhtml+xml">'
            '<div xmlns="http://www.w3.org/1999/xhtml"><p>Testiteksti</p></div></text>'
        )
        _, replaced = replace_validation_inputs(frame(), document(body))
        text = ET.fromstring(replaced).find(f"{{{HL7_NAMESPACE}}}text")
        self.assertEqual(1, len(text))
        self.assertEqual("{http://www.w3.org/1999/xhtml}div", text[0].tag)
        self.assertEqual(0, len(text[0]))
        self.assertNotIn("Testiteksti", replaced)

    def test_text_representation_is_replaced_without_base64_conversion(self):
        """Korvaa CDATA-tekstinä annetun XHTML:n säilyttäen TXT-esitystavan."""
        body = (
            '<text mediaType="application/xhtml+xml" representation="TXT">'
            '<![CDATA[<div>Testiteksti</div>]]></text>'
        )
        _, replaced = replace_validation_inputs(frame(), document(body))
        text = ET.fromstring(replaced).find(f"{{{HL7_NAMESPACE}}}text")
        self.assertEqual("TXT", text.get("representation"))
        self.assertEqual(EMPTY_XHTML, text.text)

    def test_invalid_explicit_base64_narrative_is_rejected(self):
        """Estää virheellisen B64-näyttömuodon piilottamisen korvauksen aikana."""
        with self.assertRaises(ValueError):
            replace_validation_inputs(
                frame(), document('<text mediaType="application/xhtml+xml" representation="B64">%%%broken%%%</text>')
            )


class JsonInspectionTests(unittest.TestCase):
    def test_inspection_shows_pretty_json_and_roundtrip_preserves_special_characters(self):
        """Tarkistaa ääkkösten, XML-merkkien ja CDATA-lopetusmerkkien turvallisen kierron."""
        fixture = {
            "sisalto": "Ääkköset <teksti> & lainaus \" sekä ]]> ja vielä ]]>",
            "rivit": ["ensimmäinen", {"toinen": True}],
        }
        source = document(f'<f:json xmlns:f="urn:hl7finland">{b64(json.dumps(fixture))}</f:json>')
        inspected = prepare_document_for_inspection(source)
        element = ET.fromstring(inspected).find("{urn:hl7finland}json")
        self.assertEqual(json.dumps(fixture, ensure_ascii=False, indent=2), element.text)
        self.assertIn('<![CDATA[{\n  "sisalto": "Ääkköset <teksti> &', inspected)
        self.assertIn("]]]]><![CDATA[>", inspected)
        self.assertIsNone(element.get("representation"))
        sending = encode_document_json_for_sending(inspected)
        encoded = ET.fromstring(sending).find("{urn:hl7finland}json")
        self.assertEqual(fixture, json.loads(base64.b64decode(encoded.text, validate=True).decode("utf-8")))
        self.assertIsNone(encoded.get("representation"))
        self.assertEqual(source, document(f'<f:json xmlns:f="urn:hl7finland">{b64(json.dumps(fixture))}</f:json>'))

    def test_all_json_attachments_are_transformed_with_existing_metadata_preserved(self):
        """Muuttaa kaikki JSON-liitteet ja säilyttää niiden attribuutit sekä muut nimiavaruudet."""
        first = {"ensimmainen": "Ääkköstesti"}
        second = [1, {"toinen": "säilyy"}]
        body = (
            f'<f:json xmlns:f="urn:hl7finland" representation="B64" mediaType="application/json" fixture="keep">{b64(json.dumps(first))}</f:json>'
            '<section><f:json xmlns:f="urn:hl7finland" representation="TXT"><![CDATA['
            + json.dumps(second, ensure_ascii=False)
            + ']]></f:json></section><json xmlns="">ei JSON-liite</json>'
        )
        inspected = prepare_document_for_inspection(document(body))
        root = ET.fromstring(inspected)
        elements = root.findall(".//{urn:hl7finland}json")
        self.assertEqual([first, second], [json.loads(element.text) for element in elements])
        self.assertEqual(["TXT", "TXT"], [element.get("representation") for element in elements])
        self.assertEqual("application/json", elements[0].get("mediaType"))
        self.assertEqual("keep", elements[0].get("fixture"))
        self.assertEqual("ei JSON-liite", root.find("json").text)
        sending = encode_document_json_for_sending(inspected)
        elements = ET.fromstring(sending).findall(".//{urn:hl7finland}json")
        self.assertEqual(["B64", "B64"], [element.get("representation") for element in elements])
        self.assertEqual([first, second], [json.loads(base64.b64decode(element.text, validate=True)) for element in elements])

    def test_already_encoded_json_is_normalized_without_double_encoding(self):
        """Säilyttää jo Base64-koodatun liitteen JSON-sisällön ilman kaksinkertaista koodausta."""
        fixture = {"nimi": "Ääkköstesti"}
        encoded = b64(json.dumps(fixture, ensure_ascii=False)).rstrip("=")
        encoded = encoded[:8] + "\n" + encoded[8:]
        source = ('<?xml version="1.0" encoding="UTF-16"?>' + document(
            f'<f:json xmlns:f="urn:hl7finland" representation="B64">{encoded}</f:json>'
        )).encode("utf-16")
        sending = encode_document_json_for_sending(source)
        element = ET.fromstring(sending).find("{urn:hl7finland}json")
        self.assertTrue(sending.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        self.assertEqual("B64", element.get("representation"))
        self.assertEqual(fixture, json.loads(base64.b64decode(element.text, validate=True).decode("utf-8")))

    def test_plain_json_without_representation_is_encoded_before_sending(self):
        """Koodaa myös attribuutittoman tekstimuotoisen JSON:n lähetysversioon."""
        fixtures = ({"teksti": "Ääkköset"}, [1, 2], "Testiteksti", True, None)
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                source = document('<f:json xmlns:f="urn:hl7finland"><![CDATA['
                                  + json.dumps(fixture, ensure_ascii=False) + ']]></f:json>')
                sending = encode_document_json_for_sending(source)
                element = ET.fromstring(sending).find("{urn:hl7finland}json")
                self.assertEqual(fixture, json.loads(base64.b64decode(element.text, validate=True)))
                self.assertIsNone(element.get("representation"))

    def test_inspection_and_sending_preserve_inherited_qnames_and_document_identifiers(self):
        """Säilyttää perityt QName-prefixit, nimiavaruusrajat ja CDA:n tunnisteet."""
        body = (
            '<value xsi:type="kind:Outer"/>'
            '<section xmlns:kind="urn:inner"><value xsi:type="kind:Inner"/>'
            f'<f:json xmlns:f="urn:hl7finland">{b64("{}")}</f:json></section>'
            '<extension xmlns=""><value xsi:type="kind:Plain"/></extension>'
        )
        source = ('<trace xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                  'xmlns:kind="urn:outer">' + document(body) + '</trace>')
        inspected = prepare_document_for_inspection(source)
        sending = encode_document_json_for_sending(inspected)
        for result in (inspected, sending):
            self.assertEqual([("urn:outer", "Outer"), ("urn:inner", "Inner"), ("urn:outer", "Plain")], resolved_types(result))
            self.assertEqual(("1.2.3.4", "1.2.3.5"), extract_clinical_doc_identifiers(result))
            self.assertIsNotNone(ET.fromstring(result).find("extension/value"))

    def test_document_without_json_keeps_other_content(self):
        """Sallii JSON-liitteettömän asiakirjan muuttamatta muita tekstisisältöjä."""
        source = document('<text mediaType="text/plain">Säilyvä teksti</text>')
        for transform in (prepare_document_for_inspection, encode_document_json_for_sending):
            with self.subTest(transform=transform.__name__):
                result = transform(source)
                self.assertEqual("Säilyvä teksti", ET.fromstring(result).find(f"{{{HL7_NAMESPACE}}}text").text)
                self.assertEqual(("1.2.3.4", "1.2.3.5"), extract_clinical_doc_identifiers(result))

    def test_invalid_xml_json_and_base64_block_both_transforms(self):
        """Estää virheellisen XML:n, JSON:n ja Base64:n tarkistus- ja lähetysversioissa."""
        invalid_sources = (
            "<broken>",
            '<!DOCTYPE doc [<!ENTITY x "test">]>' + document(),
            document('<f:json xmlns:f="urn:hl7finland">{invalid}</f:json>'),
            document('<f:json xmlns:f="urn:hl7finland">{"numero":NaN}</f:json>'),
            document(f'<f:json xmlns:f="urn:hl7finland" representation="B64">{b64("[Infinity]")}</f:json>'),
            document('<f:json xmlns:f="urn:hl7finland" representation="B64">%%%broken%%%</f:json>'),
            document(f'<f:json xmlns:f="urn:hl7finland" representation="B64">{b64("not JSON")}</f:json>'),
            document(f'<f:json xmlns:f="urn:hl7finland" representation="TXT">{b64("{}")}</f:json>'),
            document('<f:json xmlns:f="urn:hl7finland" representation="B64">{}</f:json>'),
            document('<f:json xmlns:f="urn:hl7finland"><value/></f:json>'),
        )
        for transform in (prepare_document_for_inspection, encode_document_json_for_sending):
            for source in invalid_sources:
                with self.subTest(transform=transform.__name__, source=source), self.assertRaises(ValueError):
                    transform(source)


if __name__ == "__main__":
    unittest.main()
