"""Korvaa tunnettuja henkilötietokenttiä paikallisesti ilman tiedostoja tai verkkoa.

Korvaus ei takaa koko aineiston anonymisointia: esimerkiksi vapaa teksti ja
muut kuin tässä nimetyt kentät jäävät käyttäjän tarkistettaviksi.
"""

from __future__ import annotations

import base64
import json
import xml.etree.ElementTree as ET
from xml.dom import Node, minidom

from validator_core import (
    HL7_NAMESPACE,
    _HETU_PATTERN,
    _decode_base64,
    _local_name,
    _parse_xml,
    _to_text_helper,
    _utf8_xml_declaration,
    ensure_clinical_document_xml,
)


REPLACEMENT_HETU = "010181-900C"
REPLACEMENT_NAME = "feikkinimi"
EMPTY_XHTML = '<div class="soc-document" xmlns="http://www.w3.org/1999/xhtml"/>'
_FINLAND_NAMESPACE = "urn:hl7finland"
_XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
_IDENTIFIER_FIELDS = {"henkilotunnus", "tilapainen_yksilointitunnus"}
_JSON_REPLACEMENTS = {
    "sukunimi": REPLACEMENT_NAME,
    "etunimet": REPLACEMENT_NAME,
    "postinumero": "12345",
    "lahiosoite": "Katuosoite 1",
    "postitoimipaikka": "Kaupunki",
    "puhelinnumero": "012 345 6789",
    "sahkopostiosoite": "testi@example.invalid",
    "syntymaaika": "1981-01-01",
    **dict.fromkeys(_IDENTIFIER_FIELDS, REPLACEMENT_HETU),
}


def _xml_replacements(name: str, attributes: dict, parents: tuple) -> tuple[dict, str | None]:
    """Valitsee XML-kentän korvaukset muuttamatta tunnisteiden root-arvoja."""
    updated = {}
    replacement_text = None
    if name == "id":
        extension = attributes.get("extension", "")
        if _HETU_PATTERN.fullmatch(extension):
            updated["extension"] = REPLACEMENT_HETU
    elif name in {"given", "family"} and "name" in parents:
        replacement_text = REPLACEMENT_NAME
    elif name == "birthTime" and "value" in attributes:
        updated["value"] = "19810101"
    return updated, replacement_text


def korvaaja(element: ET.Element, parent_list=()) -> None:
    """Korvaa XML-puun henkilötunnus-, nimi- ja syntymäaikakentät paikallaan."""
    pending = [(element, tuple(_local_name(name) for name in parent_list))]
    while pending:
        current, parents = pending.pop()
        name = _local_name(current.tag)
        attributes, replacement_text = _xml_replacements(name, current.attrib, parents)
        current.attrib.update(attributes)
        if replacement_text is not None:
            current.text = replacement_text
            # Nimikentän mahdollinen sisäinen teksti poistuu samalla.
            current[:] = []
        pending.extend((child, parents + (name,)) for child in reversed(list(current)))


def _replace_json_field(value, replacement: str):
    """Säilyttää JSON-kentän listat ja arvo-oliot korvatessaan varsinaisen arvon."""
    if isinstance(value, list):
        return [_replace_json_field(item, replacement) for item in value]
    if isinstance(value, dict):
        for key in ("value", "arvo", "text"):
            if key in value:
                value[key] = _replace_json_field(value[key], replacement)
        return value
    if value is None or isinstance(value, bool):
        return value
    return replacement


def json_korvaaja(element, parent=None) -> None:
    """Korvaa nimetyt JSON-kentät paikallaan ja säilyttää listojen tunnistekontekstin."""
    pending = [(element, parent)]
    while pending:
        current, parent_name = pending.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                replacement = _JSON_REPLACEMENTS.get(key)
                if key == "value" and parent_name in _IDENTIFIER_FIELDS:
                    replacement = REPLACEMENT_HETU
                if replacement is not None:
                    value = current[key] = _replace_json_field(value, replacement)
                if isinstance(value, (dict, list)):
                    pending.append((value, key))
        elif isinstance(current, list):
            pending.extend((item, parent_name) for item in current)


def _set_dom_text(element, value: str) -> None:
    """Poistaa elementin aiemman sisällön ja lisää uuden tekstiarvon."""
    for child in list(element.childNodes):
        element.removeChild(child)
        child.unlink()
    element.appendChild(element.ownerDocument.createTextNode(value))


def _dom_text(element) -> str:
    """Lukee elementin tekstin ja CDATA:n sekä estää rakenteisen JSON-liitteen."""
    if any(child.nodeType == Node.ELEMENT_NODE for child in element.childNodes):
        raise ValueError("JSON-liitteen on oltava tekstiä tai Base64-koodattua tekstiä.")
    return "".join(
        child.data for child in element.childNodes
        if child.nodeType in {Node.TEXT_NODE, Node.CDATA_SECTION_NODE}
    )


def _reject_json_constant(value: str) -> None:
    """Estää JSON-standardin ulkopuoliset NaN- ja Infinity-arvot."""
    raise ValueError(f"JSON-liitteessä on virheellinen numeroarvo: {value}.")


def _read_json_element(element):
    """Lukee JSON-liitteen tekstistä tai Base64:stä sekä tunnistaa esitystavan."""
    source = _dom_text(element).strip()
    representation = element.getAttribute("representation").upper()
    if representation != "B64":
        try:
            return json.loads(source, parse_constant=_reject_json_constant), False
        except (ValueError, RecursionError) as error:
            if representation == "TXT" or source.startswith(("{", "[")):
                raise ValueError("HL7 Finland -laajennoksen JSON-liite ei ole kelvollista JSON:ia.") from error
    decoded = _decode_base64(source, explicit=True, media_type="application/json")
    try:
        return json.loads(decoded, parse_constant=_reject_json_constant), True
    except (ValueError, RecursionError) as error:
        raise ValueError("HL7 Finland -laajennoksen JSON-liite ei ole kelvollista JSON:ia.") from error


def _replace_json_element(element) -> None:
    """Purkaa suomalaisen HL7-laajennoksen JSON:n ja palauttaa korvatun sisällön."""
    value, encoded = _read_json_element(element)
    try:
        json_korvaaja(value)
        replaced = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError("HL7 Finland -laajennoksen JSON-liite ei ole kelvollista JSON:ia.") from error
    if encoded:
        replaced = base64.b64encode(replaced.encode("utf-8")).decode("ascii")
    _set_dom_text(element, replaced)


def _set_dom_cdata(element, value: str) -> None:
    """Näyttää tekstin CDATA:na ja jakaa CDATA:n lopetusmerkit turvallisesti."""
    _set_dom_text(element, "")
    parts = value.split("]]>")
    for index, part in enumerate(parts):
        if index:
            part = ">" + part
        if index < len(parts) - 1:
            part += "]]"
        element.appendChild(element.ownerDocument.createCDATASection(part))


def _transform_document_json(source: bytes | str, *, encode: bool) -> str:
    """Muuttaa kaikki CDA:n JSON-liitteet luettaviksi tai lähetyksen Base64:ksi."""
    text = ensure_clinical_document_xml(source)
    _parse_xml(text, "Asiakirja")
    tree = minidom.parseString(_utf8_xml_declaration(text).encode("utf-8"))
    try:
        pending = [tree.documentElement]
        while pending:
            element = pending.pop()
            if element.namespaceURI == _FINLAND_NAMESPACE and element.localName == "json":
                value, _ = _read_json_element(element)
                try:
                    formatted = json.dumps(value, ensure_ascii=False, indent=None if encode else 2, allow_nan=False)
                except (ValueError, RecursionError) as error:
                    raise ValueError("HL7 Finland -laajennoksen JSON-liitettä ei voida muotoilla.") from error
                if encode:
                    _set_dom_text(element, base64.b64encode(formatted.encode("utf-8")).decode("ascii"))
                else:
                    _set_dom_cdata(element, formatted)
                if element.hasAttribute("representation"):
                    element.setAttribute("representation", "B64" if encode else "TXT")
            pending.extend(
                child for child in reversed(list(element.childNodes)) if child.nodeType == Node.ELEMENT_NODE
            )
        return _utf8_xml_declaration(tree.toxml(encoding="utf-8").decode("utf-8"))
    finally:
        tree.unlink()


def prepare_document_for_inspection(source: bytes | str) -> str:
    """Palauttaa CDA:n, jonka kaikki JSON-liitteet ovat sisennettyä CDATA-tekstiä."""
    return _transform_document_json(source, encode=False)


def encode_document_json_for_sending(source: bytes | str) -> str:
    """Koodaa CDA:n kaikki JSON-liitteet UTF-8-Base64:ksi ennen lähettämistä."""
    return _transform_document_json(source, encode=True)


def _replace_xhtml_element(element) -> None:
    """Korvaa XHTML-näyttömuodon tyhjällä divillä sen esitystavan mukaisesti."""
    representation = element.getAttribute("representation").upper()
    structured = any(child.nodeType == Node.ELEMENT_NODE for child in element.childNodes)
    source = "" if structured else _dom_text(element).strip()
    encoded = representation == "B64" or (
        not representation and not structured and bool(source) and not source.startswith("<")
    )
    if encoded:
        _decode_base64(source, explicit=True, media_type="application/xhtml+xml")
        replaced = base64.b64encode(EMPTY_XHTML.encode("utf-8")).decode("ascii")
        _set_dom_text(element, replaced)
    elif structured or not source:
        _set_dom_text(element, "")
        div = element.ownerDocument.createElementNS(_XHTML_NAMESPACE, "div")
        div.setAttribute("xmlns", _XHTML_NAMESPACE)
        div.setAttribute("class", "soc-document")
        element.appendChild(div)
    else:
        _set_dom_text(element, EMPTY_XHTML)


def _replace_xml(source: bytes | str, label: str) -> str:
    """Korvaa XML:n kentät ja liitteet säilyttäen prefixit sekä QName-nimiavaruudet."""
    text = _to_text_helper(source)
    _parse_xml(text, label)
    # DOM säilyttää myös pelkästään xsi:type-arvossa tarvittavat xmlns-määritykset.
    tree = minidom.parseString(_utf8_xml_declaration(text).encode("utf-8"))
    try:
        pending = [(tree.documentElement, (), None)]
        while pending:
            element, parents, inherited_type = pending.pop()
            name = element.localName or element.tagName
            attributes = {key: element.getAttribute(key) for key in ("extension", "value") if element.hasAttribute(key)}
            changes, replacement_text = _xml_replacements(name, attributes, parents)
            for key, value in changes.items():
                element.setAttribute(key, value)
            if replacement_text is not None:
                _set_dom_text(element, replacement_text)
            media_type = element.getAttribute("mediaType") or inherited_type
            if element.namespaceURI == _FINLAND_NAMESPACE and name == "json":
                _replace_json_element(element)
            elif (
                element.namespaceURI in {None, HL7_NAMESPACE}
                and name == "text"
                and media_type
                and media_type.split(";", 1)[0].strip().lower() == "application/xhtml+xml"
            ):
                _replace_xhtml_element(element)
            pending.extend(
                (child, parents + (name,), media_type)
                for child in reversed(list(element.childNodes)) if child.nodeType == Node.ELEMENT_NODE
            )
        return _utf8_xml_declaration(tree.toxml(encoding="utf-8").decode("utf-8"))
    finally:
        tree.unlink()


def replace_validation_inputs(frame_source: bytes | str, document_source: bytes | str) -> tuple[str, str]:
    """Palauttaa korvatun kehyksen ja CDA:n muuttamatta alkuperäisiä syötteitä."""
    document = ensure_clinical_document_xml(document_source)
    return _replace_xml(frame_source, "Siirtokehys"), _replace_xml(document, "Asiakirja")
