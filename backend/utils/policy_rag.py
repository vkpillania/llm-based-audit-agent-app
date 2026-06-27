"""RAG-based expense policy extractor.

Loads all PDF files from a folder, builds an in-memory vector store from their
text, then uses targeted retrieval + an LLM to populate each field of
ExpensePolicy from the document content rather than hardcoded defaults.

Usage:
    from utils.policy_rag import build_policy_from_folder

    policy = build_policy_from_folder("path/to/policy_docs/")
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import dotenv
import fitz  # pymupdf
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel, Field
from core.config import settings

dotenv.load_dotenv()

from utils.policy import ExpensePolicy  # noqa: E402  (run from utils/ dir)


# ---------------------------------------------------------------------------
# Pydantic model — used only for validating the parsed JSON response
# ---------------------------------------------------------------------------

class _ExtractedPolicy(BaseModel):
    category_limits: dict[str, float] = Field(default_factory=dict)
    receipt_required_threshold: float = Field(25.0)
    manager_review_threshold: float = Field(1000.0)
    disallowed_categories: list[str] = Field(default_factory=list)
    disallowed_keywords: list[str] = Field(default_factory=list)
    home_currency: str = Field("USD")
    max_expense_age_days: int = Field(90)


# ---------------------------------------------------------------------------
# Targeted retrieval queries — one per policy field
# ---------------------------------------------------------------------------

_FIELD_QUERIES: dict[str, str] = {
    "category_limits": (
        "expense category spending limits meals hotel airfare ground transport "
        "office supplies software entertainment maximum dollar cap per category"
    ),
    "receipt_required_threshold": (
        "receipt required threshold minimum amount documentation itemized receipt rule"
    ),
    "manager_review_threshold": (
        "manager approval review required threshold high-value large expense approval limit"
    ),
    "disallowed_categories": (
        "not reimbursable disallowed prohibited expense categories personal entertainment alcohol gifts"
    ),
    "disallowed_keywords": (
        "prohibited items keywords alcohol wine beer bar tab minibar cigarette tobacco gift card spa massage"
    ),
    "home_currency": (
        "home currency base currency reimbursement currency company currency foreign exchange"
    ),
    "max_expense_age_days": (
        "expense submission deadline age days stale late submission window reimbursement cutoff"
    ),
}

_SYSTEM_PROMPT = """\
You are a compliance analyst extracting structured data from corporate expense policy documents.

Given excerpts retrieved from the policy document, extract the following fields and return them \
as a single JSON object. Use the defaults below for any field not mentioned in the excerpts.

Expected JSON structure (return ONLY this, no markdown fences):
{
  "category_limits": {<category_name>: <dollar_cap_float>, ...},
  "receipt_required_threshold": <float>,
  "manager_review_threshold": <float>,
  "disallowed_categories": [<string>, ...],
  "disallowed_keywords": [<string>, ...],
  "home_currency": "<ISO-4217 code>",
  "max_expense_age_days": <int>
}

Defaults if not found: category_limits={}, receipt_required_threshold=25.0, \
manager_review_threshold=1000.0, disallowed_categories=[], disallowed_keywords=[], \
home_currency="USD", max_expense_age_days=90.\
"""


# ---------------------------------------------------------------------------
# Simple text splitter (avoids langchain_text_splitters dependency)
# ---------------------------------------------------------------------------

def _split_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class PolicyRAGPipeline:
    """Builds an ExpensePolicy by RAG-querying a folder of PDF policy documents.

    Steps:
      1. Load and chunk all PDFs in the folder.
      2. Embed chunks into an in-memory vector store.
      3. For each ExpensePolicy field, retrieve the top-k most relevant chunks.
      4. Send combined context to the LLM and parse the JSON response.
      5. Return a populated ExpensePolicy instance.
    """

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        top_k: int = 4,
        model: str | None = None,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.top_k = top_k
        self.model = model or os.getenv("AUDIT_MODEL", "gpt-4o-mini")
        self._embeddings = OpenAIEmbeddings(
            openai_api_key=settings.OPENAI_API_KEY,
            model="text-embedding-3-small",
            dimensions=1024,
        )
        self._llm = ChatOpenAI(openai_api_key=settings.OPENAI_API_KEY, model=self.model, temperature=0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_policy_from_folder(self, folder: str | Path) -> ExpensePolicy:
        """Load all PDFs in *folder* and return a populated ExpensePolicy."""
        folder = Path(folder)
        chunks = self._load_pdfs(folder)
        if not chunks:
            raise ValueError(
                f"No text could be extracted from PDFs in {str(folder)!r}. "
                "Check that the folder exists and contains readable PDF files."
            )
        store = self._build_vector_store(chunks)
        extracted = self._extract_fields(store)
        return self._to_policy(extracted)

    # ------------------------------------------------------------------
    # Step 1 — load and chunk PDFs
    # ------------------------------------------------------------------

    def _load_pdfs(self, folder: Path) -> list[str]:
        chunks: list[str] = []
        for pdf_path in sorted(folder.glob("*.pdf")):
            raw = _pdf_to_text(pdf_path)
            if raw.strip():
                chunks.extend(
                    _split_text(raw, self.chunk_size, self.chunk_overlap)
                )
        return chunks

    # ------------------------------------------------------------------
    # Step 2 — embed into an in-memory vector store
    # ------------------------------------------------------------------

    def _build_vector_store(self, texts: list[str]) -> InMemoryVectorStore:
        store = InMemoryVectorStore(embedding=self._embeddings)
        store.add_texts(texts)
        return store

    # ------------------------------------------------------------------
    # Step 3+4 — retrieve context per field, call LLM, parse JSON
    # ------------------------------------------------------------------

    def _extract_fields(self, store: InMemoryVectorStore) -> _ExtractedPolicy:
        context_sections: list[str] = []
        for field_name, query in _FIELD_QUERIES.items():
            docs = store.similarity_search(query, k=self.top_k)
            if docs:
                excerpts = "\n---\n".join(d.page_content for d in docs)
                context_sections.append(f"### {field_name}\n{excerpts}")

        combined = "\n\n".join(context_sections)
        msg = self._llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=combined),
            ]
        )
        raw = msg.content.strip()
        # Strip markdown fences if the model added them anyway.
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(raw)
        return _ExtractedPolicy(**data)

    # ------------------------------------------------------------------
    # Step 5 — map onto ExpensePolicy, falling back to defaults
    # ------------------------------------------------------------------

    @staticmethod
    def _to_policy(extracted: _ExtractedPolicy) -> ExpensePolicy:
        defaults = ExpensePolicy()
        return ExpensePolicy(
            category_limits=extracted.category_limits or defaults.category_limits,
            receipt_required_threshold=extracted.receipt_required_threshold,
            manager_review_threshold=extracted.manager_review_threshold,
            disallowed_categories=extracted.disallowed_categories or defaults.disallowed_categories,
            disallowed_keywords=extracted.disallowed_keywords or defaults.disallowed_keywords,
            home_currency=extracted.home_currency or defaults.home_currency,
            max_expense_age_days=extracted.max_expense_age_days,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pdf_to_text(path: Path) -> str:
    doc = fitz.open(str(path))
    pages = [page.get_text() for page in doc]
    doc.close()
    return "\n".join(pages)


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def build_policy_from_folder(folder: str | Path) -> ExpensePolicy:
    """Build an ExpensePolicy from all PDFs in *folder*.

    Requires OPENAI_API_KEY in the environment (or .env file).
    Falls back to ExpensePolicy defaults for any field not found in the PDFs.
    """
    return PolicyRAGPipeline().build_policy_from_folder(folder)


if __name__ == "__main__":
    import pprint
    pprint.pprint(build_policy_from_folder("./policies"))
