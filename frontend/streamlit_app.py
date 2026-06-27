"""Streamlit UI for the audit_agent app.

A standalone front end that talks to the existing FastAPI backend over HTTP — it
does not import or modify any of the backend code. Point it at your running API
(default http://127.0.0.1:8000) and use the sections in the sidebar:

  • Dashboard          — KPIs, spend over time, breakdowns, top vendors
  • Upload & Audit     — upload an invoice/PDF and run the pipeline
  • Invoices           — filterable, paginated list + per-invoice details
  • Duplicates         — every invoice flagged as a duplicate
  • Reports            — grouped spend report + CSV export

Run:
    pip install streamlit requests pandas
    streamlit run streamlit_app.py
"""

from __future__ import annotations

import io
import os

import altair as alt
import pandas as pd
import requests
import streamlit as st

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
st.set_page_config(page_title="Audit Agent", page_icon="🧾", layout="wide")

DEFAULT_API = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
# DEFAULT_API = os.getenv("API_BASE_URL", "http://backend:8000")

# A little colour for verdicts/severities.
st.markdown(
    """
    <style>
    .verdict {padding:14px 18px;border-radius:8px;border:1px solid;display:inline-block;width:100%;}
    .v-approve{background:#eaf3ee;border-color:#1f7a5c;color:#1f7a5c;}
    .v-flag{background:#f8efdf;border-color:#c77d1a;color:#9a5e10;}
    .v-reject{background:#f6e8e6;border-color:#b23a36;color:#b23a36;}
    .verdict h2{margin:0;font-size:30px;text-transform:uppercase;letter-spacing:1px;}
    .pill{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;}
    .p-error{background:#f6e8e6;color:#b23a36;}
    .p-warning{background:#f8efdf;color:#9a5e10;}
    .p-info{background:#edf0f4;color:#14213d;}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# API helpers
# --------------------------------------------------------------------------- #
def api_base() -> str:
    return st.session_state.get("api_base", DEFAULT_API).rstrip("/")


def get_json(path: str, params: dict | None = None):
    r = requests.get(f"{api_base()}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def post_file(path: str, file) -> dict | list:
    files = {"files": (file.name, file.getvalue(), file.type or "application/octet-stream")}
    # Try the multi-file field first ("files"); fall back to single "file".
    r = requests.post(f"{api_base()}{path}", files=files, timeout=120)
    if r.status_code == 422:
        files = {"file": (file.name, file.getvalue(), file.type or "application/octet-stream")}
        r = requests.post(f"{api_base()}{path}", files=files, timeout=120)
    r.raise_for_status()
    return r.json()


def fmt_money(v, currency="USD") -> str:
    if v in (None, ""):
        return "—"
    try:
        return f"{currency} {float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def fmt_compact(v, currency="USD") -> str:
    """Short money for KPI tiles, e.g. 'USD 512.8K' — avoids truncation."""
    if v in (None, ""):
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(n) >= div:
            return f"{currency} {n / div:,.1f}{suffix}"
    return f"{currency} {n:,.0f}"


def _colored_bar_chart(df: pd.DataFrame, x_col: str, y_col: str, height: int = 260):
    """Render a bar chart where every bar gets a distinct color."""
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X(f"{x_col}:N", sort="-y", axis=alt.Axis(labelAngle=-30, labelLimit=160), title=None),
            y=alt.Y(f"{y_col}:Q", axis=alt.Axis(format="~s"), title=None),
            color=alt.Color(f"{x_col}:N", scale=alt.Scale(scheme="tableau20"), legend=None),
            tooltip=[alt.Tooltip(f"{x_col}:N"), alt.Tooltip(f"{y_col}:Q", format=",.2f")],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, use_container_width=True)


def sidebar():
    st.sidebar.title("🧾 Audit Agent")
    st.session_state["api_base"] = st.sidebar.text_input(
        "API base URL", value=st.session_state.get("api_base", DEFAULT_API)
    )
    # Quick connectivity check.
    try:
        get_json("/api/v1/invoices/health")
        st.sidebar.success("Connected")
    except Exception as e:
        st.sidebar.error(f"Backend not reachable {e}")

    st.sidebar.divider()
    return st.sidebar.radio(
        "Section",
        ["Dashboard", "Upload & Audit", "Invoices", "Duplicates", "Reports"],
        label_visibility="collapsed",
    )


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def page_dashboard():
    st.header("Dashboard")
    try:
        summary = get_json("/api/v1/invoices/dashboard")
    except Exception as e:
        st.error(f"Couldn't load dashboard: {e}")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total invoices", f"{summary['total_invoices']:,}")
    c2.metric("Total amount", fmt_compact(summary["total_amount"]))
    c3.metric("Avg invoice", fmt_compact(summary["average_amount"]))
    c4.metric(
        "Duplicates",
        summary["duplicate_count"],
        f"{summary['duplicate_rate'] * 100:.1f}% of all",
        delta_color="inverse",
    )

    st.caption(
        f"This month: {summary['documents_this_month']} documents · "
        f"{fmt_money(summary['amount_this_month'])}"
    )
    st.divider()

    # Spend over time
    try:
        series = get_json("/api/v1/invoices/spend-over-time", {"granularity": "month"})
        if series:
            df = pd.DataFrame(series)
            df["amount"] = df["amount"].astype(float)
            st.subheader("Spend over time")
            st.line_chart(df.set_index("period")["amount"], height=260)
    except Exception as e:
        st.warning(f"spend-over-time unavailable: {e}")

    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("By document type")
        try:
            data = get_json("/api/v1/invoices/by-document-type")
            if data:
                df = pd.DataFrame(data)
                df["amount"] = df["amount"].astype(float)
                _colored_bar_chart(df, "label", "amount", height=240)
        except Exception as e:
            st.warning(str(e))

    with col_b:
        st.subheader("By currency")
        try:
            data = get_json("/api/v1/invoices/by-currency")
            if data:
                df = pd.DataFrame(data)
                df["amount"] = df["amount"].astype(float)
                _colored_bar_chart(df, "label", "amount", height=240)
        except Exception as e:
            st.warning(str(e))

    st.subheader("Top vendors")
    try:
        vendors = get_json("/api/v1/invoices/top-vendors", {"limit": 10})
        if vendors:
            df = pd.DataFrame(vendors)
            df["total_amount"] = df["total_amount"].astype(float)
            _colored_bar_chart(df, "vendor_name", "total_amount", height=280)
    except Exception as e:
        st.warning(str(e))


def _render_verdict(audit: dict):
    verdict = (audit.get("verdict") or "").lower()
    cls = {"approve": "v-approve", "flag": "v-flag", "reject": "v-reject"}.get(
        verdict, "v-flag"
    )
    st.markdown(
        f'<div class="verdict {cls}"><h2>{verdict or "—"}</h2>'
        f'Risk {audit.get("risk_score", "—")}/100</div>',
        unsafe_allow_html=True,
    )
    if audit.get("summary"):
        st.write(audit["summary"])
    if audit.get("recommended_action"):
        st.caption(audit["recommended_action"])
    violations = audit.get("violations") or []
    if violations:
        st.markdown("**Policy violations**")
        for v in violations:
            sev = v.get("severity", "info")
            st.markdown(
                f'<span class="pill p-{sev}">{sev}</span> '
                f'**{v.get("rule_id","")}** — {v.get("description","")}',
                unsafe_allow_html=True,
            )


def page_upload():
    st.header("Upload & Audit")
    st.write("Upload an invoice or reimbursement form to run the full pipeline.")

    endpoint = st.text_input("Audit endpoint", value="/api/v1/invoices/process")
    uploaded = st.file_uploader(
        "Choose a file", type=["pdf", "png", "jpg", "jpeg", "webp"]
    )

    if uploaded and st.button("Run audit", type="primary"):
        with st.spinner("Processing…"):
            try:
                result = post_file(endpoint, uploaded)
            except Exception as e:
                st.error(f"Upload failed: {e}")
                return

        # The pipeline may return a single object or a list of them.
        items = result if isinstance(result, list) else [result]
        for item in items:
            st.success(f"Processed {item.get('file_name', uploaded.name)}")
            extracted = item.get("extracted", {})
            validation = item.get("validation", {})
            audit = item.get("audit", {})

            left, right = st.columns([1, 1])
            with left:
                st.subheader("Extracted")
                fields = {
                    "Document type": extracted.get("document_type"),
                    "Invoice #": extracted.get("invoice_number"),
                    "Invoice date": extracted.get("invoice_date"),
                    "Vendor": extracted.get("vendor_name"),
                    "Currency": extracted.get("currency"),
                    "Total": fmt_money(
                        extracted.get("total_amount"), extracted.get("currency", "USD")
                    ),
                }
                st.table(pd.DataFrame(fields.items(), columns=["Field", "Value"]))
                if validation:
                    issues = validation.get("issues") or []
                    st.caption(
                        f"Validation: "
                        f"{'valid' if validation.get('is_valid') else 'invalid'} · "
                        f"completeness {int(validation.get('completeness_score', 0) * 100)}% · "
                        f"{len(issues)} issue(s)"
                    )
            with right:
                if audit:
                    _render_verdict(audit)
            st.divider()


def _load_invoices(params: dict) -> dict:
    return get_json("/api/v1/invoices", params)


def page_invoices():
    st.header("Invoices")

    with st.expander("Filters", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        vendor = c1.text_input("Vendor contains")
        doc_type = c2.selectbox("Type", ["", "invoice", "reimbursement"])
        currency = c3.selectbox("Currency", ["", "USD", "EUR", "GBP"])
        include_dup = c4.checkbox("Include duplicates", value=True)
        c5, c6, c7 = st.columns(3)
        verdict_filter = c5.selectbox("Verdict", ["", "approve", "flag", "reject"])
        risk_min = c6.number_input("Risk score min", min_value=0, max_value=100, value=0, step=1)
        risk_max = c7.number_input("Risk score max", min_value=0, max_value=100, value=100, step=1)
        c9, c10, c11 = st.columns(3)
        page = c9.number_input("Page", min_value=1, value=1, step=1)
        page_size = c10.selectbox("Page size", [10, 25, 50, 100], index=0)
        sort_by = c11.selectbox(
            "Sort by", ["created_at", "invoice_date", "total_amount", "vendor_name", "audit_risk_score"]
        )

    params = {
        "page": int(page),
        "page_size": int(page_size),
        "include_duplicates": include_dup,
        "sort_by": sort_by,
        "sort_dir": "desc",
    }
    if vendor:
        params["vendor"] = vendor
    if doc_type:
        params["document_type"] = doc_type
    if currency:
        params["currency"] = currency
    if verdict_filter:
        params["verdict"] = verdict_filter
    if risk_min > 0:
        params["risk_score_min"] = int(risk_min)
    if risk_max < 100:
        params["risk_score_max"] = int(risk_max)

    try:
        data = _load_invoices(params)
    except Exception as e:
        st.error(f"Couldn't load invoices: {e}")
        return

    rows = data.get("results", [])
    st.caption(
        f"{data.get('total', 0)} invoices · page {data.get('page', 1)} of "
        f"{data.get('total_pages', 1)}"
    )

    if not rows:
        st.info("No invoices match these filters.")
        return

    df = pd.DataFrame(rows)
    show_cols = [
        c
        for c in [
            "id", "invoice_number", "vendor_name", "document_type",
            "invoice_date", "total_amount", "currency", "is_duplicate",
            "audit_verdict", "audit_risk_score",
        ]
        if c in df.columns
    ]
    st.dataframe(df[show_cols], use_container_width=True, hide_index=True)

    # --- Invoice details -------------------------------------------------- #
    st.subheader("Invoice details")
    ids = [r["id"] for r in rows]
    selected = st.selectbox("Select an invoice id", ids)
    inv = next((r for r in rows if r["id"] == selected), None)
    if inv:
        d1, d2 = st.columns(2)
        with d1:
            st.markdown(f"**{inv.get('vendor_name','—')}**")
            st.write(
                {
                    "ID": inv.get("id"),
                    "File": inv.get("file_name"),
                    "Invoice #": inv.get("invoice_number"),
                    "Document type": inv.get("document_type"),
                    "Invoice date": inv.get("invoice_date"),
                    "Due date": inv.get("due_date"),
                }
            )
        with d2:
            st.metric(
                "Total",
                fmt_money(inv.get("total_amount"), inv.get("currency", "USD")),
            )
            st.write(
                {
                    "Subtotal": fmt_money(inv.get("subtotal"), inv.get("currency", "USD")),
                    "Tax": fmt_money(inv.get("tax"), inv.get("currency", "USD")),
                    "Currency": inv.get("currency"),
                    "Duplicate": inv.get("is_duplicate"),
                    "Duplicate of": inv.get("duplicate_of_id"),
                    "Created": inv.get("created_at"),
                }
            )


def page_duplicates():
    st.header("Duplicate invoices")
    try:
        data = get_json("/api/v1/invoices/duplicates")
    except Exception as e:
        st.error(f"Couldn't load duplicates: {e}")
        return
    rows = data.get("results", [])
    if not rows:
        st.success("No duplicates found. 🎉")
        return
    df = pd.DataFrame(rows)
    st.caption(f"{data.get('total', len(rows))} duplicate invoice(s)")
    cols = [
        c
        for c in [
            "id", "file_name", "vendor_name", "invoice_number",
            "total_amount", "currency", "duplicate_of_id", "created_at",
        ]
        if c in df.columns
    ]
    st.dataframe(df[cols], use_container_width=True, hide_index=True)


def page_reports():
    st.header("Reports")

    st.subheader("Spend report")
    options = ["vendor", "document_type","currency"]
    labels = {
    "vendor": "📄 Vendor",
    "document_type": "💸 Document Type",
    "currency": "↩️ Currency",
}
    # group_by = st.selectbox("Group by", ["vendor", "vendor", "currency"])
    group_by = st.selectbox("Group by", options,format_func=lambda v: labels[v],)
    try:
        report = get_json("/api/v1/invoices/spend", {"group_by": group_by})
        st.caption(
            f"Total {fmt_money(report.get('total_amount'))} across "
            f"{report.get('total_count', 0)} invoices"
        )
        rows = report.get("rows", [])
        if rows:
            df = pd.DataFrame(rows)
            df["amount"] = df["amount"].astype(float)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.bar_chart(df.set_index("label")["amount"], height=260)
    except Exception as e:
        st.error(f"Couldn't load spend report: {e}")

    # st.divider()
    # st.subheader("Export")
    # st.write("Download the full invoice ledger as CSV.")
    # if st.button("Fetch CSV"):
    #     try:
    #         r = requests.get(f"{api_base()}/reports/export.csv", timeout=60)
    #         r.raise_for_status()
    #         st.download_button(
    #             "Download invoices.csv",
    #             data=io.BytesIO(r.content),
    #             file_name="invoices.csv",
    #             mime="text/csv",
    #         )
    #     except Exception as e:
    #         st.error(f"Export failed: {e}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    section = sidebar()
    if section == "Dashboard":
        page_dashboard()
    elif section == "Upload & Audit":
        page_upload()
    elif section == "Invoices":
        page_invoices()
    elif section == "Duplicates":
        page_duplicates()
    elif section == "Reports":
        page_reports()


if __name__ == "__main__":
    main()