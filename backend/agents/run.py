import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.orm import Session
from sqlalchemy import text
# from agents.validation_agent import ValidationAgent
# from schemas import invoices
# from schemas.invoices import ExtractedDocument
# from agents.ocr_agent import OCRAgent
# from agents.audit_agent import AuditAgent
# from agents.graph import ExpenseAuditGraph
from database.session import SessionLocal


# from utils.policy import DEFAULT_POLICY
from utils.secrets_manager import get_secret

import hashlib
# ocr = OCRAgent()
# invoice_data = None
# file_hash = None
# file_bytes = None
# with open(os.path.join(os.path.dirname(__file__), "../samples/DUPLICATE_of_first.pdf"), "rb") as f:
#         file_bytes = f.read()
#         file_hash = hashlib.sha256(file_bytes).hexdigest()
#         # invoice_data = ocr.extract_invoice_fields(
        #     file_bytes=file_bytes,
        #     file_name="invoices.pdf",
        # )
# import pprint
# pprint.pprint(invoice_data)
# print(type(invoice_data))
# invoice_data = ExtractedDocument(**invoice_data)
# validator = ValidationAgent()
db: Session = SessionLocal()
print("db ===", db.execute(text("select current_timestamp")).fetchone())
# valid = validator.run(extracted=invoice_data, db=db, file_hash=file_hash)
# auditor = AuditAgent(policy=DEFAULT_POLICY)
# print("=================" ,auditor.summerize_audit(extracted=invoice_data,validation=valid))
# print(get_secret("open-api-key", "us-east-1"))
# print(get_secret("open-api-key", "ap-south-1"))
# print(get_secret("db-creds", "ap-south-1"))

# graph = ExpenseAuditGraph(policy = DEFAULT_POLICY)
# graph.run(file_bytes=file_bytes , file_name="invoices.pdf" , db=db)