"""Expense policy.

This is the ruleset the Audit Agent checks each expense against. It's kept as
plain data so a non-engineer can edit limits and categories without touching
agent logic. In a real deployment you'd load this from a database or config
service per organization.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExpensePolicy:
    # Hard spend caps by category (USD). None = no specific cap.
    category_limits: dict[str, float] = field(
        default_factory=lambda: {
            "meals": 75.0,           # per person per meal
            "hotel": 300.0,          # per night
            "airfare": 1500.0,
            "ground_transport": 100.0,
            "office_supplies": 500.0,
            "software": 1000.0,
            "entertainment": 0.0,    # not reimbursable at all
        }
    )

    # Any single expense at or above this needs an itemized receipt.
    receipt_required_threshold: float = 25.0

    # Any single expense at or above this is auto-flagged for manager review.
    manager_review_threshold: float = 1000.0

    # Categories that are never reimbursable.
    disallowed_categories: list[str] = field(
        default_factory=lambda: ["alcohol", "entertainment", "personal", "gift"]
    )

    # Keywords that hint at a disallowed item appearing in line items.
    disallowed_keywords: list[str] = field(
        default_factory=lambda: [
            "alcohol", "wine", "beer", "liquor", "bar tab", "minibar",
            "cigarette", "tobacco", "gift card", "spa", "massage",
        ]
    )

    # Currency the company reimburses in. Others get flagged for FX review.
    home_currency: str = "USD"

    # Max age (days) of an expense before it's considered stale/late.
    max_expense_age_days: int = 90


DEFAULT_POLICY = ExpensePolicy()