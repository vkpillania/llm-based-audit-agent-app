"""Pydantic schemas for the OCR agent.

These define the contract for what the agent extracts from a document and what
the API returns to callers. The extraction schema mirrors the JSON the model is
asked to produce, so it doubles as validation on the model's output.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from datetime import date, datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field



class DocumentType(str, Enum):
    invoice = "invoice"
    reimbursement = "reimbursement"
    unknown = "unknown"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class LineItem(BaseModel):
    description: str = ""
    quantity: float | None = None
    unit_price: Decimal | None = None
    total: Decimal | None = None


class FieldConfidence(BaseModel):
    invoice_number: Confidence = Confidence.low
    amount: Confidence = Confidence.low
    date: Confidence = Confidence.low


class ExtractedDocument(BaseModel):
    """The structured result of running OCR + extraction on one file."""

    document_type: DocumentType = DocumentType.unknown
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    bill_to: Optional[str] = None
    po_number: Optional[str] = None
    payment_terms: Optional[str] = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    currency: str = "USD"
    notes: Optional[str] = None
    confidence: FieldConfidence = Field(default_factory=FieldConfidence)


class DuplicateMatch(BaseModel):
    is_duplicate: bool
    matched_invoice_id: Optional[int] = None
    matched_file_name: Optional[str] = None
    reason: Optional[str] = None


class ProcessResponse(BaseModel):
    """What the /process endpoint returns for a single uploaded file."""

    invoice_id: int
    file_name: str
    extracted: ExtractedDocument
    duplicate: DuplicateMatch


class InvoiceListItem(BaseModel):
    id: int
    file_name: str
    document_type: DocumentType
    vendor_name: Optional[str]
    invoice_number: Optional[str]
    invoice_date: Optional[date]
    total_amount: Optional[Decimal]
    currency: str
    is_duplicate: bool

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Validation Agent schemas
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    error = "error"      # blocks approval
    warning = "warning"  # needs a human look
    info = "info"        # advisory only


class ValidationIssue(BaseModel):
    code: str            # machine-readable, e.g. "MATH_TOTAL_MISMATCH"
    severity: Severity
    message: str
    field: Optional[str] = None


class ValidationResult(BaseModel):
    is_valid: bool                                  # False if any error-level issue
    completeness_score: float                       # 0..1, fraction of key fields present
    issues: list[ValidationIssue] = Field(default_factory=list)
    duplicate: "DuplicateMatch"


# ---------------------------------------------------------------------------
# Audit Agent schemas
# ---------------------------------------------------------------------------

class AuditVerdict(str, Enum):
    approve = "approve"
    flag = "flag"        # needs manager review
    reject = "reject"


class PolicyViolation(BaseModel):
    rule_id: str
    severity: Severity
    description: str
    detail: Optional[str] = None


class AuditResult(BaseModel):
    verdict: AuditVerdict
    risk_score: int                                 # 0..100, higher = riskier
    summary: str                                    # human-readable expense summary
    violations: list[PolicyViolation] = Field(default_factory=list)
    recommended_action: str


# ---------------------------------------------------------------------------
# Full pipeline result (OCR -> Validation -> Audit)
# ---------------------------------------------------------------------------

class PipelineResult(BaseModel):
    invoice_id: int
    file_name: str
    extracted: ExtractedDocument
    validation: ValidationResult
    audit: AuditResult



# invoice CRUD validation pydantic models

class InvoiceBase(BaseModel):
    """Shared fields (everything the caller can set)."""

    file_name: str
    file_hash: Optional[str] = None

    document_type: str = "unknown"
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    bill_to: Optional[str] = None
    po_number: Optional[str] = None
    payment_terms: Optional[str] = None

    line_items: list[dict[str, Any]] = Field(default_factory=list)
    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    currency: str = "USD"
    notes: Optional[str] = None
    confidence: dict[str, Any] = Field(default_factory=dict)

    is_duplicate: bool = False
    duplicate_of_id: Optional[int] = None


class InvoiceCreate(InvoiceBase):
    """Payload for creating a row — no id/created_at (DB sets those)."""
    pass


class InvoiceRead(InvoiceBase):
    """What you return from the API — includes DB-generated fields."""

    model_config = ConfigDict(from_attributes=True)  # allows InvoiceRead.model_validate(orm_obj)

    id: int
    created_at: datetime

class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_name: str
    document_type: str
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    total_amount: Optional[Decimal] = None
    currency: str = "USD"
    is_duplicate: bool = False
    bill_to: Optional[str] = None
    po_number: Optional[str] = None
    payment_terms: Optional[str] = None
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    notes: Optional[str] = None
    confidence: dict[str, Any] = Field(default_factory=dict)
    duplicate_of_id: Optional[int] = None
    audit_verdict: Optional[str] = None
    audit_risk_score: Optional[int] = None
    created_at: Optional[datetime] = None

class PaginatedInvoices(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    results: list[InvoiceRead] 