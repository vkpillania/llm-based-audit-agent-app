"""Agent 1 of 3 — OCR Agent.

Responsibility: turn a raw file (PDF, scan, or photo) into a structured
ExtractedDocument. This is the only agent that looks at the original document;
everything downstream works from its structured output.

Uses Claude's vision capability, so it handles arbitrary invoice and
reimbursement-form layouts without per-vendor templates.
"""

from __future__ import annotations

import base64
import json
import os
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
import dotenv
from core.config import settings

dotenv.load_dotenv()

# from ..schemas import ExtractedDocument
OCR_MODEL = os.getenv("OCR_MODEL", "gpt-4o-mini")

EXTRACTION_SCHEMA = """{
  "document_type": "invoice | reimbursement | unknown",
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "vendor_name": "string or null",
  "vendor_address": "string or null",
  "bill_to": "string or null",
  "po_number": "string or null",
  "payment_terms": "string or null",
  "line_items": [
    {"description": "string", "quantity": number, "unit_price": number, "total": number}
  ],
  "subtotal": number or null,
  "tax": number or null,
  "total_amount": number or null,
  "currency": "ISO 4217 code, default USD",
  "notes": "string or null",
  "confidence": {
    "invoice_number": "high | medium | low",
    "amount": "high | medium | low",
    "date": "high | medium | low"
  }
}"""

SYSTEM_PROMPT = (
    "You are a precise OCR and data-extraction agent for accounts payable. "
    "You read invoices and employee reimbursement forms and return structured data. "
    "Extract only what is actually present. If a field is missing or illegible, "
    "return null rather than guessing. Report confidence per field based on "
    "legibility. Return ONLY a single JSON object matching the schema. No markdown, "
    "no prose, no code fences."
)


class OCRAgent:
    def __init__(self, model: str = OCR_MODEL):
        self.model = model
        self.client = ChatOpenAI(openai_api_key=settings.OPENAI_API_KEY, model=self.model, max_tokens=1500)

    @staticmethod
    def _media_type(file_name: str) -> str:
        name = file_name.lower()
        if name.endswith(".pdf"):
            return "application/pdf"
        if name.endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        if name.endswith(".webp"):
            return "image/webp"
        if name.endswith(".gif"):
            return "image/gif"
        return "image/png"

    @staticmethod
    def _strip_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        return text.strip()

    def _content(self, file_bytes: bytes, media_type: str) -> list[dict]:
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        prompt = f"Extract the fields from this document. Schema:\n{EXTRACTION_SCHEMA}"
        if media_type == "application/pdf":
            return [
                {
                    "type": "file",
                    "file": {
                        "filename": "document.pdf",
                        "file_data": f"data:application/pdf;base64,{b64}",
                    },
                },
                {"type": "text", "text": prompt},
            ]
        return [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"},
            },
            {"type": "text", "text": prompt},
        ]

    # def run(self, file_bytes: bytes, file_name: str) -> ExtractedDocument:
    def run(self, file_bytes: bytes, file_name: str) :
        """Extract structured data from one document."""
        media_type = self._media_type(file_name)
        response = self.client.invoke(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=self._content(file_bytes, media_type)),
            ]
        )
        # print(response)
        raw = response.content if isinstance(response.content, str) else str(response.content)
        try:
            data = json.loads(self._strip_fences(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"OCR agent: model did not return valid JSON: {exc}")
        # return ExtractedDocument.model_validate(data)
        return data

if __name__ =="__main__":
    ocr = OCRAgent()
    with open("invoices.pdf", "rb") as f:
        file_bytes = f.read()
        data = ocr.run(
            file_bytes=file_bytes,
            file_name="invoices.pdf",
        )
        import pprint
        pprint.pprint(data)