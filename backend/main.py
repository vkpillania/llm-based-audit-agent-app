from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.config import settings
from database.session import get_db

from routers.invoice import invoice_router

app = FastAPI()
API_PREFIX = "/api/v1"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Your Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
) 
app.include_router(invoice_router, prefix=API_PREFIX)

@app.get("/api/v1/health")
def health():
    return "OK"
# app.include_router(members.router, prefix=API_PREFIX)
# app.include_router(borrow_records.router, prefix=API_PREFIX)
# app.include_router(auth.auth_router, prefix=API_PREFIX)

