"""SOAP note generation via phi3:mini, combining the consultation transcript and optional X-ray analysis."""

import ollama

from soap_app.config import SOAP_MODEL

SYSTEM_PROMPT = (
    "You are a clinical assistant that writes SOAP notes (Subjective, Objective, Assessment, Plan) "
    "for a doctor from a consultation transcript and, if provided, an X-ray analysis. "
    "Only use information given to you, do not invent facts. "
    "There is exactly ONE patient and ONE transcript — never invent a second patient, transcript, or note. "
    "Reply with plain text using exactly these four labelled sections: SUBJECTIVE:, OBJECTIVE:, ASSESSMENT:, PLAN:. "
    "Stop writing immediately after the Plan section."
)


def generate_soap_note(transcript: str, xray_analysis: str | None = None) -> str:
    """Returns a plain-text SOAP note built from the transcript and optional X-ray analysis."""
    user_prompt = f"Consultation transcript:\n{transcript}\n\n"
    if xray_analysis:
        user_prompt += f"X-ray analysis:\n{xray_analysis}\n\n"
    user_prompt += "Write the SOAP note now."

    response = ollama.chat(
        model=SOAP_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        options={
            "temperature": 0.1,  # low temperature keeps the note consistent/deterministic
            "num_predict": 600,  # caps runaway generation well past one note's worth of text
            # small models can otherwise keep going and invent a second fake transcript/patient
            "stop": ["Consultation transcript:", "\nConsultation transcript"],
        },
    )
    return response["message"]["content"]
