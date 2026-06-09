"""Storage de PDFs gerados pelo scheduler diário (Fatia 2).

Salva PDFs em `%LOCALAPPDATA%/LirazTools/relatorios/{profile_slug}/{dia}.pdf`.
Layout simples por perfil pra evitar colisões e facilitar limpeza manual.

Operações: salvar, listar (ordenado do mais recente), recuperar bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from liraz_tools.core.paths import get_app_data_dir


@dataclass(frozen=True)
class PDFArquivado:
    """Metadado de um PDF salvo em disco."""

    profile_slug: str
    dia: date
    filename: str
    path: Path
    tamanho_bytes: int
    modificado_em_iso: str


def _dir_perfil(profile_slug: str) -> Path:
    """Pasta do perfil, criada se necessário."""
    base = get_app_data_dir() / "relatorios" / profile_slug
    base.mkdir(parents=True, exist_ok=True)
    return base


def _filename_pra_dia(dia: date) -> str:
    return f"{dia.isoformat()}.pdf"


def salvar_pdf(profile_slug: str, dia: date, conteudo: bytes) -> Path:
    """Persiste o PDF em disco; sobrescreve se já existir."""
    path = _dir_perfil(profile_slug) / _filename_pra_dia(dia)
    path.write_bytes(conteudo)
    return path


def caminho_do_pdf(profile_slug: str, dia: date) -> Path | None:
    """Path do PDF salvo pra esse (perfil, dia), ou None se não existe."""
    path = _dir_perfil(profile_slug) / _filename_pra_dia(dia)
    return path if path.exists() else None


def existe_pdf(profile_slug: str, dia: date) -> bool:
    return caminho_do_pdf(profile_slug, dia) is not None


def listar_pdfs(profile_slug: str, *, limit: int = 30) -> list[PDFArquivado]:
    """Últimos N PDFs do perfil, do mais recente pro mais antigo."""
    pasta = _dir_perfil(profile_slug)
    arquivos = []
    for f in pasta.glob("*.pdf"):
        # Espera filename YYYY-MM-DD.pdf
        try:
            dia = date.fromisoformat(f.stem)
        except ValueError:
            continue
        stat = f.stat()
        arquivos.append(PDFArquivado(
            profile_slug=profile_slug,
            dia=dia,
            filename=f.name,
            path=f,
            tamanho_bytes=stat.st_size,
            modificado_em_iso=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        ))
    arquivos.sort(key=lambda a: a.dia, reverse=True)
    return arquivos[:limit]


def ler_pdf_por_filename(
    profile_slug: str, filename: str,
) -> bytes | None:
    """Recupera bytes do PDF pelo nome do arquivo (sem path traversal).

    Defensivo: rejeita filename com '/', '\\' ou '..'. Só aceita arquivos
    na pasta do próprio perfil.
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    path = _dir_perfil(profile_slug) / filename
    if not path.exists() or not path.is_file():
        return None
    return path.read_bytes()
