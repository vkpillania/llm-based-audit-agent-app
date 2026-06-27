"""Agent 2 of 3 — Validation Agent.

Responsibility: decide whether the extracted data is trustworthy and internally
consistent before it reaches the Audit Agent. This is deliberately deterministic
(no LLM): math, completeness, dates, and duplicate checks have correct answers,
so we compute them directly. That keeps validation fast, free, and reproducible.

Checks performed:
  - Completeness:   are the key fields present?
  - Arithmetic:     do line items sum to subtotal? does subtotal + tax = total?
  - Date sanity:    invoice date not in the future; due date after invoice date.
  - Confidence:     low-confidence critical fields raise a warning.
  - Duplicate:      delegated to the existing three-layer duplicate checker.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from utils.duplicate_checker import check_duplicate
from schemas.invoices import (
    ExtractedDocument,
    Severity,
    ValidationIssue,
    ValidationResult,
)

# Fields we consider essential for an auditable expense.
KEY_FIELDS = ["vendor_name", "invoice_date", "total_amount"]

# How much arithmetic drift to tolerate (rounding on scanned docs).
MONEY_TOLERANCE = Decimal("0.02")


class ValidationAgent:
    def __init__(self, money_tolerance: Decimal = MONEY_TOLERANCE):
        self.tol = money_tolerance

    def run(
        self, extracted: ExtractedDocument, db: Session, file_hash: str
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        
        issues += self._check_completeness(extracted)
        issues += self._check_arithmetic(extracted)
        issues += self._check_dates(extracted)
        issues += self._check_confidence(extracted)

        duplicate = check_duplicate(db, extracted, file_hash)
        if duplicate.is_duplicate:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE",
                    severity=Severity.error,
                    message=duplicate.reason or "Duplicate of an existing record.",
                )
            )

        present = sum(
            1 for f in KEY_FIELDS if getattr(extracted, f) not in (None, "")
        )
        completeness = present / len(KEY_FIELDS)

        is_valid = not any(i.severity == Severity.error for i in issues)

        return ValidationResult(
            is_valid=is_valid,
            completeness_score=round(completeness, 2),
            issues=issues,
            duplicate=duplicate,
        )

    # -- individual checks ---------------------------------------------------

    def _check_completeness(self, doc: ExtractedDocument) -> list[ValidationIssue]:
        out = []
        import pprint
        pprint.pprint(doc)
        pprint.pprint(KEY_FIELDS)
        for f in KEY_FIELDS:
            # if not doc.get(f) :
            if getattr(doc, f) in (None, ""):
                out.append(
                    ValidationIssue(
                        code="MISSING_FIELD",
                        severity=Severity.error,
                        message=f"Required field '{f}' is missing.",
                        field=f,
                    )
                )
        return out

    def _check_arithmetic(self, doc: ExtractedDocument) -> list[ValidationIssue]:
        out = []

        # Line items should sum to the subtotal (when we have both).
        if doc.line_items and doc.subtotal is not None:
            line_sum = sum(
                (li.total for li in doc.line_items if li.total is not None),
                Decimal("0"),
            )
            if abs(line_sum - Decimal(doc.subtotal)) > self.tol:
                out.append(
                    ValidationIssue(
                        code="MATH_SUBTOTAL_MISMATCH",
                        severity=Severity.warning,
                        message=(
                            f"Line items sum to {line_sum} but subtotal is "
                            f"{doc.subtotal}."
                        ),
                        field="subtotal",
                    )
                )

        # subtotal + tax should equal total (when we have all three).
        if (
            doc.subtotal is not None
            and doc.tax is not None
            and doc.total_amount is not None
        ):
            expected = Decimal(doc.subtotal) + Decimal(doc.tax)
            if abs(expected - Decimal(doc.total_amount)) > self.tol:
                out.append(
                    ValidationIssue(
                        code="MATH_TOTAL_MISMATCH",
                        severity=Severity.error,
                        message=(
                            f"Subtotal + tax = {expected} but total is "
                            f"{doc.total_amount}."
                        ),
                        field="total_amount",
                    )
                )

        # A negative or zero total is suspicious.
        if doc.total_amount is not None and Decimal(doc.total_amount) <= 0:
            out.append(
                ValidationIssue(
                    code="NON_POSITIVE_TOTAL",
                    severity=Severity.error,
                    message=f"Total amount is {doc.total_amount}.",
                    field="total_amount",
                )
            )
        return out

    def _check_dates(self, doc: ExtractedDocument) -> list[ValidationIssue]:
        out = []
        today = date.today()
        if doc.invoice_date and doc.invoice_date > today:
            out.append(
                ValidationIssue(
                    code="FUTURE_INVOICE_DATE",
                    severity=Severity.error,
                    message=f"Invoice date {doc.invoice_date} is in the future.",
                    field="invoice_date",
                )
            )
        if doc.invoice_date and doc.due_date and doc.due_date < doc.invoice_date:
            out.append(
                ValidationIssue(
                    code="DUE_BEFORE_INVOICE",
                    severity=Severity.warning,
                    message="Due date is before the invoice date.",
                    field="due_date",
                )
            )
        return out

    def _check_confidence(self, doc: ExtractedDocument) -> list[ValidationIssue]:
        out = []
        c = doc.confidence
        if c.amount.value == "low":
            out.append(
                ValidationIssue(
                    code="LOW_CONFIDENCE_AMOUNT",
                    severity=Severity.warning,
                    message="OCR confidence on the amount is low; verify manually.",
                    field="total_amount",
                )
            )
        if c.invoice_number.value == "low":
            out.append(
                ValidationIssue(
                    code="LOW_CONFIDENCE_INVOICE_NUMBER",
                    severity=Severity.info,
                    message="OCR confidence on the invoice number is low.",
                    field="invoice_number",
                )
            )
        return out

if __name__ == "__main__":
    invoice_data = {'bill_to': 'Test Business, 123 Somewhere St, Melbourne, VIC 3000',
 'confidence': {'amount': 'high', 'date': 'high', 'invoice_number': 'high'},
 'currency': 'USD',
 'document_type': 'invoice',
 'due_date': '2016-01-31',
 'invoice_date': '2016-01-25',
 'invoice_number': 'INV-3337',
 'line_items': [{'description': 'Web Design',
                 'quantity': 1.0,
                 'total': 85.0,
                 'unit_price': 85.0}],
 'notes': None,
 'payment_terms': 'Payment is due within 30 days from date of invoice. Late '
                  'payment is subject to fees of 5% per month.',
 'po_number': '12345',
 'subtotal': 85.0,
 'tax': 8.5,
 'total_amount': 93.5,
 'vendor_address': 'Suite 5A-1204, 123 Somewhere Street, Your City AZ 12345',
 'vendor_name': 'DEMO - Sliced Invoices'}
    data = ExtractedDocument(**invoice_data)
    # user = ExtractedDocument.model_validate(invoice_data)
    import hashlib
    with open("invoices.pdf", "rb") as f:
        file_bytes = f.read()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        from database.session import SessionLocal
        db: Session = SessionLocal()
        print("db ===" , db)
        validator = ValidationAgent()
        validator.run(extracted=data, db=db, file_hash=file_hash)