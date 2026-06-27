"""LangGraph orchestration of the three-agent expense-audit pipeline.

This replaces the hand-written sequential orchestrator in workflow.py with a
LangGraph StateGraph. The agents are unchanged — they're wrapped as graph nodes
that read from and write to a shared state object.

Graph shape:

        START
          |
          v
      [ ocr_node ]                     Agent 1: extract fields
          |
          v
   [ validation_node ]                 Agent 2: validate + dedupe
          |
          v
     <should_audit?>   ── reject ──>  [ short_circuit_node ] ──> persist ──> END
          |
        audit
          v
      [ audit_node ]                   Agent 3: policy + verdict
          |
          v
     [ persist_node ]                  save to DB (enables future dedupe)
          |
          v
         END

The conditional edge after validation is the reason to use a graph: if
validation finds a duplicate or a blocking error, we skip the (paid) Audit LLM
call and go straight to a deterministic rejection.
"""

from __future__ import annotations

import hashlib
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session


from utils.policy_rag import build_policy_from_folder
from models.invoice import Invoice
from agents.audit_agent import AuditAgent
from agents.ocr_agent import OCRAgent
from agents.validation_agent import ValidationAgent
from utils.policy import DEFAULT_POLICY, ExpensePolicy
from schemas.invoices import (
    AuditResult,
    AuditVerdict,
    ExtractedDocument,
    PolicyViolation,
    Severity,
    ValidationResult,
)


# --------------------------------------------------------------------------- #
# Shared state that flows through the graph.
# Each node returns a partial dict; LangGraph merges it into the state.
# --------------------------------------------------------------------------- #
class AuditState(TypedDict, total=False):
    # Inputs (set at invocation time)
    file_bytes: bytes
    file_name: str
    db: Session

    # Produced by nodes
    file_hash: str
    extracted: ExtractedDocument
    validation: ValidationResult
    audit: AuditResult
    invoice_id: int

# --------------------------------------------------------------------------- #
# State values can arrive as a Pydantic model OR as a plain dict (LangGraph may
# serialize them between nodes, and always does so with a checkpointer). These
# helpers accept either form, so nodes never assume one or the other.
# --------------------------------------------------------------------------- #
def _as_extracted(value) -> ExtractedDocument:
    return value if isinstance(value, ExtractedDocument) else ExtractedDocument.model_validate(value)
 
 
def _as_validation(value) -> ValidationResult:
    return value if isinstance(value, ValidationResult) else ValidationResult.model_validate(value)
 
 
def _as_audit(value) -> AuditResult:
    return value if isinstance(value, AuditResult) else AuditResult.model_validate(value)
 

# --------------------------------------------------------------------------- #
# Node implementations. Each wraps one agent (or a persistence step).
# --------------------------------------------------------------------------- #
class ExpenseAuditGraph:
    def __init__(self, policy: ExpensePolicy = DEFAULT_POLICY):
        self.ocr = OCRAgent()
        self.validator = ValidationAgent()
        self.auditor = AuditAgent(policy=policy)
        self.app = self._build()

    # -- nodes ------------------------------------------------------------- #

    def ocr_node(self, state: AuditState) -> dict:
        """Agent 1 — OCR extraction."""
        file_hash = hashlib.sha256(state["file_bytes"]).hexdigest()
        extracted = self.ocr.run(state["file_bytes"], state["file_name"])
        return {"extracted": extracted, "file_hash": file_hash}

    def validation_node(self, state: AuditState) -> dict:
        """Agent 2 — validation + duplicate detection."""
        extracted = _as_extracted(state["extracted"])
        validation = self.validator.run(extracted, state["db"], state["file_hash"])
        return {"validation": validation}

    def audit_node(self, state: AuditState) -> dict:
        """Agent 3 — policy audit, summary, and verdict."""
        extracted = _as_extracted(state["extracted"])
        validation = _as_validation(state["validation"])
        audit = self.auditor.run(extracted, validation)
        return {"audit": audit}

    def short_circuit_node(self, state: AuditState) -> dict:
        """Build a deterministic reject without calling the Audit LLM.

        Reached only when validation found a duplicate or a blocking error, so
        the expense is already disqualified and the paid audit call is wasteful.
        """
        # validation = state["validation"]
        validation = _as_validation(state["validation"])
        violations: list[PolicyViolation] = []
        seen_codes: set[str] = set()
        if validation.duplicate.is_duplicate:
            violations.append(
                PolicyViolation(
                    rule_id="DUPLICATE",
                    severity=Severity.error,
                    description="Duplicate expense.",
                    detail=validation.duplicate.reason,
                )
            )
            seen_codes.add("DUPLICATE")
        for issue in validation.issues:
            if issue.severity == Severity.error and issue.code not in seen_codes:
                seen_codes.add(issue.code)
                violations.append(
                    PolicyViolation(
                        rule_id=issue.code,
                        severity=Severity.error,
                        description=issue.message,
                        detail=issue.field,
                    )
                )
        # doc = state["extracted"]
        doc = _as_extracted(state["extracted"])
        amount = (
            f"{doc.currency} {doc.total_amount}"
            if doc.total_amount is not None
            else "unknown amount"
        )
        audit = AuditResult(
            verdict=AuditVerdict.reject,
            risk_score=100,
            summary=(
                f"{doc.document_type.value.title()} from "
                f"{doc.vendor_name or 'unknown vendor'} for {amount} — "
                f"rejected at validation."
            ),
            violations=violations,
            recommended_action="Reject; failed validation before audit.",
        )
        return {"audit": audit}

    def persist_node(self, state: AuditState) -> dict:
        """Save the result so future runs can detect duplicates of this one."""
        doc = _as_extracted(state["extracted"])
        v = _as_validation(state["validation"])
        audit = _as_audit(state["audit"])

        # Build a human-readable summary stored in notes.
        notes = (
            f"Verdict: {audit.verdict.value.upper()} | Risk: {audit.risk_score}/100\n"
            f"Summary: {audit.summary}\n"
            f"Action: {audit.recommended_action}"
        )

        row = Invoice(
            file_name=state["file_name"],
            file_hash=state["file_hash"],
            document_type=doc.document_type.value,
            invoice_number=doc.invoice_number,
            invoice_date=doc.invoice_date,
            due_date=doc.due_date,
            vendor_name=doc.vendor_name,
            vendor_address=doc.vendor_address,
            bill_to=doc.bill_to,
            po_number=doc.po_number,
            payment_terms=doc.payment_terms,
            line_items=[li.model_dump(mode="json") for li in doc.line_items],
            subtotal=doc.subtotal,
            tax=doc.tax,
            total_amount=doc.total_amount,
            currency=doc.currency,
            notes=notes,
            confidence=doc.confidence.model_dump(mode="json"),
            is_duplicate=v.duplicate.is_duplicate,
            duplicate_of_id=v.duplicate.matched_invoice_id,
            audit_verdict=audit.verdict.value,
            audit_risk_score=audit.risk_score,
        )
        db = state["db"]
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"invoice_id": row.id}

    # -- conditional routing ---------------------------------------------- #

    @staticmethod
    def should_audit(state: AuditState) -> str:
        """Decide whether to run the full audit or short-circuit to reject."""
        v = _as_validation(state["validation"])
        has_blocking_error = any(i.severity == Severity.error for i in v.issues)
        if not v.is_valid or has_blocking_error or v.duplicate.is_duplicate:
            return "short_circuit"
        return "audit"

    # -- graph wiring ------------------------------------------------------ #

    def _build(self):
        g = StateGraph(AuditState)

        g.add_node("ocr", self.ocr_node)
        g.add_node("validation", self.validation_node)
        g.add_node("audit", self.audit_node)
        g.add_node("short_circuit", self.short_circuit_node)
        g.add_node("persist", self.persist_node)

        g.add_edge(START, "ocr")
        g.add_edge("ocr", "validation")

        # Conditional branch after validation.
        g.add_conditional_edges(
            "validation",
            self.should_audit,
            {"audit": "audit", "short_circuit": "short_circuit"},
        )

        # Both branches converge on persistence, then end.
        g.add_edge("audit", "persist")
        g.add_edge("short_circuit", "persist")
        g.add_edge("persist", END)

        return g.compile()

    # -- public entry point ------------------------------------------------ #

    def run(self, file_bytes: bytes, file_name: str, db: Session) -> dict:
        """Invoke the compiled graph and return the final state.

        The returned dict contains 'extracted', 'validation', 'audit', and
        'invoice_id'.
        """
        return self.app.invoke(
            {"file_bytes": file_bytes, "file_name": file_name, "db": db}
        )


# Convenience singleton-style factory.
def build_graph(policy: ExpensePolicy = DEFAULT_POLICY) -> ExpenseAuditGraph:
    return ExpenseAuditGraph(policy=policy)