FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Install the hashed production lock before copying source, so a code change
# does not invalidate the dependency layer. Nothing newer than the lock is resolved.
RUN pip install --upgrade pip && pip install --require-hashes -r requirements.txt

COPY . .
# Editable: /app stays the one copy of `app`, so VERSION and alembic.ini resolve
# next to it. A regular install would add a second copy in site-packages that
# finds neither. An editable install never writes to the tree.
RUN pip install --no-deps -e .

# Non-root. The app never needs to write to its own source tree.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
