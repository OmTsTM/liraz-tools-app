"""Endpoint de upload da planilha de custos (.xlsx) por perfil.

Fluxo:
1. Frontend faz POST multipart/form-data com o .xlsx
2. Backend valida que é XLSX válido (openpyxl consegue abrir + tem aba 'Produtos')
3. Salva em <data>/_shared/custos-<slug>-<timestamp>.xlsx
4. Atualiza config.custos_xlsx_path do perfil com o caminho absoluto

Por que <data>/_shared/ e não dentro do perfil?
Cada loja pode usar uma planilha diferente OU compartilhar. Pra MVP fica em
_shared (mais simples). Se virar problema, mudamos pra <profile>/custos.xlsx.
"""
from __future__ import annotations

import time
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from liraz_tools.api.deps import ProfileRepo
from liraz_tools.api.schemas.pricing_schemas import UploadCustosXLSXResponse
from liraz_tools.core.logging import get_logger
from liraz_tools.core.paths import get_shared_dir
from liraz_tools.domain.profiles.repository import ProfileNotFoundError
from liraz_tools.domain.profiles.use_cases import UpdateProfileConfigUseCase
from liraz_tools.infrastructure.pricing.costs_loader import (
    CustosXLSXError,
    carregar_custos,
    carregar_tarifas_ml,
)

router = APIRouter(prefix="/api/profiles", tags=["config"])
logger = get_logger(__name__)


MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB — planilha de custos não passa disso


@router.post(
    "/{profile_id}/config/custos-xlsx",
    response_model=UploadCustosXLSXResponse,
)
async def upload_custos_xlsx(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    file: UploadFile = File(...),
) -> UploadCustosXLSXResponse:
    """Faz upload do .xlsx de custos e atualiza o path do perfil."""
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Arquivo deve ser .xlsx (recebido: {file.filename})",
        )

    # Lê com cap de tamanho
    contents = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Arquivo maior que {MAX_UPLOAD_SIZE // (1024*1024)}MB",
        )

    # Carrega perfil pra pegar slug
    try:
        profile = await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    # Salva em _shared/
    ts = int(time.time())
    saved_path = get_shared_dir() / f"custos-{profile.slug}-{ts}.xlsx"
    saved_path.write_bytes(contents)

    # Valida que é XLSX com schema esperado
    try:
        custos_map = carregar_custos(saved_path)
    except CustosXLSXError as e:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Planilha inválida: {e}",
        ) from e

    # Atualiza config.custos_xlsx_path do perfil
    new_config = profile.config.model_copy(
        update={"custos_xlsx_path": str(saved_path)}
    )
    use_case = UpdateProfileConfigUseCase(profile_repo)
    # Único ponto autorizado a gravar custos_xlsx_path (caminho já validado acima).
    await use_case.execute(
        profile_id, new_config=new_config, permitir_custos_path=True,
    )

    logger.info(
        "custos_xlsx_uploaded",
        profile_id=str(profile_id),
        saved_path=str(saved_path),
        skus=len(custos_map),
    )

    return UploadCustosXLSXResponse(
        saved_path=str(saved_path),
        size_bytes=len(contents),
        skus_carregados=len(custos_map),
    )


@router.post(
    "/{profile_id}/config/tarifas-ml-xlsx",
    response_model=UploadCustosXLSXResponse,
)
async def upload_tarifas_ml_xlsx(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    file: UploadFile = File(...),
) -> UploadCustosXLSXResponse:
    """Upload da planilha de tarifas reais por anúncio (export da extensão Chrome).

    Schema esperado: aba 'TarifasML' (ou 1ª aba) com colunas A=MLB, B=Frete,
    C=Custo fixo. Quando o item está cadastrado aqui, o calculator usa esses
    valores em vez do teto teórico (6,75/8,55) E pula a chamada
    `/items/{id}/shipping_options` (economia de call por item).
    """
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Arquivo deve ser .xlsx (recebido: {file.filename})",
        )
    contents = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Arquivo maior que {MAX_UPLOAD_SIZE // (1024*1024)}MB",
        )

    try:
        profile = await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    ts = int(time.time())
    saved_path = get_shared_dir() / f"tarifas-ml-{profile.slug}-{ts}.xlsx"
    saved_path.write_bytes(contents)

    # Valida: precisa ter pelo menos 1 linha válida
    tarifas_map = carregar_tarifas_ml(saved_path)
    if len(tarifas_map) == 0:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Planilha vazia ou inválida. Esperado: aba 'TarifasML' (ou 1ª aba) "
                "com colunas A=MLB, B=Frete, C=Custo fixo e pelo menos 1 linha "
                "começando com 'MLB' no item_id."
            ),
        )

    new_config = profile.config.model_copy(
        update={"tarifas_ml_xlsx_path": str(saved_path)}
    )
    use_case = UpdateProfileConfigUseCase(profile_repo)
    await use_case.execute(
        profile_id, new_config=new_config, permitir_custos_path=True,
    )

    logger.info(
        "tarifas_ml_xlsx_uploaded",
        profile_id=str(profile_id),
        saved_path=str(saved_path),
        anuncios=len(tarifas_map),
    )

    return UploadCustosXLSXResponse(
        saved_path=str(saved_path),
        size_bytes=len(contents),
        skus_carregados=len(tarifas_map),
    )
