"""Paikalliset XML- ja tietosuojatarkistukset ilman käyttöliittymää tai verkkoa."""

from __future__ import annotations

import base64
import binascii
import codecs
from collections import deque
from datetime import date
import html
import json
import re
import uuid
import xml.etree.ElementTree as ET
from xml.parsers import expat
from xml.sax.saxutils import quoteattr


HL7_NAMESPACE = "urn:hl7-org:v3"
HETU_CHECK_CHARS = "0123456789ABCDEFHJKLMNPRSTUVWXY"
SERVICE_REASON_CODE_SYSTEM = "1.2.246.537.6.1503.201601"

# Rajat koskevat myös sisäkkäisiä liitteitä. Rajan ylitys pysäyttää lähetyksen.
MAX_INPUT_CHARS = 20_000_000
MAX_DECODED_BYTES = 20_000_000
MAX_EXPANDED_CHARS = 80_000_000
MAX_BASE64_BLOCKS = 256
MAX_DECODE_DEPTH = 6
MAX_SEGMENTS = 100_000

_CENTURIES = {"+": 1800, **dict.fromkeys("-YXVWU", 1900), **dict.fromkeys("ABCDEF", 2000)}
_HETU_PATTERN = re.compile(
    rf"([0-9]{{2}})([0-9]{{2}})([0-9]{{2}})([-+ABCDEFYXVWU])"
    rf"([0-9]{{3}})([{HETU_CHECK_CHARS}])", re.IGNORECASE | re.ASCII
)
_HETU_SEARCH = re.compile(
    rf"(?<!\w){_HETU_PATTERN.pattern}(?!\w)", re.IGNORECASE | re.ASCII
)
_XML_DECLARATION = re.compile(r"^\s*<\?xml\b[^?]*\?>", re.IGNORECASE)
_ENCODING_ATTRIBUTE = re.compile(r"\bencoding\s*=\s*(['\"])([^'\"]+)\1", re.IGNORECASE)
_BASE64_RUN = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{16,}={0,2}(?![A-Za-z0-9+/_=-])")
# Kommentit ja käsittelyohjeet ovat sallittuja XML-juuren edellä.
_XML_START = re.compile(r"^<")


def _declared_encoding(text: str) -> str | None:
    """Lukee XML-ilmoituksen merkistön, jos ilmoitus sisältää encoding-attribuutin."""
    declaration = _XML_DECLARATION.match(text)
    if declaration:
        match = _ENCODING_ATTRIBUTE.search(declaration.group(0))
        if match:
            return match.group(2)
    return None


def _to_text_helper(source: bytes | str) -> str:
    """Purkaa tavut BOMin/XML-ilmoituksen mukaan; ei arvaa Latin-1-merkistöä."""
    if isinstance(source, str):
        return source.removeprefix("\ufeff")
    if not isinstance(source, bytes):
        raise TypeError("XML-syötteen on oltava tekstiä tai tavuja.")
    if len(source) > MAX_INPUT_CHARS * 4:
        raise ValueError("Syöte ylittää paikallisen käsittelyn kokorajan.")

    encoding = None
    if source.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif source.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    elif source.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif source.startswith(b"\x00\x00\x00<"):
        encoding = "utf-32-be"
    elif source.startswith(b"<\x00\x00\x00"):
        encoding = "utf-32-le"
    elif len(source) >= 4:
        sample = source[:128]
        # UTF-16:n ASCII-merkkien nollatavujen paikat erottavat tavujärjestyksen.
        pairs = len(sample) // 2
        even_zeroes = sum(value == 0 for value in sample[: pairs * 2 : 2])
        odd_zeroes = sum(value == 0 for value in sample[1 : pairs * 2 : 2])
        if pairs and odd_zeroes / pairs >= 0.6 and even_zeroes / pairs < 0.2:
            encoding = "utf-16-le"
        elif pairs and even_zeroes / pairs >= 0.6 and odd_zeroes / pairs < 0.2:
            encoding = "utf-16-be"

    if encoding is None:
        # XML-ilmoitus on ASCII-yhteensopivissa merkistöissä luettavissa sellaisenaan.
        prefix = source[:512].decode("ascii", errors="ignore")
        encoding = _declared_encoding(prefix) or "utf-8"
    try:
        text = source.decode(encoding)
    except (UnicodeError, LookupError) as error:
        raise ValueError(f"Syötteen merkistöä ei voida purkaa ({encoding}).") from error

    declared = _declared_encoding(text)
    if declared:
        try:
            declared_codec = codecs.lookup(declared).name
            actual_codec = codecs.lookup(encoding).name
        except LookupError as error:
            raise ValueError(f"Tuntematon XML-merkistö: {declared}.") from error
        compatible = declared_codec == actual_codec
        compatible |= actual_codec == "utf-8-sig" and declared_codec == "utf-8"
        compatible |= actual_codec.startswith("utf-16") and declared_codec == "utf-16"
        compatible |= actual_codec.startswith("utf-32") and declared_codec == "utf-32"
        if actual_codec in {"utf-16", "utf-32"}:
            endian = "le" if source.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF32_LE)) else "be"
            compatible |= declared_codec == f"{actual_codec}-{endian}"
        if not compatible:
            raise ValueError("XML:n merkistöilmoitus ei vastaa syötteen tavukoodausta.")
    return text.removeprefix("\ufeff")


def is_valid_finnish_hetu(hetu: str) -> bool:
    """Tarkistaa muodon, todellisen kalenteripäivän, yksilönumeron ja tarkistusmerkin."""
    if not isinstance(hetu, str):
        return False
    match = _HETU_PATTERN.fullmatch(hetu)
    if not match:
        return False
    day, month, year, century, individual, check_char = match.groups()
    if int(individual) < 2:
        return False
    try:
        date(_CENTURIES[century.upper()] + int(year), int(month), int(day))
    except ValueError:
        return False
    number = int(day + month + year + individual)
    return HETU_CHECK_CHARS[number % 31] == check_char.upper()


def _parse_xml(text: str, label: str = "XML") -> ET.Element:
    """Jäsentää kokorajan alittavan XML:n ja estää DTD- ja ENTITY-määrittelyt."""
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"{label} ylittää paikallisen käsittelyn kokorajan.")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ValueError(f"{label}: DTD- ja ENTITY-määrittelyjä ei tueta paikallisessa tarkistuksessa.")
    try:
        return ET.fromstring(text)
    except (ET.ParseError, ValueError) as error:
        raise ValueError(f"{label} ei ole kelvollista XML:ää: {error}") from error


def _local_name(name: str) -> str:
    """Palauttaa elementin tai attribuutin paikallisen nimen ilman nimiavaruutta."""
    return name.rsplit("}", 1)[-1]


def _unwrap_cdata(text: str) -> str:
    """Purkaa vain kokonaisen CDATA-/escaped-XML-kääreen, ei XML:n arvoja."""
    current = text.strip()
    for _ in range(3):
        if current.startswith("<![CDATA[") and current.endswith("]]>"):
            return current[9:-3].strip()
        if current.startswith(("&lt;", "&amp;lt;")):
            unescaped = html.unescape(current)
            if unescaped != current:
                current = unescaped
                continue
        break
    return current


def _compact_base64(candidate: str) -> str:
    """Yhtenäistää Base64-merkit ja rivinvaihdot sekä tarkistaa täytteen."""
    candidate = candidate.replace(r"\r\n", "").replace(r"\n", "").replace(r"\r", "")
    compact = "".join(candidate.split()).replace("-", "+").replace("_", "/")
    if not compact or not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", compact):
        raise ValueError("Base64-sisältö sisältää virheellisiä merkkejä tai täytteen.")
    unpadded = compact.rstrip("=")
    if len(unpadded) % 4 == 1:
        raise ValueError("Base64-sisällön pituus on virheellinen.")
    required_padding = (-len(unpadded)) % 4
    if "=" in compact and len(compact) - len(unpadded) != required_padding:
        raise ValueError("Base64-sisällön täyte on virheellinen.")
    return unpadded + "=" * required_padding


def _decode_base64(candidate: str, *, explicit: bool, media_type: str | None = None) -> str | None:
    """Purkaa tekstiliitteen ja nostaa virheen vain nimenomaisesti merkityistä liitteistä."""
    try:
        if media_type:
            mime = media_type.split(";", 1)[0].strip().lower()
            textual = mime.startswith("text/") or mime in {"application/xml", "application/json"}
            textual |= mime.endswith(("+xml", "+json"))
            if not textual:
                raise ValueError(f"Binääriliitteen ({media_type}) henkilötunnuksia ei voida tarkistaa paikallisesti.")
        normalized = _compact_base64(candidate)
        if len(normalized) > ((MAX_DECODED_BYTES + 2) // 3) * 4:
            raise ValueError("Base64-liite ylittää paikallisen käsittelyn kokorajan.")
        try:
            decoded = base64.b64decode(normalized, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Base64-sisältöä ei voida purkaa.") from error
        if base64.b64encode(decoded).decode("ascii") != normalized:
            raise ValueError("Base64-sisällön täytebitit ovat virheelliset.")
        if decoded.startswith((b"%PDF-", b"PK\x03\x04", b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"\x1f\x8b")):
            raise ValueError("Binääriliitteen henkilötunnuksia ei voida tarkistaa paikallisesti.")
        text = _to_text_helper(decoded)
        if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
            raise ValueError("Binääriliitteen henkilötunnuksia ei voida tarkistaa paikallisesti.")
        return text
    except ValueError as error:
        if explicit:
            raise ValueError(f"Base64-liitteen tietosuojatarkistus ei onnistunut: {error}") from error
        return None


def _json_strings(value):
    """Iteroi JSON-arvot ilman Python-rekursiota; tunnistaa nimetyt Base64-kentät."""
    pending = [(value, False)]
    while pending:
        item, explicit = pending.pop()
        if isinstance(item, str):
            yield item, explicit
        elif isinstance(item, dict):
            encoded_object = any(
                key.lower() in {"representation", "encoding"} and isinstance(val, str) and val.upper() in {"B64", "BASE64"}
                for key, val in item.items()
            )
            for key, val in item.items():
                normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
                is_b64 = normalized_key in {"b64", "base64"} or normalized_key.endswith("base64")
                is_b64 |= encoded_object and normalized_key in {"value", "data", "text", "content"}
                if encoded_object and normalized_key in {"representation", "encoding"}:
                    continue
                yield key, False
                pending.append((val, is_b64))
        elif isinstance(item, list):
            pending.extend((child, explicit) for child in item)


def _xml_elements_with_media_type(root: ET.Element):
    """Käy XML-elementit läpi ja välittää ylemmältä tasolta perityn mediatyypin."""
    pending = [(root, None)]
    while pending:
        element, inherited_type = pending.pop()
        attrs = {_local_name(key).lower(): value for key, value in element.attrib.items()}
        media_type = attrs.get("mediatype", inherited_type)
        yield element, media_type
        pending.extend((child, media_type) for child in reversed(list(element)))


def _collect_texts(source: str) -> tuple[list[str], list[str]]:
    """Kerää tarkistettavat tekstit ja puretut liitteet XML:stä, JSONista ja Base64:stä."""
    if not isinstance(source, str):
        raise TypeError("Tarkistettavan sisällön on oltava tekstiä.")
    if len(source) > MAX_INPUT_CHARS:
        raise ValueError("Syöte ylittää paikallisen käsittelyn kokorajan.")
    pending = deque([(source, 0)])
    seen = set()
    decoded_seen = set()
    texts, decoded_texts = [], []
    expanded_chars = 0
    decoded_blocks = 0

    def add_decoded(candidate: str, depth: int, explicit: bool, media_type: str | None = None):
        """Lisää uuden puretun liitteen jonoon määrä- ja syvyysrajojen puitteissa."""
        nonlocal decoded_blocks
        key = (candidate, explicit, media_type)
        if key in decoded_seen:
            return
        decoded_seen.add(key)
        decoded = _decode_base64(candidate, explicit=explicit, media_type=media_type)
        if decoded is None:
            return
        # Sama onnistuneesti purettu arvo ei vie rajaa uudestaan yleisessä haussa.
        decoded_seen.add((candidate, not explicit, media_type))
        decoded_seen.add((candidate, False, None))
        if depth >= MAX_DECODE_DEPTH:
            raise ValueError("Sisäkkäisen Base64-sisällön tarkistus ylittää sallitun syvyyden.")
        decoded_blocks += 1
        if decoded_blocks > MAX_BASE64_BLOCKS:
            raise ValueError("Base64-liitteiden määrä ylittää paikallisen käsittelyn rajan.")
        decoded_texts.append(decoded)
        pending.append((decoded, depth + 1))

    while pending:
        text, depth = pending.popleft()
        if text in seen:
            continue
        seen.add(text)
        expanded_chars += len(text)
        if expanded_chars > MAX_EXPANDED_CHARS or len(seen) > MAX_SEGMENTS:
            raise ValueError("Purettu sisältö ylittää paikallisen käsittelyn rajan.")
        texts.append(text)
        trimmed = text.strip()
        unwrapped = _unwrap_cdata(trimmed)
        if unwrapped != trimmed:
            pending.append((unwrapped, depth))
        if _XML_START.match(trimmed):
            try:
                root = _parse_xml(trimmed, "Sisäinen XML")
            except ValueError:
                # Tavallinen tekstiarvo saa sisältää XML:n näköisiä merkkejä.
                # DTD ei kuitenkaan saa päätyä käsittelemättömäksi liitteeksi.
                if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", trimmed, re.IGNORECASE):
                    raise
            else:
                for element, media_type in _xml_elements_with_media_type(root):
                    attrs = {_local_name(key).lower(): value for key, value in element.attrib.items()}
                    explicit = attrs.get("representation", "").upper() in {"B64", "BASE64"}
                    explicit |= attrs.get("encoding", "").upper() in {"B64", "BASE64"}
                    explicit |= _local_name(element.tag).lower() in {"b64", "base64"}
                    # nonXMLBody voi sisältää raakaliitteen tai CDA:n text-elementin.
                    explicit |= _local_name(element.tag).lower() == "nonxmlbody" and not list(element)
                    if explicit:
                        value = "".join(element.itertext()).strip()
                        if not value:
                            raise ValueError("Base64-liite on tyhjä; tietosuojatarkistusta ei voida tehdä.")
                        add_decoded(value, depth, True, media_type)
                    for value in element.attrib.values():
                        pending.append((value, depth))
                    if element.text:
                        pending.append((element.text, depth))
                    if element.tail:
                        pending.append((element.tail, depth))
        if trimmed.startswith(("{", "[", '"')):
            try:
                value = json.loads(trimmed)
            except RecursionError as error:
                raise ValueError("JSON-sisällön rakenne on liian syvä paikalliseen tarkistukseen.") from error
            except ValueError:
                pass
            else:
                for value, explicit in _json_strings(value):
                    pending.append((value, depth))
                    if explicit:
                        add_decoded(value, depth, True)
        # Kokonainen arvo kattaa myös lyhyet hetut ja rivitetyn Base64:n.
        if len(trimmed) >= 16:
            add_decoded(trimmed, depth, False)
        for match in _BASE64_RUN.finditer(text):
            add_decoded(match.group(0), depth, False)
    return texts, list(dict.fromkeys(decoded_texts))


def extract_base64_contents(xml_text: str) -> list[str]:
    """Palauttaa myös sisäkkäisten XML/JSON-liitteiden puretut tekstisisällöt."""
    return _collect_texts(xml_text)[1]


def find_non_test_hetus(xml_text: str) -> list[str]:
    """Etsii hetut myös jäsennetyistä arvoista; 9xx-yksilönumerot ovat testitunnuksia."""
    found = set()
    for text in _collect_texts(xml_text)[0]:
        for match in _HETU_SEARCH.finditer(text):
            hetu = match.group(0).upper()
            if hetu[7] != "9" and is_valid_finnish_hetu(hetu):
                found.add(hetu)
    return sorted(found)


def _clinical_documents(root: ET.Element, *, frame: bool = False) -> list[ET.Element]:
    """Etsii HL7-asiakirjat ja hyväksyy siirtokehyksessä myös clinicalDocument-nimen."""
    names = {f"{{{HL7_NAMESPACE}}}ClinicalDocument"}
    if frame:
        names.add(f"{{{HL7_NAMESPACE}}}clinicalDocument")
    return [element for element in root.iter() if element.tag in names]


def _serialise_embedded(document: ET.Element, source: str) -> str:
    """Poimii sisäisen asiakirjan säilyttäen alkuperäiset nimiavaruudet ja etuliitteet."""
    # Säilytetään alkuperäiset prefixit, QName-arvot ja sisäiset xmlns=""-rajat.
    # ElementTree-sarjoitus kadottaisi osan näistä nimialuesuhteista.
    expected = document.tag.removeprefix("{")
    encoded = _utf8_xml_declaration(source).encode("utf-8")
    parser = expat.ParserCreate(namespace_separator="}")
    scopes, pending_ns, scope = [], [], {}
    start, end, target_depth = None, None, None
    inherited = {}

    def on_namespace(prefix, uri):
        """Kerää seuraavan aloituselementin nimiavaruusmääritykset."""
        pending_ns.append((prefix or "", uri or ""))

    def on_start(name, _attrs):
        """Päivittää nimiavaruuspinon ja tallentaa poimittavan asiakirjan aloituskohdan."""
        nonlocal scope, start, target_depth, inherited
        scopes.append(scope)
        local_namespaces = dict(pending_ns)
        scope = {**scope, **local_namespaces}
        pending_ns.clear()
        if name == expected and start is None:
            start = parser.CurrentByteIndex
            target_depth = len(scopes)
            inherited = {prefix: uri for prefix, uri in scope.items() if prefix not in local_namespaces}

    def on_end(name):
        """Tallentaa asiakirjan loppukohdan ja palauttaa ylemmän tason nimiavaruudet."""
        nonlocal scope, end
        if name == expected and len(scopes) == target_depth:
            index = parser.CurrentByteIndex
            # Tyhjän elementin lopputapahtuma osoittaa jo '/>'-merkkien jälkeen.
            end = encoded.find(b">", index) + 1 if encoded[index : index + 2] == b"</" else index
        scope = scopes.pop()

    parser.StartNamespaceDeclHandler = on_namespace
    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    try:
        parser.Parse(encoded, True)
    except expat.ExpatError as error:
        raise ValueError(f"ClinicalDocumentia ei voida poimia: {error}") from error
    if start is None or end is None:
        raise ValueError("ClinicalDocumentia ei voida poimia siirtävästä XML-rakenteesta.")
    extracted = encoded[start:end].decode("utf-8")
    quote = None
    insertion = None
    for index, char in enumerate(extracted):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == ">":
            insertion = index - 1 if extracted[index - 1] == "/" else index
            break
    if insertion is None:
        raise ValueError("ClinicalDocumentin aloituselementti on virheellinen.")
    additions = [
        f" xmlns{':' + prefix if prefix else ''}={quoteattr(uri)}"
        for prefix, uri in inherited.items() if prefix != "xml"
    ]
    extracted = extracted[:insertion] + "".join(additions) + extracted[insertion:]
    _parse_xml(extracted, "Poimittu ClinicalDocument")
    return extracted


def ensure_clinical_document_xml(source: bytes | str, source_filename: str | None = None, verbose: bool = True) -> str:
    """Varmistaa yhden HL7 ClinicalDocumentin; tukee XML-, CDATA- ja Base64-syötteitä."""
    del verbose  # Säilytetään vanhan käyttöliittymän kutsurajapinta.
    text = _to_text_helper(source)
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError("Asiakirja ylittää paikallisen käsittelyn kokorajan.")
    candidate = _unwrap_cdata(text)
    if candidate == text.strip():
        candidate = text
    if candidate.lstrip().startswith("<"):
        root = _parse_xml(candidate, "Asiakirja")
        documents = _clinical_documents(root)
        if len(documents) > 1:
            raise ValueError("Asiakirja sisältää useita ClinicalDocument-elementtejä; valinta ei ole yksiselitteinen.")
        if len(documents) == 1:
            if documents[0] is root:
                return candidate
            return _serialise_embedded(documents[0], candidate)
        if _local_name(root.tag).lower() == "clinicaldocument":
            raise ValueError("Asiakirjan juuren on oltava ClinicalDocument nimialueessa urn:hl7-org:v3.")
    else:
        decoded = _decode_base64(candidate, explicit=True)
        candidate = decoded if decoded is not None else candidate

    candidates = []
    for nested in _collect_texts(candidate)[0]:
        unwrapped = _unwrap_cdata(nested)
        if not _XML_START.match(unwrapped):
            continue
        try:
            root = _parse_xml(unwrapped, "Purettu asiakirja")
        except ValueError:
            # XML-kääreen tavallinen tekstiarvo voi alkaa esimerkiksi '<'-merkillä.
            continue
        documents = _clinical_documents(root)
        if len(documents) > 1:
            raise ValueError("Purettu sisältö sisältää useita ClinicalDocument-elementtejä.")
        if documents:
            document = unwrapped if documents[0] is root else _serialise_embedded(documents[0], unwrapped)
            if document not in candidates:
                candidates.append(document)
    if len(candidates) > 1:
        raise ValueError("Syöte sisältää useita ClinicalDocument-asiakirjoja; valinta ei ole yksiselitteinen.")
    if candidates:
        return candidates[0]
    suffix = f" ({source_filename})" if source_filename else ""
    raise ValueError(f"Kelvollista HL7 ClinicalDocument-asiakirjaa ei löytynyt{suffix}.")


def extract_clinical_doc_identifiers(xml_text: str) -> tuple[str | None, str | None]:
    """Lukee yhden HL7 ClinicalDocument/clinicalDocument-elementin suorat tunnisteet."""
    try:
        root = _parse_xml(xml_text)
    except ValueError:
        return None, None
    documents = _clinical_documents(root, frame=True)
    if len(documents) != 1:
        return None, None
    identifiers = []
    for name in ("id", "setId"):
        children = documents[0].findall(f"{{{HL7_NAMESPACE}}}{name}")
        value = children[0].get("root", "").strip() if len(children) == 1 else ""
        identifiers.append(value or None)
    return tuple(identifiers)


def _utf8_xml_declaration(text: str) -> str:
    """Muodostaa palvelun edellyttämän XML-ilmoituksen ja LF-rivinvaihdot.

    SHARK palauttaa ilmoituksettomalle asiakirjalle HTML-estosivun. CRLF taas
    voi aiheuttaa virheellisen ilmoituksen puuttuvasta nonXMLBody-sisällöstä.
    Rivinvaihtojen muunnos vastaa XML:n normaalia rivinvaihtonormalisointia.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    declaration = _XML_DECLARATION.match(text)
    if not declaration:
        return '<?xml version="1.0" encoding="UTF-8"?>' + text
    updated = _ENCODING_ATTRIBUTE.sub(lambda match: f"encoding={match.group(1)}UTF-8{match.group(1)}", declaration.group(0))
    return updated + text[declaration.end() :]


def prepare_validation_request(frame: bytes | str, document: bytes | str) -> tuple[dict, list[tuple[str, str]]]:
    """Valmistelee lähetyksen vain kelvollisen XML:n ja kaikkien paikallisten tarkistusten jälkeen."""
    frame_text = _to_text_helper(frame)
    document_text = _to_text_helper(document)
    frame_root = _parse_xml(frame_text, "Siirtokehys")
    document_xml = ensure_clinical_document_xml(document_text)

    found = set()
    for text in (frame_text, document_text, document_xml):
        found.update(find_non_test_hetus(text))
    if found:
        raise ValueError("\n\nVarmista tietosuoja ennen pyynnön lähettämistä eteenpäin.\n\nLähetys pysäytettiin: aineisto sisältää oikeita henkilötunnuksia: " + ", ".join(sorted(found)))

    frame_ids = extract_clinical_doc_identifiers(frame_text)
    document_ids = extract_clinical_doc_identifiers(document_xml)
    checks = []
    for label, frame_id, document_id in zip(("id", "setId"), frame_ids, document_ids):
        if not frame_id or not document_id:
            raise ValueError(f"ClinicalDocument {label}/@root puuttuu siirtokehyksestä tai asiakirjasta.")
        if frame_id != document_id:
            raise ValueError(f"ClinicalDocument {label}/@root ei täsmää: kehys '{frame_id}', asiakirja '{document_id}'.")
        checks.append((label, document_id))

    reasons = [
        reason
        for control in frame_root.iter(f"{{{HL7_NAMESPACE}}}controlActProcess")
        for reason in control.findall(f"{{{HL7_NAMESPACE}}}reasonCode")
    ]
    service_reasons = [
        reason for reason in reasons
        if reason.get("codeSystem", "").strip() == SERVICE_REASON_CODE_SYSTEM
    ]
    """Minimaalinen kehys voi jättää codeSystemin pois. Usean koodin kehyksessä
    palvelupyyntö erotetaan käyttötarkoituksesta sen koodistotunnisteella. 
    """
    if not service_reasons and len(reasons) == 1 and not reasons[0].get("codeSystem", "").strip():
        service_reasons = reasons
    if len(service_reasons) != 1 or not service_reasons[0].get("code", "").strip():
        raise ValueError("Siirtokehyksen HL7 controlActProcess/reasonCode/@code puuttuu, on tyhjä tai ei ole yksiselitteinen.")
    reason_code = service_reasons[0].get("code").strip()

    payload = {
        "messageId": str(uuid.uuid4()),
        "palveluPyynto": reason_code,
        "level": "1",
        "siirtokehysXml": _utf8_xml_declaration(frame_text),
        "asiakirjaXml": _utf8_xml_declaration(document_xml),
    }
    return payload, checks
