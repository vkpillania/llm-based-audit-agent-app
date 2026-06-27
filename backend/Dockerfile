# ============ Stage 1: Builder ============
# Use a build stage to compile dependencies without bloating the final image

FROM python:3.11-slim AS builder

# Set environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app 
# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /app/requirements.txt
COPY . .

# ✅ Install uv AND add it to PATH (in same layer)
ENV PATH="/root/.local/bin:$PATH"
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN uv pip install --system --no-cache-dir -r requirements.txt

EXPOSE 8000:8000


# ✅ Right — binds to 0.0.0.0
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000 --reload"]
