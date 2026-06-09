"""Endpoints REST de Profile.

Endpoints:
  GET    /api/profiles           — lista todas
  POST   /api/profiles           — cria nova (DRAFT)
  GET    /api/profiles/active    — perfil ativo
  POST   /api/profiles/{id}/activate — marca como ativo
  GET    /api/profiles/{id}      — detalhe
  PATCH  /api/profiles/{id}      — atualiza
  DELETE /api/profiles/{id}      — arquiva (soft delete)
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from liraz_tools.api.deps import (
    AccessRepo,
    AdminUser,
    CurrentUser,
    ProfileRepo,
    RequireAdminProfile,
    RequireViewer,
    SettingsRepo,
)
from liraz_tools.api.schemas.profile_schemas import (
    ActiveProfileResponse,
    CreateProfileRequest,
    ListProfilesResponse,
    ProfileResponse,
    UpdateProfileRequest,
)
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.profiles.repository import (
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
)
from liraz_tools.domain.profiles.use_cases import (
    ArchiveProfileUseCase,
    CreateProfileUseCase,
    DeleteProfileUseCase,
    GetProfileUseCase,
    ListProfilesUseCase,
    ProfileCannotBeDeletedError,
    ProfileNotArchivedError,
    UnarchiveProfileUseCase,
    UpdateProfileConfigUseCase,
)
from liraz_tools.infrastructure.repositories.settings_repository import (
    ACTIVE_PROFILE_KEY,
)

router = APIRouter(prefix="/api/profiles", tags=["profiles"])
logger = get_logger(__name__)


@router.get("", response_model=ListProfilesResponse)
async def list_profiles(
    repo: ProfileRepo,
    user: CurrentUser,
    access_repo: AccessRepo,
    include_archived: bool = Query(False, description="Incluir lojas arquivadas."),
) -> ListProfilesResponse:
    """Lista lojas que o usuário tem acesso. Admin global vê todas."""
    use_case = ListProfilesUseCase(repo)
    profiles = await use_case.execute(include_archived=include_archived)
    if not user.is_admin:
        # Filtra pra só as lojas em que o user tem acesso (ACL granular).
        access_rows = await access_repo.list_for_user(user.id)
        allowed = {a.profile_id for a in access_rows}
        profiles = [p for p in profiles if p.id in allowed]
    return ListProfilesResponse(
        items=[ProfileResponse.from_domain(p) for p in profiles],
        total=len(profiles),
    )


@router.post(
    "",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    body: CreateProfileRequest,
    repo: ProfileRepo,
    _admin: AdminUser,
) -> ProfileResponse:
    """Cria uma loja nova (estado DRAFT, aguardando OAuth). Admin global only."""
    use_case = CreateProfileUseCase(repo)
    try:
        profile = await use_case.execute(name=body.name)
    except ProfileAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    logger.info("profile_created", profile_id=str(profile.id), name=profile.name)
    return ProfileResponse.from_domain(profile)


@router.get("/active", response_model=ActiveProfileResponse)
async def get_active_profile(
    profile_repo: ProfileRepo,
    settings_repo: SettingsRepo,
    user: CurrentUser,
    access_repo: AccessRepo,
) -> ActiveProfileResponse:
    """Retorna o perfil ativo (último selecionado pelo usuário).

    Se o user atual não tem acesso à loja ativa (admin trocou ACL),
    retorna None — frontend deve cair pro seletor de lojas.
    """
    active_id_str = await settings_repo.get(ACTIVE_PROFILE_KEY)
    if not active_id_str:
        return ActiveProfileResponse(profile=None)

    try:
        active_id = UUID(active_id_str)
        profile = await profile_repo.get_by_id(active_id)
    except (ProfileNotFoundError, ValueError):
        # Perfil ativo foi deletado ou ID corrompido — limpa setting
        await settings_repo.delete(ACTIVE_PROFILE_KEY)
        return ActiveProfileResponse(profile=None)

    # Filtra pela ACL — non-admin só vê se tem linha em access
    if not user.is_admin:
        role = await access_repo.get_role(user_id=user.id, profile_id=active_id)
        if role is None:
            return ActiveProfileResponse(profile=None)

    return ActiveProfileResponse(profile=ProfileResponse.from_domain(profile))


@router.post("/{profile_id}/activate", response_model=ActiveProfileResponse)
async def activate_profile(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    settings_repo: SettingsRepo,
    _role: RequireViewer,
) -> ActiveProfileResponse:
    """Marca um perfil como o ativo atual (viewer+ na loja)."""
    try:
        profile = await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    await settings_repo.set(ACTIVE_PROFILE_KEY, str(profile.id))
    logger.info("profile_activated", profile_id=str(profile.id), name=profile.name)
    return ActiveProfileResponse(profile=ProfileResponse.from_domain(profile))


@router.get("/{profile_id}", response_model=ProfileResponse)
async def get_profile(
    profile_id: UUID, repo: ProfileRepo, _role: RequireViewer,
) -> ProfileResponse:
    """Detalhe de um perfil por id (viewer+ na loja)."""
    use_case = GetProfileUseCase(repo)
    try:
        profile = await use_case.execute(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return ProfileResponse.from_domain(profile)


@router.patch("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: UUID,
    body: UpdateProfileRequest,
    repo: ProfileRepo,
    _role: RequireAdminProfile,
) -> ProfileResponse:
    """Atualiza nome ou config de um perfil. Só admin da loja (ou global)."""
    use_case = UpdateProfileConfigUseCase(repo)
    new_config = body.config.to_domain() if body.config else None
    try:
        profile = await use_case.execute(
            profile_id=profile_id,
            new_name=body.name,
            new_config=new_config,
        )
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    logger.info("profile_updated", profile_id=str(profile.id))
    return ProfileResponse.from_domain(profile)


@router.post("/{profile_id}/archive", response_model=ProfileResponse)
async def archive_profile(
    profile_id: UUID,
    repo: ProfileRepo,
    _admin: AdminUser,
) -> ProfileResponse:
    """Arquiva uma loja (soft delete). Admin global only."""
    use_case = ArchiveProfileUseCase(repo)
    try:
        profile = await use_case.execute(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    logger.info("profile_archived", profile_id=str(profile.id))
    return ProfileResponse.from_domain(profile)


@router.post("/{profile_id}/unarchive", response_model=ProfileResponse)
async def unarchive_profile(
    profile_id: UUID,
    repo: ProfileRepo,
    _admin: AdminUser,
) -> ProfileResponse:
    """Desarquiva uma loja.

    Modo inteligente: se a loja tem credenciais salvas (arquivo
    app_credentials.enc), volta pra CONNECTED. Senão, volta pra DRAFT
    (precisará reconfigurar).
    """
    from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
        PerProfileCredentialsRepository,
    )

    creds_repo = PerProfileCredentialsRepository()
    use_case = UnarchiveProfileUseCase(
        repo,
        check_has_credentials=creds_repo.has_credentials,
    )
    try:
        profile = await use_case.execute(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ProfileNotArchivedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    logger.info(
        "profile_unarchived",
        profile_id=str(profile.id),
        new_status=profile.status.value,
    )
    return ProfileResponse.from_domain(profile)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: UUID,
    repo: ProfileRepo,
    _admin: AdminUser,
) -> None:
    """Hard delete — apaga o perfil permanentemente.

    Só funciona pra perfis em estado DRAFT (nunca conectados ao ML).
    Pra perfis conectados/desconectados/arquivados, use POST /archive.
    """
    from liraz_tools.infrastructure.repositories.per_profile_credentials_repository import (
        PerProfileCredentialsRepository,
    )

    creds_repo = PerProfileCredentialsRepository()
    use_case = DeleteProfileUseCase(
        repo,
        cleanup_files_callback=creds_repo.delete_all,
    )
    try:
        await use_case.execute(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ProfileCannotBeDeletedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    logger.info("profile_deleted", profile_id=str(profile_id))
