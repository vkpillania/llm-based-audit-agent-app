"""Synthetic test-data generator.

Creates fake invoices and reimbursement forms as PDFs so you can exercise the
full pipeline (including duplicate detection and policy violations) without real
documents. Generates a mix of clean, duplicate, and policy-violating samples.

Usage:
    python -m app.synthetic_data --out ./samples --count 8
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

VENDORS = ["Acme Office Supplies", "TechMart", "CloudHost Inc", "Steakhouse 21", "GlobalTravel"]
CLEAN_ITEMS = [("Printer paper", 12.0), ("USB cable", 9.5), ("Notebook", 6.0), ("Monitor stand", 45.0)]
RISKY_ITEMS = [("Bottle of wine", 60.0), ("Minibar charge", 35.0), ("Gift card", 100.0)]


def _draw(path: Path, doc_type: str, vendor: str, inv_no: str,
          inv_date: date, items: list[tuple[str, float]], currency: str = "USD"):
    c = canvas.Canvas(str(path), pagesize=LETTER)
    w, h = LETTER
    y = h - inch

    c.setFont("Helvetica-Bold", 18)
    title = "INVOICE" if doc_type == "invoice" else "REIMBURSEMENT FORM"
    c.drawString(inch, y, title)
    y -= 0.4 * inch

    c.setFont("Helvetica", 11)
    c.drawString(inch, y, f"Vendor: {vendor}"); y -= 0.25 * inch
    c.drawString(inch, y, f"Invoice #: {inv_no}"); y -= 0.25 * inch
    c.drawString(inch, y, f"Date: {inv_date.isoformat()}"); y -= 0.25 * inch
    c.drawString(inch, y, "Bill To: Employee Expenses Dept"); y -= 0.4 * inch

    c.setFont("Helvetica-Bold", 11)
    c.drawString(inch, y, "Description"); c.drawString(5 * inch, y, "Amount")
    y -= 0.05 * inch
    c.line(inch, y, 7.5 * inch, y); y -= 0.25 * inch

    c.setFont("Helvetica", 11)
    subtotal = 0.0
    for desc, amt in items:
        c.drawString(inch, y, desc)
        c.drawString(5 * inch, y, f"{currency} {amt:.2f}")
        subtotal += amt
        y -= 0.25 * inch

    tax = round(subtotal * 0.08, 2)
    total = round(subtotal + tax, 2)
    y -= 0.15 * inch
    c.line(inch, y, 7.5 * inch, y); y -= 0.25 * inch
    c.drawString(5 * inch, y, f"Subtotal: {currency} {subtotal:.2f}"); y -= 0.22 * inch
    c.drawString(5 * inch, y, f"Tax (8%): {currency} {tax:.2f}"); y -= 0.22 * inch
    c.setFont("Helvetica-Bold", 11)
    c.drawString(5 * inch, y, f"Total: {currency} {total:.2f}")
    c.save()


def generate(out_dir: str, count: int):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    made = []

    for i in range(count):
        roll = random.random()
        vendor = random.choice(VENDORS)
        inv_no = f"INV-{1000 + i}"
        inv_date = date.today() - timedelta(days=random.randint(1, 30))
        doc_type = random.choice(["invoice", "reimbursement"])

        if roll < 0.25:  # policy-violating
            items = random.sample(CLEAN_ITEMS, 1) + random.sample(RISKY_ITEMS, 1)
            currency = random.choice(["USD", "EUR"])
        else:            # clean
            items = random.sample(CLEAN_ITEMS, k=random.randint(1, 3))
            currency = "USD"

        path = out / f"{doc_type}_{inv_no}.pdf"
        _draw(path, doc_type, vendor, inv_no, inv_date, items, currency)
        made.append(path)

    # Force one exact duplicate of the first file, to exercise dedupe.
    if made:
        dup = out / "DUPLICATE_of_first.pdf"
        dup.write_bytes(made[0].read_bytes())
        made.append(dup)

    print(f"Generated {len(made)} files in {out}/")
    for p in made:
        print("  -", p.name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./samples")
    ap.add_argument("--count", type=int, default=8)
    args = ap.parse_args()
    generate(args.out, args.count)