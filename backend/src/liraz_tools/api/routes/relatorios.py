"""Endpoint manual do relatório diário de vendas (Fatia 1).

GET /api/profiles/{profile_id}/relatorio?dia=YYYY-MM-DD → devolve PDF.

Sem scheduler, sem WhatsApp — o user dispara manualmente quando quer.
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from liraz_tools.api.deps import CredsRepo, DbSession, ProfileRepo
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.profiles.repository import ProfileNotFoundError
from liraz_tools.domain.relatorio.use_cases import (
    GerarRelatorioDiarioUseCase,
    ProfileNaoConectadoError,
    SemCustosXlsxError,
)
from liraz_tools.infrastructure.relatorio.pdf_renderer import renderizar_pdf
from liraz_tools.infrastructure.relatorio.pdf_storage import (
    ler_pdf_por_filename,
    listar_pdfs,
    salvar_pdf,
)
from liraz_tools.infrastructure.repositories.relatorio_diario_repository import (
    RelatorioDiarioRepository,
)

router = APIRouter(prefix="/api/profiles", tags=["relatorios"])
logger = get_logger(__name__)


@router.get("/{profile_id}/relatorio")
async def baixar_relatorio_diario(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    session: DbSession,
    dia: date = Query(..., description="Dia BRT a relatar (YYYY-MM-DD)"),
) -> Response:
    """Gera o relatório do dia pra esse perfil e devolve como PDF.

    - Coleta orders do dia via `/orders/search`
    - Cruza com `custos.xlsx` pra calcular lucro líquido por SKU
    - Compara com `ontem` e `média 7d` (do snapshot persistido em ticks anteriores)
    - Aplica heurísticas pra gerar sugestões
    - Persiste KPIs do dia (alimenta comparativos futuros)
    - Renderiza PDF com KPIs + gráficos + tabela top SKUs + sugestões
    """
    relatorio_repo = RelatorioDiarioRepository(session)
    use_case = GerarRelatorioDiarioUseCase(
        profile_repo=profile_repo,
        creds_repo=creds_repo,
        relatorio_repo=relatorio_repo,
    )
    try:
        rel = await use_case.execute(profile_id, dia=dia)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ProfileNaoConectadoError, SemCustosXlsxError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    pdf_bytes = renderizar_pdf(rel)
    filename = f"relatorio_{rel.profile_name}_{rel.dia.isoformat()}.pdf"
    # Substitui espaços e caracteres problemáticos pro nome do arquivo
    filename_safe = filename.replace(" ", "_").replace("/", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename_safe}"',
        },
    )


# ─── Fatia 2: arquivos do scheduler diário ─────────────────────────────────


class PDFArquivadoResponse(BaseModel):
    filename: str
    dia: str
    tamanho_bytes: int
    modificado_em_iso: str


class ListaPDFsResponse(BaseModel):
    pdfs: list[PDFArquivadoResponse]


class GerarAgoraResponse(BaseModel):
    ok: bool
    filename: str
    dia: str
    tamanho_bytes: int


@router.get(
    "/{profile_id}/relatorio/agendamentos",
    response_model=ListaPDFsResponse,
)
async def listar_pdfs_arquivados(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    limit: int = 30,
) -> ListaPDFsResponse:
    """Lista os últimos PDFs salvos em disco pelo scheduler diário."""
    try:
        profile = await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    arquivos = listar_pdfs(profile.slug, limit=limit)
    return ListaPDFsResponse(
        pdfs=[
            PDFArquivadoResponse(
                filename=a.filename,
                dia=a.dia.isoformat(),
                tamanho_bytes=a.tamanho_bytes,
                modificado_em_iso=a.modificado_em_iso,
            )
            for a in arquivos
        ],
    )


@router.get("/{profile_id}/relatorio/agendamentos/{filename}")
async def baixar_pdf_arquivado(
    profile_id: UUID,
    filename: str,
    profile_repo: ProfileRepo,
) -> Response:
    """Baixa um PDF previamente salvo pelo scheduler."""
    try:
        profile = await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    conteudo = ler_pdf_por_filename(profile.slug, filename)
    if conteudo is None:
        raise HTTPException(status_code=404, detail="PDF não encontrado")
    return Response(
        content=conteudo,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post(
    "/{profile_id}/relatorio/gerar-agora",
    response_model=GerarAgoraResponse,
)
async def gerar_pdf_agora(
    profile_id: UUID,
    profile_repo: ProfileRepo,
    creds_repo: CredsRepo,
    session: DbSession,
    dia: date | None = Query(
        default=None,
        description="Dia BRT (YYYY-MM-DD). Default: ontem.",
    ),
) -> GerarAgoraResponse:
    """Gera o relatório do dia (ou de ontem por default) e SALVA em disco.

    Útil pra testar manualmente antes de ativar o scheduler. O arquivo entra
    na listagem `/agendamentos`, do mesmo jeito que se o scheduler tivesse
    gerado.
    """
    try:
        profile = await profile_repo.get_by_id(profile_id)
    except ProfileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    dia_final = dia or (date.today() - timedelta(days=1))

    relatorio_repo = RelatorioDiarioRepository(session)
    use_case = GerarRelatorioDiarioUseCase(
        profile_repo=profile_repo,
        creds_repo=creds_repo,
        relatorio_repo=relatorio_repo,
    )
    try:
        rel = await use_case.execute(profile_id, dia=dia_final)
    except (ProfileNaoConectadoError, SemCustosXlsxError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    pdf_bytes = renderizar_pdf(rel)
    path = salvar_pdf(profile.slug, dia_final, pdf_bytes)
    return GerarAgoraResponse(
        ok=True,
        filename=path.name,
        dia=dia_final.isoformat(),
        tamanho_bytes=len(pdf_bytes),
    )
