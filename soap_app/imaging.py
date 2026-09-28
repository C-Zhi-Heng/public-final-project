"""X-ray image analysis via MedGemma running locally through Ollama."""

import ollama

from soap_app.config import MEDGEMMA_MODEL

XRAY_PROMPT = (
    "You are a radiologist. Write a report for this X-ray image using standard radiology report style, "
    "in exactly two labelled sections:\n"
    "FINDINGS: factual, itemized observations (lung fields, cardiac silhouette, mediastinum, bony structures, "
    "pleura, and any other notable findings). Use terse clinical phrasing, not conversational prose.\n"
    "IMPRESSION: a concise 1-2 sentence summary of the clinically significant conclusion(s).\n"
    "Do not include any other sections or commentary."
)


def analyze_xray(image_path: str) -> str:
    """Sends the X-ray image at image_path to MedGemma and returns its text description."""
    response = ollama.chat(
        model=MEDGEMMA_MODEL,
        messages=[{"role": "user", "content": XRAY_PROMPT, "images": [image_path]}],
        options={"temperature": 0.0},  # deterministic output for consistent clinical wording
    )
    return response["message"]["content"]
