"""Callback público do OAuth.

O ML redireciona pra cá com ?code=...&state=... depois que o usuário
autoriza. Não fica sob /api/profiles porque é um endpoint público
(precisa ser exatamente o que está cadastrado no painel dev do ML).
"""
from __future__ import annotations

import html

from fastapi import APIRouter, Query, status
from fastapi.responses import HTMLResponse

from liraz_tools.api.deps import CredsRepo, PendingStore, ProfileRepo
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.oauth.use_cases import (
    CompleteOAuthFlowUseCase,
    InvalidStateError,
    OAuthError,
)

router = APIRouter(prefix="/api/auth", tags=["oauth-callback"])
logger = get_logger(__name__)


_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Conectado — LiraZ Tools</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; margin: 0; background: #f7f7f8; color: #1a1a1a;
    }}
    .card {{
      background: white; padding: 48px 56px; border-radius: 12px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.08), 0 8px 24px rgba(0,0,0,0.06);
      max-width: 420px; text-align: center;
    }}
    .icon {{ font-size: 48px; margin-bottom: 16px; }}
    h1 {{ font-size: 20px; font-weight: 600; margin: 0 0 8px; }}
    p {{ color: #666; font-size: 14px; margin: 8px 0; line-height: 1.5; }}
    .info {{ background: #f0f7ff; border-radius: 6px; padding: 12px; margin-top: 16px;
            font-size: 13px; color: #0066cc; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✓</div>
    <h1>Loja conectada</h1>
    <p>{nickname}</p>
    <p class="info">Você pode fechar esta aba e voltar pro app.</p>
  </div>
</body>
</html>"""


_ERROR_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Erro — LiraZ Tools</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; margin: 0; background: #f7f7f8; color: #1a1a1a;
    }}
    .card {{
      background: white; padding: 48px 56px; border-radius: 12px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.08), 0 8px 24px rgba(0,0,0,0.06);
      max-width: 480px; text-align: center;
    }}
    .icon {{ font-size: 48px; margin-bottom: 16px; color: #d32f2f; }}
    h1 {{ font-size: 20px; font-weight: 600; margin: 0 0 8px; }}
    p {{ color: #666; font-size: 14px; margin: 8px 0; line-height: 1.5; }}
    code {{ font-family: SF Mono, Menlo, monospace; background: #f5f5f5;
            padding: 8px 12px; border-radius: 4px; font-size: 13px;
            display: block; margin-top: 12px; word-break: break-word; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">⚠</div>
    <h1>Não foi possível conectar</h1>
    <p>{message}</p>
    <code>{detail}</code>
    <p style="margin-top: 24px;">Volte pro app e tente novamente.</p>
  </div>
</body>
</html>"""


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    pending_store: PendingStore,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> HTMLResponse:
    """Callback OAuth do ML.

    ML chama com:
      ?code=...&state=...           (sucesso)
      ?error=access_denied&state=...  (usuário negou)
    """
    if error:
        logger.info("oauth_callback_user_denied", error=error)
        return HTMLResponse(
            _ERROR_HTML.format(
                message="Autorização recusada no Mercado Livre.",
                # error/error_description vêm de query params — escapar pra evitar XSS.
                detail=html.escape(error_description or error),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not code or not state:
        return HTMLResponse(
            _ERROR_HTML.format(
                message="Callback inválido — code ou state faltando.",
                detail="Verifique se a Redirect URI cadastrada na aplicação ML "
                "está correta e tente novamente.",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    use_case = CompleteOAuthFlowUseCase(profile_repo, creds_repo, pending_store)
    try:
        profile = await use_case.execute(code=code, state=state)
    except InvalidStateError as e:
        logger.warning("oauth_callback_invalid_state")
        return HTMLResponse(
            _ERROR_HTML.format(
                message="Sessão de autorização expirada ou inválida.",
                detail=html.escape(str(e)),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except OAuthError as e:
        logger.warning("oauth_callback_failed", error=str(e))
        return HTMLResponse(
            _ERROR_HTML.format(
                message="Falha ao concluir autorização.",
                detail=html.escape(str(e)),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # nickname vem do ML — escapar antes de injetar no HTML.
    nickname = profile.ml_nickname or f"User #{profile.ml_user_id}"
    return HTMLResponse(_SUCCESS_HTML.format(nickname=html.escape(nickname)))
