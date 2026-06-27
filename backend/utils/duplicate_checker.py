"""Duplicate detection.

Three layers, cheapest first:

1. Exact file hash  — the identical file was uploaded before.
2. Invoice number   — same vendor + same invoice number (the canonical AP rule).
3. Fuzzy fingerprint — same vendor + same total + same date, for resubmissions
                       that lack an invoice number (common with reimbursements).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.invoice import Invoice
from schemas.invoices import DuplicateMatch, ExtractedDocument


def _norm(value: str | None) -> str | None:
    return value.strip().lower() if value else None


def check_duplicate(
    db: Session, extracted: ExtractedDocument, file_hash: str
) -> DuplicateMatch:
    # 1. Exact same bytes.
    by_hash = db.query(Invoice).filter(Invoice.file_hash == file_hash).first()
    if by_hash:
        return DuplicateMatch(
            is_duplicate=True,
            matched_invoice_id=by_hash.id,
            matched_file_name=by_hash.file_name,
            reason="Identical file already uploaded.",
        )

    vendor = _norm(extracted.vendor_name)

    # 2. Same vendor + invoice number.
    if extracted.invoice_number and vendor:
        by_number = (
            db.query(Invoice)
            .filter(
                Invoice.invoice_number == extracted.invoice_number,
                func.lower(Invoice.vendor_name) == vendor,
            )
            .first()
        )
        if by_number:
            return DuplicateMatch(
                is_duplicate=True,
                matched_invoice_id=by_number.id,
                matched_file_name=by_number.file_name,
                reason=(
                    f"Invoice #{extracted.invoice_number} from "
                    f"{extracted.vendor_name} already exists."
                ),
            )

    # 3. Fuzzy: vendor + amount + date (catches no-number resubmissions).
    if extracted.total_amount is not None and vendor and extracted.invoice_date:
        amount = Decimal(extracted.total_amount)
        by_fingerprint = (
            db.query(Invoice)
            .filter(
                func.lower(Invoice.vendor_name) == vendor,
                Invoice.total_amount == amount,
                Invoice.invoice_date == extracted.invoice_date,
            )
            .first()
        )
        if by_fingerprint:
            return DuplicateMatch(
                is_duplicate=True,
                matched_invoice_id=by_fingerprint.id,
                matched_file_name=by_fingerprint.file_name,
                reason=(
                    "Same vendor, amount, and date as an existing record "
                    "(no invoice number to compare)."
                ),
            )

    return DuplicateMatch(is_duplicate=False)