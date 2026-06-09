# LiraZ Tools — Backend

API REST que gerencia perfis de loja, autenticação OAuth com Mercado Livre,
e expõe a lógica de simulação/precificação/campanhas.

## Setup local

### Pré-requisitos
- Python 3.12+
- `uv` (gerenciador de pacotes moderno). Instalar:
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

### Instalação
```powershell
cd backend
uv venv
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
```

### Rodar em dev
```powershell
uv run uvicorn liraz_tools.main:app --reload --port 8000
```

Acessar:
- API: http://localhost:8000
- Docs OpenAPI: http://localhost:8000/docs
- Healthcheck: http://localhost:8000/health

### Comandos úteis
```powershell
# Linter
uv run ruff check src tests

# Formatter
uv run ruff format src tests

# Type check
uv run mypy src

# Testes
uv run pytest
```

## Estrutura do projeto

```
backend/
├── pyproject.toml
├── src/liraz_tools/
│   ├── main.py              # entry point FastAPI
│   ├── core/                # configurações e utilitários
│   │   ├── config.py        # settings via env
│   │   ├── logging.py       # structlog
│   │   ├── paths.py         # paths do AppData
│   │   └── crypto.py        # criptografia de credenciais
│   ├── domain/              # regras de negócio (DDD)
│   │   ├── profiles/        # perfis de loja
│   │   └── ...              # mais contextos virão depois
│   ├── infrastructure/      # adapters (DB, ML API, filesystem)
│   └── api/                 # camada HTTP
│       ├── deps.py          # dependências FastAPI
│       └── routes/          # endpoints por contexto
└── tests/
```

Convenções arquiteturais:
- `domain/` não importa de `infrastructure/` nem de `api/`
- `api/` importa de `domain/` e `infrastructure/`
- `infrastructure/` importa só de `domain/`

Esse é o padrão Clean Architecture / Hexagonal: domínio puro no centro,
adapters nas bordas.
