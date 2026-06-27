
from pathlib import Path
 
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
 
app = FastAPI(title="File Upload API", version="1.0.0")
 
# --- configuration ---------------------------------------------------------
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
 
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".csv", ".xlsx"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_BYTES = 25 * 1024 * 1024  # 25 MB
CHUNK = 1024 * 1024  # 1 MB read chunks
 
def validate(upload: UploadFile) -> str:
    """Validate extension + content-type; return the lowercased extension."""
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Extension '{ext or '(none)'}' not allowed. "
            f"Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )
    if upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Content-type '{upload.content_type}' not allowed.",
        )
    return ext