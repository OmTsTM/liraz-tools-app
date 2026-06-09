"""Endpoints OAuth.

Rotas:
  POST /api/profiles/{id}/oauth/credentials   — salva CLIENT_ID/SECRET
  GET  /api/profiles/{id}/oauth/authorize-url — gera URL pro navegador
  POST /api/profiles/{id}/oauth/import-mcp    — importa de MCP existente
  POST /api/profiles/{id}/oauth/disconnect    — desconecta loja
  GET  /api/profiles/{id}/ml-test             — testa conexão
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from liraz_tools.api.deps import (
    CredsRepo,
    PendingStore,
    ProfileRepo,
    require_admin_in_profile,
)
from liraz_tools.api.schemas.oauth_schemas import (
    AuthorizationUrlResponse,
    ImportFromMCPRequest,
    MLConnectionTestResponse,
    SaveCredentialsRequest,
)
from liraz_tools.api.schemas.profile_schemas import ProfileResponse
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.oauth.entity import AppCredentials
from liraz_tools.domain.oauth.use_cases import (
    DisconnectUseCase,
    GenerateAuthorizationUrlUseCase,
    ImportFromMCPUseCase,
    MCPImportError,
    OAuthError,
    SaveAppCredentialsUseCase,
    TestMLConnectionUseCase,
)
from liraz_tools.domain.profiles.repository import ProfileNotFoundError
from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
    OAuthCredentialsNotFoundError,
)

router = APIRouter(
    prefix="/api/profiles",
    tags=["oauth"],
    # OAuth da loja é config sensível (CLIENT_ID/SECRET) — só admin da loja
    # (ou admin global do sistema, que bypassa) pode mexer/ver.
    dependencies=[Depends(require_admin_in_profile)],
)
logger = get_logger(__name__)


@router.post(
    "/{profile_id}/oauth/credentials",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def save_credentials(
    profile_id: UUID,
    body: SaveCredentialsRequest,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
) -> None:
    """Salva CLIENT_ID/SECRET da aplicação ML pra um perfil."""
    try:
        creds = AppCredentials(
            client_id=body.client_id,
            client_secret=body.client_secret,
            redirect_uri=body.redirect_uri,
        )
        use_case = SaveAppCredentialsUseCase(profile_repo, creds_repo)
        await use_case.execute(profile_id, creds)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get(
    "/{profile_id}/oauth/authorize-url",
    response_model=AuthorizationUrlResponse,
)
async def get_authorization_url(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    pending_store: PendingStore,
) -> AuthorizationUrlResponse:
    """Gera URL pra autorizar no ML."""
    try:
        use_case = GenerateAuthorizationUrlUseCase(
            profile_repo, creds_repo, pending_store
        )
        url = await use_case.execute(profile_id)
        return AuthorizationUrlResponse(url=url)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except OAuthCredentialsNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{e}. Salve CLIENT_ID/SECRET via "
                f"POST /api/profiles/{profile_id}/oauth/credentials primeiro."
            ),
        ) from e


@router.post(
    "/{profile_id}/oauth/import-mcp",
    response_model=ProfileResponse,
)
async def import_from_mcp(
    profile_id: UUID,
    body: ImportFromMCPRequest,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
) -> ProfileResponse:
    """Importa credenciais de projeto MCP existente."""
    use_case = ImportFromMCPUseCase(profile_repo, creds_repo)
    try:
        profile = await use_case.execute(
            profile_id=profile_id,
            tokens_db_path=body.tokens_db_path,
            client_id=body.client_id,
            client_secret=body.client_secret,
            redirect_uri=body.redirect_uri,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except MCPImportError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return ProfileResponse.from_domain(profile)


@router.post("/{profile_id}/oauth/disconnect", response_model=ProfileResponse)
async def disconnect(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
) -> ProfileResponse:
    """Remove credenciais e tokens."""
    try:
        use_case = DisconnectUseCase(profile_repo, creds_repo)
        profile = await use_case.execute(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return ProfileResponse.from_domain(profile)


@router.get("/{profile_id}/ml-test", response_model=MLConnectionTestResponse)
async def test_ml_connection(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
) -> MLConnectionTestResponse:
    """Testa conexão fazendo GET /users/me no ML."""
    try:
        use_case = TestMLConnectionUseCase(profile_repo, creds_repo)
        me = await use_case.execute(profile_id)
        return MLConnectionTestResponse(**me)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (OAuthError, OAuthCredentialsNotFoundError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
