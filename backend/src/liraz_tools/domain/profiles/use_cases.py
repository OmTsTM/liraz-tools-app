"""Use cases do contexto Profiles.

Casos de uso = ações que o usuário do app pode disparar.
Cada um recebe dependências via construtor (DI explícita).
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable
from uuid import UUID

from liraz_tools.domain.profiles.entity import Profile, ProfileConfig, ProfileStatus
from liraz_tools.domain.profiles.repository import (
    ProfileRepository,
)


class ProfileCannotBeDeletedError(Exception):
    """Hard delete só é permitido em perfis DRAFT (nunca foram conectados).

    Perfis CONNECTED, DISCONNECTED ou ARCHIVED só podem ser arquivados
    (soft delete) — pra preservar histórico e prevenir perda acidental.
    """


class CreateProfileUseCase:
    """Cria um novo perfil em estado DRAFT (antes do OAuth)."""

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    async def execute(self, name: str) -> Profile:
        profile = Profile.create_draft(name=name)

        if await self._repo.exists_by_slug(profile.slug):
            # Se o slug já existe, anexa sufixo numérico até ser único
            base = profile.slug
            i = 2
            while await self._repo.exists_by_slug(f"{base}-{i}"):
                i += 1
            profile = profile.model_copy(update={"slug": f"{base}-{i}"})

        return await self._repo.add(profile)


class ListProfilesUseCase:
    """Lista todos os perfis (arquivados são opcionais)."""

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    async def execute(self, include_archived: bool = False) -> list[Profile]:
        return await self._repo.list_all(include_archived=include_archived)


class GetProfileUseCase:
    """Detalhe de um perfil por id."""

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    async def execute(self, profile_id: UUID) -> Profile:
        return await self._repo.get_by_id(profile_id)


class UpdateProfileConfigUseCase:
    """Atualiza configurações de um perfil (alíquota, CEP, paths, etc)."""

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    async def execute(
        self,
        profile_id: UUID,
        new_name: str | None = None,
        new_config: ProfileConfig | None = None,
        *,
        permitir_custos_path: bool = False,
    ) -> Profile:
        profile = await self._repo.get_by_id(profile_id)

        if new_name is not None:
            profile.name = new_name.strip()

        if new_config is not None:
            if not permitir_custos_path:
                # custos_xlsx_path E tarifas_ml_xlsx_path só são graváveis via
                # endpoints de upload validados (config_files.py). Aqui ignoramos
                # qualquer valor vindo do cliente pra não permitir apontar pra
                # arquivo arbitrário no disco.
                new_config = new_config.model_copy(update={
                    "custos_xlsx_path": profile.config.custos_xlsx_path,
                    "tarifas_ml_xlsx_path": profile.config.tarifas_ml_xlsx_path,
                })
            profile.config = new_config

        return await self._repo.update(profile)


class ArchiveProfileUseCase:
    """Soft delete — marca como arquivado mas não apaga dados."""

    def __init__(self, repo: ProfileRepository) -> None:
        self._repo = repo

    async def execute(self, profile_id: UUID) -> Profile:
        profile = await self._repo.get_by_id(profile_id)
        profile.archive()
        return await self._repo.update(profile)


class UnarchiveProfileUseCase:
    """Restaura um perfil arquivado.

    Modo "inteligente": se a loja tem credenciais salvas (arquivos
    `app_credentials.enc` no perfil), volta pra CONNECTED. Senão, volta
    pra DRAFT (precisa reconfigurar).

    O callback `check_has_credentials` é injetado pra desacoplar o domínio
    da infraestrutura (filesystem).
    """

    def __init__(
        self,
        repo: ProfileRepository,
        check_has_credentials: CredentialsCheckCallback | None = None,
    ) -> None:
        self._repo = repo
        self._check = check_has_credentials

    async def execute(self, profile_id: UUID) -> Profile:
        profile = await self._repo.get_by_id(profile_id)

        if profile.status != ProfileStatus.ARCHIVED:
            raise ProfileNotArchivedError(
                f"perfil '{profile.name}' não está arquivado (status: {profile.status.value})"
            )

        # Verifica se há credenciais — define se volta pra CONNECTED ou DRAFT
        has_creds = (
            self._check(profile.slug) if self._check is not None else False
        )

        if has_creds and profile.ml_user_id is not None:
            profile.unarchive_to_connected()
        else:
            profile.unarchive_to_draft()

        return await self._repo.update(profile)


class ProfileNotArchivedError(Exception):
    """Tentativa de desarquivar perfil que não está arquivado."""


class DeleteProfileUseCase:
    """Hard delete — apaga o perfil permanentemente do banco.

    SÓ funciona pra perfis em estado DRAFT (nunca foram conectados ao ML).
    Pra outros estados, lança ProfileCannotBeDeletedError — use Archive em vez.

    Motivo: perfis conectados podem ter histórico, snapshots, custos
    associados. Apagar permanentemente sem aviso é destrutivo demais.

    Também apaga os arquivos do perfil (credenciais e tokens.db, se existirem),
    via cleanup_files_callback opcional. Mantemos o callback opcional pra
    desacoplar o domínio da infraestrutura.
    """

    def __init__(
        self,
        repo: ProfileRepository,
        cleanup_files_callback: CleanupFilesCallback | None = None,
    ) -> None:
        self._repo = repo
        self._cleanup = cleanup_files_callback

    async def execute(self, profile_id: UUID) -> None:
        profile = await self._repo.get_by_id(profile_id)
        if profile.status != ProfileStatus.DRAFT:
            raise ProfileCannotBeDeletedError(
                f"perfil '{profile.name}' está em estado '{profile.status.value}' "
                f"e só pode ser arquivado. Apenas perfis DRAFT podem ser apagados."
            )
        await self._repo.delete(profile_id)

        # Limpa arquivos do perfil também (best-effort — não bloqueia se falhar)
        if self._cleanup is not None:
            with contextlib.suppress(Exception):
                self._cleanup(profile.slug)


# Tipo do callback de cleanup — recebe slug e apaga arquivos do perfil
CleanupFilesCallback = Callable[[str], None]
CredentialsCheckCallback = Callable[[str], bool]
