"""SQLAlchemy model for a processed invoice/reimbursement document.

The extracted line items and confidence blob are stored as JSON; the fields used
for duplicate detection are promoted to real columns so they can be indexed and
queried efficiently.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.types import JSON

from database.base import Base


class Invoice(Base):
    # __tablename__ = "invoices"
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String(512), nullable=False)
    file_hash = Column(String(64), index=True)  # sha256 of raw bytes — exact-file dedupe

    document_type = Column(String(32), default="unknown")
    invoice_number = Column(String(128), index=True)
    invoice_date = Column(Date, index=True)
    due_date = Column(Date)
    vendor_name = Column(String(512), index=True)
    vendor_address = Column(Text)
    bill_to = Column(Text)
    po_number = Column(String(128))
    payment_terms = Column(String(256))

    line_items = Column(JSON, default=list)
    subtotal = Column(Numeric(14, 2))
    tax = Column(Numeric(14, 2))
    total_amount = Column(Numeric(14, 2), index=True)
    currency = Column(String(8), default="USD")
    notes = Column(Text)
    confidence = Column(JSON, default=dict)

    is_duplicate = Column(Boolean, default=False)
    duplicate_of_id = Column(Integer, nullable=True)

    audit_verdict = Column(String(16), nullable=True)   # approve / flag / reject
    audit_risk_score = Column(Integer, nullable=True)   # 0-100

    created_at = Column(DateTime, default=datetime.utcnow)


    