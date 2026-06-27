from fastapi import APIRouter, Depends, File, HTTPException, Response, status, Query,  UploadFile
from httpx import get
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import Integer
from starlette.status import HTTP_201_CREATED 
from core.config import settings
from models.invoice import Invoice
from database.session import get_db
from typing import Optional
from utils.files import validate
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import func

from models.invoice import Invoice
from utils.policy import DEFAULT_POLICY
from utils.policy_rag import build_policy_from_folder

from agents.graph import ExpenseAuditGraph

router = APIRouter(prefix="/invoces", tags=["invoices"])

from schemas.invoices import (
    InvoiceBase,
    InvoiceListItem,
    InvoiceCreate,
    InvoiceRead,
    PaginatedInvoices
)

invoice_router = APIRouter(prefix="/invoices", tags=["Invoices"] ,)
# router = APIRouter(prefix="/books", tags=["Books"] , dependencies=[Depends(get_current_active_user)],)


# ============ CREATE ============

@invoice_router.post("", response_model=InvoiceBase, status_code=status.HTTP_201_CREATED)
def create_invoice(invoice_data: InvoiceCreate, db: Session = Depends(get_db) ):
    """Create a new invoice."""
    # Check for duplicate invoice
    existing = db.query(Invoice).filter(Invoice.invoice_number == invoice_data.invoice_number).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A invoice with invoice {invoice_data.invoice_number} already exists",
        )
    invoice = Invoice(**invoice_data.model_dump())
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice




# @router.get("/{book_id}", response_model=BookDetailSchema)
# def get_book(book_id: int, db: Session = Depends(get_db)):
#     """Get a single book with its borrow records."""
#     book = (
#         db.query(Invoice)
#         # .options(selectinload(Invoice.borrow_records).joinedload(BorrowRecord.member))
#         .filter(Invoice.id == book_id)
#         .first()
#     )
#     if not book:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=f"Invoice {book_id} not found",
#         )
#     return book


@invoice_router.get("", response_model=PaginatedInvoices)
async def list_invoices(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    vendor: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    currency: Optional[str] = Query(None),
    include_duplicates: bool = Query(True),
    verdict: Optional[str] = Query(None),
    risk_score_min: Optional[int] = Query(None, ge=0, le=100),
    risk_score_max: Optional[int] = Query(None, ge=0, le=100),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    db: Session = Depends(get_db),
):
    """List all invoices (paginated, with optional filters)."""
    from sqlalchemy import asc, desc

    query = db.query(Invoice)

    if search:
        term = f"%{search}%"
        query = query.filter(
            Invoice.invoice_number.ilike(term) | Invoice.invoice_date.cast(str).ilike(term)
        )
    if vendor:
        query = query.filter(Invoice.vendor_name.ilike(f"%{vendor}%"))
    if document_type:
        query = query.filter(Invoice.document_type == document_type)
    if currency:
        query = query.filter(Invoice.currency == currency)
    if not include_duplicates:
        query = query.filter(Invoice.is_duplicate.is_(False))
    if verdict:
        query = query.filter(Invoice.audit_verdict == verdict)
    if risk_score_min is not None:
        query = query.filter(Invoice.audit_risk_score >= risk_score_min)
    if risk_score_max is not None:
        query = query.filter(Invoice.audit_risk_score <= risk_score_max)

    _SORTABLE = {
        "created_at", "invoice_date", "total_amount", "vendor_name",
        "audit_risk_score", "audit_verdict",
    }
    col_name = sort_by if sort_by in _SORTABLE else "created_at"
    sort_col = getattr(Invoice, col_name)
    query = query.order_by(desc(sort_col) if sort_dir == "desc" else asc(sort_col))

    total = query.count()
    results = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "results": results,
    }


@invoice_router.get("/duplicates", response_model=PaginatedInvoices)
async def list_invoices(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search invoice by number or date"),
    db: Session = Depends(get_db),
):
    """List all invoices (paginated, with optional filters)."""
    query = db.query(Invoice)
    # if search:
        # search_term = f"%{search}%"
    query = query.filter(Invoice.is_duplicate.is_(True))
    
    total = query.count()
    books = (
        query
        .order_by(Invoice.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "results": books,
    } 

@invoice_router.post("/process")
async def audit_invoice(file: UploadFile = File(...), db: Session = Depends(get_db)):
    ext = validate(file)
    policy = build_policy_from_folder("policies")
    graph = ExpenseAuditGraph(policy=policy)
    file_bytes = await file.read()
    try:
        state = graph.run(file_bytes=file_bytes, file_name=file.filename, db=db)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
    # return only the serializable parts — never the raw bytes
    return {
        "invoice_id": state["invoice_id"],
        "file_name": file.filename,
        "extracted": state["extracted"],
        "validation": state["validation"],
        "audit": state["audit"],
    }




@invoice_router.get("/dashboard")
def dashboard_summary(db: Session = Depends(get_db)) -> dict:
    ZERO = Decimal("0")

    # One row of scalar aggregates: count, sum, distinct vendors, duplicates.
    totals = db.query(
        func.count(Invoice.id),
        func.coalesce(func.sum(Invoice.total_amount), ZERO),
        func.count(func.distinct(Invoice.vendor_name)),
        func.coalesce(
            func.sum(
                func.cast(Invoice.is_duplicate, Integer)
            ),
            0,
        ),
    ).one()

    total_invoices, total_amount, unique_vendors, duplicate_count = totals

    # This-month figures, filtered by created_at.
    month_start = date.today().replace(day=1)
    docs_month, amount_month = db.query(
        func.count(Invoice.id),
        func.coalesce(func.sum(Invoice.total_amount), ZERO),
    ).filter(
        Invoice.created_at >= datetime(month_start.year, month_start.month, 1)
    ).one()

    average_amount = (total_amount / total_invoices) if total_invoices else ZERO
    duplicate_rate = (duplicate_count / total_invoices) if total_invoices else 0.0

    return {
        "total_invoices": total_invoices,
        "total_amount": str(total_amount),
        "average_amount": str(average_amount.quantize(Decimal("0.01"))),
        "unique_vendors": unique_vendors,
        "duplicate_count": duplicate_count,
        "duplicate_rate": round(duplicate_rate, 4),
        "documents_this_month": docs_month,
        "amount_this_month": str(amount_month),
    }


@invoice_router.get("/by-document-type")
async def by_document_type(db: Session = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(
            Invoice.document_type,
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total_amount), Decimal("0")),
        )
        .group_by(Invoice.document_type)
        .order_by(func.count(Invoice.id).desc())
        .all()
    )
    return [
        {"label": label or "unknown", "count": count, "amount": str(amount)}
        for label, count, amount in rows
    ]


from datetime import date
# from decimal import Decimal
from sqlalchemy import func
# from sqlalchemy.orm import Session


ZERO = Decimal("0")

@invoice_router.get("/spend")
async def spend_report(
    db: Session = Depends(get_db),
    group_by: str = "vendor",
    period_start: date | None = None,
    period_end: date | None = None,
    currency_filter: str | None = None,
) -> dict:
    # Map the requested dimension to a real column.
    column = {
        "vendor": Invoice.vendor_name,
        "document_type": Invoice.document_type,
        "currency": Invoice.currency,
    }[group_by]

    # Base grouped query: label, count, summed amount.
    q = db.query(
        column,
        func.count(Invoice.id),
        func.coalesce(func.sum(Invoice.total_amount), ZERO),
    )

    # Optional filters.
    if period_start:
        q = q.filter(Invoice.invoice_date >= period_start)
    if period_end:
        q = q.filter(Invoice.invoice_date <= period_end)
    if currency_filter:
        q = q.filter(Invoice.currency == currency_filter)

    rows = (
        q.group_by(column)
        .order_by(func.coalesce(func.sum(Invoice.total_amount), ZERO).desc())
        .all()
    )

    items = [
        {"label": str(label) if label is not None else "—", "count": count, "amount": str(amount)}
        for label, count, amount in rows
    ]

    return {
        "group_by": group_by,
        "period_start": period_start,
        "period_end": period_end,
        "currency_filter": currency_filter,
        "total_amount": str(sum((Decimal(i["amount"]) for i in items), ZERO)),
        "total_count": sum(i["count"] for i in items),
        "rows": items,
    }



@invoice_router.get("/spend-over-time")
async def spend_over_time(db: Session = Depends(get_db), granularity: str = "month") -> list[dict]:
    rows = (
        db.query(Invoice.invoice_date, Invoice.total_amount)
        .filter(Invoice.invoice_date.isnot(None))
        .all()
    )

    buckets: dict[str, dict] = {}
    for inv_date, amount in rows:
        key = (
            f"{inv_date.year:04d}-{inv_date.month:02d}"   # "2026-06"
            if granularity == "month"
            else inv_date.isoformat()                      # "2026-06-24"
        )
        b = buckets.setdefault(key, {"count": 0, "amount": ZERO})
        b["count"] += 1
        b["amount"] += Decimal(amount) if amount is not None else ZERO

    return [
        {"period": k, "count": v["count"], "amount": str(v["amount"])}
        for k, v in sorted(buckets.items())
    ]


@invoice_router.get("/by-currency")
def by_currency(db: Session = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(
            Invoice.currency,
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total_amount), ZERO),
        )
        .group_by(Invoice.currency)
        .order_by(func.coalesce(func.sum(Invoice.total_amount), ZERO).desc())
        .all()
    )

    return [
        {"label": cur or "—", "count": count, "amount": str(amount)}
        for cur, count, amount in rows
    ]

@invoice_router.get("/top-vendors")
def top_vendors(db: Session = Depends(get_db), limit: int = 10) -> list[dict]:
    rows = (
        db.query(
            Invoice.vendor_name,
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.total_amount), ZERO),
        )
        .filter(Invoice.vendor_name.isnot(None))
        .group_by(Invoice.vendor_name)
        .order_by(func.coalesce(func.sum(Invoice.total_amount), ZERO).desc())
        .limit(limit)
        .all()
    )

    return [
        {"vendor_name": name, "count": count, "total_amount": str(amount)}
        for name, count, amount in rows
    ]


# import csv, io
# from fastapi.responses import StreamingResponse

# @invoice_router.get("/export-csv")
# def export(db: Session = Depends(get_db) , group_by:str = 'vendor' ):
#     invoices = db.query(Invoice)
#     .filter(Invoice.).order_by(Invoice.created_at.desc()).all()

#     buf = io.StringIO()
#     writer = csv.writer(buf)
#     writer.writerow(["id", "vendor_name", "total_amount", "currency"])
#     for inv in invoices:
#         writer.writerow([inv.id, inv.vendor_name, inv.total_amount, inv.currency])

#     buf.seek(0)
#     return StreamingResponse(
#         buf,
#         media_type="text/csv",
#         headers={"Content-Disposition": "attachment; filename=invoices.csv"},
#     )

@invoice_router.get("/health")
def health():
    return "OK"