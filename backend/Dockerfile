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
# VERSION and the release notes sit at the repository root, outside the backend/
# build context. They arrive through the named additional build context `repo`:
#   docker buildx build --build-context repo=. backend
# (compose: additional_contexts; the release workflow: build-contexts).
COPY --from=repo VERSION /app/VERSION
COPY --from=repo docs/releases/ /app/releases/

# Build-time identity. Declared after the dependency layers so a new version or
# SHA does not invalidate them.
ARG APP_VERSION
ARG GIT_SHA
ENV GIT_SHA=$GIT_SHA
LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_SHA
# A release build whose APP_VERSION differs from the VERSION file is a mislabelled image.
RUN if [ -n "$APP_VERSION" ] && [ "$APP_VERSION" != "$(cat /app/VERSION)" ]; then \
        echo "APP_VERSION=$APP_VERSION does not match /app/VERSION=$(cat /app/VERSION)" >&2; \
        exit 1; \
    fi

# Editable: /app stays the one copy of `app`, so VERSION and alembic.ini resolve
# next to it. A regular install would add a second copy in site-packages that
# finds neither. An editable install never writes to the tree.
RUN pip install --no-deps -e .

# Non-root. The app never needs to write to its own source tree.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
