# syntax=docker/dockerfile:1.7

# ============================================================================
# Stage 1 — Frontend build (Vite → static assets)
# ============================================================================
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /build

# Habilita corepack (pnpm sem instalar global)
RUN corepack enable

# Manifests primeiro pra aproveitar layer cache na maioria das builds
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./frontend/
COPY frontend/biome.json frontend/tsconfig.json frontend/tailwind.config.ts frontend/postcss.config.js frontend/vite.config.ts frontend/index.html ./frontend/

WORKDIR /build/frontend

RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# Agora o código (rebuild só se mudou algo aqui)
COPY frontend/src ./src
COPY frontend/public ./public

# Build → /build/frontend/dist
RUN pnpm build

# ============================================================================
# Stage 2 — Backend runtime (Python 3.12 + uv)
# ============================================================================
FROM python:3.12-slim-bookworm AS backend

# Não escrever .pyc + flush stdout direto (mais útil em log Render)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend/src

# Instala uv (curl deps já presentes no slim, mas certifica)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /app

# Manifests do backend primeiro pra cache do install
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./backend/

# Instala deps no venv local do projeto. `--no-install-project` pula instalar
# o pacote do app — ele entra junto com o source via PYTHONPATH.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project backend --frozen --no-install-project

# Source do backend
COPY backend/src ./backend/src

# SPA buildada do stage anterior
COPY --from=frontend-builder /build/frontend/dist /app/frontend-dist

# Render injeta $PORT; default 8000 pra rodar local. Bind 0.0.0.0 obrigatório.
ENV LIRAZ_TOOLS_SPA_DIST_DIR=/app/frontend-dist \
    LIRAZ_TOOLS_HOST=0.0.0.0 \
    LIRAZ_TOOLS_DATA_DIR=/data \
    PORT=8000

# Volume pra persistir SQLite e arquivos por perfil quando o plano do Render
# tiver disco persistente (free não tem; o usuário precisa habilitar)
VOLUME ["/data"]

EXPOSE 8000

# `--proxy-headers` faz uvicorn confiar em X-Forwarded-* (atrás do LB do Render)
# `--workers 1` porque o app usa schedulers in-process; mais workers
# disparariam o scheduler N vezes
CMD ["sh", "-c", "uv run --project backend uvicorn liraz_tools.main:app --host $LIRAZ_TOOLS_HOST --port $PORT --proxy-headers --forwarded-allow-ips='*' --workers 1"]
