"""Agent 3 of 3 — Audit Agent.

Responsibility: given a validated expense, detect policy violations, summarize
the expense in plain language, and issue a verdict (approve / flag / reject)
with a risk score.

Design: deterministic policy rules run first (spend caps, receipt thresholds,
disallowed categories, stale dates, FX). These are auditable and can't drift.
Claude is then used for the parts that need judgment and language: a concise
human-readable summary and a check for disallowed items hiding in free-text line
descriptions. The final verdict is computed from the combined signals.
"""

from __future__ import annotations

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import date
from decimal import Decimal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
import dotenv
dotenv.load_dotenv()

from utils.policy import DEFAULT_POLICY, ExpensePolicy
from core.config import settings
from schemas.invoices import (
    AuditResult,
    AuditVerdict,
    ExtractedDocument,
    PolicyViolation,
    Severity,
    ValidationResult,
)

AUDIT_MODEL = os.getenv("AUDIT_MODEL", "gpt-4o-mini")

SYSTEM_MESSAGE = (
    "You are an expense audit assistant. Given structured expense data and a list "
    "of already-detected policy violations, do the following:\n"
    "1. Write a concise 2-3 sentence summary for a human auditor covering vendor, "
    "amount, purpose, and any notable concerns.\n"
    "2. Scan line-item descriptions for disallowed items the rule engine may have "
    "missed (alcohol, entertainment, personal items, gifts).\n"
    "3. Suggest a preliminary verdict (approve / flag / reject) with a one-sentence "
    "reason based solely on what you observe in the data.\n\n"
    "Return ONLY valid JSON with this exact structure — no markdown, no fences:\n"
    '{"summary": "string", '
    '"verdict_suggestion": "approve|flag|reject", '
    '"verdict_reason": "string", '
    '"extra_violations": [{"rule_id": "LLM_FLAG", "description": "string", "detail": "string"}]}'
)


class AuditAgent:
    def __init__(self, policy: ExpensePolicy = DEFAULT_POLICY, model: str = AUDIT_MODEL):
        self.policy = policy
        self.model = model
        self.client = ChatOpenAI(openai_api_key=settings.OPENAI_API_KEY, model=self.model, max_tokens=1500)

    def run(
        self, extracted: ExtractedDocument, validation: ValidationResult
    ) -> AuditResult:
        violations = self._rule_checks(extracted)

        # Ask Claude for a summary + a second pass on line-item text.
        summary, llm_violations = self._llm_pass(extracted, violations)
        violations += llm_violations

        risk = self._risk_score(extracted, validation, violations)
        verdict = self._verdict(validation, violations, risk)
        action = self._recommended_action(verdict, violations)

        return AuditResult(
            verdict=verdict,
            risk_score=risk,
            summary=summary,
            violations=violations,
            recommended_action=action,
        )

    # -- deterministic policy rules -----------------------------------------

    def _rule_checks(self, doc: ExtractedDocument) -> list[PolicyViolation]:
        out: list[PolicyViolation] = []
        total = Decimal(doc.total_amount) if doc.total_amount is not None else None

        # Receipt required above threshold (reimbursements without itemization).
        if (
            total is not None
            and total >= Decimal(str(self.policy.receipt_required_threshold))
            and not doc.line_items
        ):
            out.append(
                PolicyViolation(
                    rule_id="RECEIPT_REQUIRED",
                    severity=Severity.warning,
                    description="Itemized receipt required for this amount.",
                    detail=f"Total {doc.currency} {total} with no line items.",
                )
            )

        # Manager review threshold.
        if total is not None and total >= Decimal(str(self.policy.manager_review_threshold)):
            out.append(
                PolicyViolation(
                    rule_id="MANAGER_REVIEW",
                    severity=Severity.warning,
                    description="Amount exceeds manager-review threshold.",
                    detail=f"Total {doc.currency} {total}.",
                )
            )

        # Disallowed keywords in line items.
        for li in doc.line_items:
            desc = (li.description or "").lower()
            for kw in self.policy.disallowed_keywords:
                if kw in desc:
                    out.append(
                        PolicyViolation(
                            rule_id="DISALLOWED_ITEM",
                            severity=Severity.error,
                            description=f"Disallowed item: '{kw}'.",
                            detail=li.description,
                        )
                    )
                    break

        # Foreign currency.
        if doc.currency and doc.currency.upper() != self.policy.home_currency:
            out.append(
                PolicyViolation(
                    rule_id="FX_REVIEW",
                    severity=Severity.info,
                    description=f"Non-{self.policy.home_currency} currency ({doc.currency}).",
                    detail="Needs FX conversion before reimbursement.",
                )
            )

        # Stale expense.
        if doc.invoice_date:
            age = (date.today() - doc.invoice_date).days
            if age > self.policy.max_expense_age_days:
                out.append(
                    PolicyViolation(
                        rule_id="STALE_EXPENSE",
                        severity=Severity.warning,
                        description="Expense is older than the submission window.",
                        detail=f"{age} days old (limit {self.policy.max_expense_age_days}).",
                    )
                )
        return out

    # -- LLM pass: summary + free-text violation scan ------------------------

    def _llm_pass(
        self, doc: ExtractedDocument, found: list[PolicyViolation]
    ) -> tuple[str, list[PolicyViolation]]:
        payload = {
            "expense": doc.model_dump(mode="json"),
            "already_found": [v.model_dump(mode="json") for v in found],
            "disallowed_categories": self.policy.disallowed_categories,
        }
        try:
            msg = self.client.invoke(
                [
                    SystemMessage(content=SYSTEM_MESSAGE),
                    HumanMessage(content=json.dumps(payload, default=str)),
                ]
            )
            raw = msg.content if isinstance(msg.content, str) else "".join(
                b.text for b in msg.content if b.type == "text"
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(raw)
            # Append verdict_reason to summary when provided.
            summary = data.get("summary", "")
            verdict_reason = data.get("verdict_reason", "")
            if verdict_reason and verdict_reason not in summary:
                summary = f"{summary} {verdict_reason}".strip()
            extra = [
                PolicyViolation(
                    rule_id=v.get("rule_id", "LLM_FLAG"),
                    severity=Severity.warning,
                    description=v.get("description", ""),
                    detail=v.get("detail"),
                )
                for v in data.get("extra_violations", [])
            ]
            return summary, extra
        except Exception:
            return self._fallback_summary(doc), []

    @staticmethod
    def _fallback_summary(doc: ExtractedDocument) -> str:
        amt = f"{doc.currency} {doc.total_amount}" if doc.total_amount is not None else "unknown amount"
        vendor = doc.vendor_name or "unknown vendor"
        return f"{doc.document_type.value.title()} from {vendor} for {amt}."

    # -- scoring + verdict ---------------------------------------------------

    def _risk_score(
        self,
        doc: ExtractedDocument,
        validation: ValidationResult,
        violations: list[PolicyViolation],
    ) -> int:
        score = 0
        weights = {Severity.error: 35, Severity.warning: 15, Severity.info: 5}
        for v in violations:
            score += weights[v.severity]
        for i in validation.issues:
            score += weights[i.severity]
        if validation.duplicate.is_duplicate:
            score += 30
        score += int((1 - validation.completeness_score) * 20)
        return max(0, min(100, score))

    def _verdict(
        self,
        validation: ValidationResult,
        violations: list[PolicyViolation],
        risk: int,
    ) -> AuditVerdict:
        has_error = (
            not validation.is_valid
            or any(v.severity == Severity.error for v in violations)
        )
        if has_error or risk >= 70:
            return AuditVerdict.reject
        if violations or risk >= 30:
            return AuditVerdict.flag
        return AuditVerdict.approve

    @staticmethod
    def _recommended_action(
        verdict: AuditVerdict, violations: list[PolicyViolation]
    ) -> str:
        if verdict == AuditVerdict.approve:
            return "Auto-approve for reimbursement."
        if verdict == AuditVerdict.flag:
            return "Route to manager for review before reimbursement."
        return "Reject and return to submitter with the listed violations."

# if __name__ == "__main__":
#     invoice_data = {'bill_to': 'Test Business, 123 Somewhere St, Melbourne, VIC 3000',
#  'confidence': {'amount': 'high', 'date': 'high', 'invoice_number': 'high'},
#  'currency': 'USD',
#  'document_type': 'invoice',
#  'due_date': '2016-01-31',
#  'invoice_date': '2016-01-25',
#  'invoice_number': 'INV-3337',
#  'line_items': [{'description': 'Web Design',
#                  'quantity': 1.0,
#                  'total': 85.0,
#                  'unit_price': 85.0}],
#  'notes': None,
#  'payment_terms': 'Payment is due within 30 days from date of invoice. Late '
#                   'payment is subject to fees of 5% per month.',
#  'po_number': '12345',
#  'subtotal': 85.0,
#  'tax': 8.5,
#  'total_amount': 93.5,
#  'vendor_address': 'Suite 5A-1204, 123 Somewhere Street, Your City AZ 12345',
#  'vendor_name': 'DEMO - Sliced Invoices'}
#     from validation_agent import ValidationAgent 
#     data = ExtractedDocument(**invoice_data)
#     audit_agent = AuditAgent()
#     print(audit_agent.summerize_audit())