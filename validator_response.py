"""Tulkitsee validointipalvelun vastaukset ilman verkko- tai Streamlit-riippuvuuksia."""

from dataclasses import dataclass
import json
import re


@dataclass(frozen=True)
class ValidationResult:
    """Sisältää näytettävän tuloksen ja alkuperäisen vastauksen lataustiedot."""

    status_code: int
    success: bool
    message: str
    response_text: str
    response_filename_suffix: str
    response_mime: str
    response_label: str
    formatted_description: str | None


def _format_description(description: str, reason_code: str) -> str | None:
    """Säilyttää rivinvaihdot ja erottaa puolipisteellä erotellut virheet omille riveilleen."""
    description = description.replace("\r\n", "\n").replace("\r", "\n")
    description = re.sub(r"[ \t]+", " ", description)
    description = re.sub(r";[ \t]*(?:\n[ \t]*)*", ";\n", description)
    description = re.sub(r"[ \t]+(?=\d+:\s)", "\n", description)
    description = "\n".join(line.strip() for line in description.splitlines()).strip()
    if not description:
        return None
    return f"{description}\n\nPalvelupyyntö: {reason_code}"


def interpret_response(
    status_code: int, content_type: str, body: str, reason_code: str
) -> ValidationResult:
    """Tulkitsee vastauksen turvallisesti ja säilyttää sen näyttämistä ja lataamista varten.

    Onnistuneen validoinnin vastaus on ohjeen mukaan tyhjä HTTP 200 -vastaus.
    HTML palautetaan tekstinä, ja kutsujan on näytettävä se tekstinä.
    Kelvollinen JSON muotoillaan juurityypistä ja ilmoitetusta mediatyypistä riippumatta.
    """
    if status_code == 200 and not body.strip():
        return ValidationResult(
            status_code=status_code,
            success=True,
            message="Sanoma validoitu onnistuneesti.",
            response_text=body,
            response_filename_suffix="response.txt",
            response_mime="text/plain",
            response_label="Lataa vastaus (.txt)",
            formatted_description=None,
        )

    if status_code == 200:
        message = (
            "Palvelin palautti odottamattoman vastauksen (tilakoodi 200). "
            "Onnistuneen validoinnin vastauksen pitäisi olla tyhjä."
        )
    else:
        message = f"Virhe pyynnössä (tilakoodi {status_code})."

    media_type = content_type.partition(";")[0].strip().lower()
    if media_type in {"text/html", "application/xhtml+xml"}:
        html_message = f"{message} Palvelin palautti HTML-sisällön."
        if (
            re.search(r"<title>\s*Request\s+Rejected\s*</title>", body, re.IGNORECASE)
            and "the requested url was rejected" in body.lower()
        ):
            html_message = (
                f"Palvelu palautti pyynnön estävän Request Rejected -sivun "
                f"(HTTP {status_code})."
            )
            support_id = re.search(r"Your\s+support\s+ID\s+is:\s*([0-9]+)", body, re.IGNORECASE)
            if support_id:
                html_message += f" Tukitunniste: {support_id.group(1)}."
        return ValidationResult(
            status_code=status_code,
            success=False,
            message=html_message,
            response_text=body,
            response_filename_suffix="html_response.html",
            response_mime="text/html",
            response_label="Lataa HTML-vastaus",
            formatted_description=None,
        )

    try:
        decoded = json.loads(body)
    except (ValueError, RecursionError):
        return ValidationResult(
            status_code=status_code,
            success=False,
            message=message,
            response_text=body,
            response_filename_suffix="error_response.txt",
            response_mime="text/plain",
            response_label="Lataa vastaus (.txt)",
            formatted_description=None,
        )

    formatted_description = None
    if isinstance(decoded, dict) and isinstance(decoded.get("description"), str):
        formatted_description = _format_description(decoded["description"], reason_code)

    try:
        response_text = json.dumps(decoded, indent=4, ensure_ascii=False)
    except (ValueError, RecursionError):
        # Säilytetään jäsennettävä vastaus, vaikka muotoilu ylittäisi käsittelyn rajat.
        response_text = body

    return ValidationResult(
        status_code=status_code,
        success=False,
        message=message,
        response_text=response_text,
        response_filename_suffix="error_response.json",
        response_mime="application/json",
        response_label="Lataa alkuperäinen JSON-vastaus",
        formatted_description=formatted_description,
    )
