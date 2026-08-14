"""CLI pra criar (ou promover) um usuário admin.

Uso típico — primeiro admin do sistema (sem este passo, ninguém
consegue acessar nada quando `auth_required=True`):

    uv run --project backend python -m liraz_tools.scripts.create_admin \\
        --email admin@empresa.com --senha "MinhaSenh@Forte" --nome "Admin"

Se o e-mail já existe, promove pra admin (e troca a senha se `--senha`
foi passado). Idempotente — pode rodar de novo sem perigo.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from getpass import getpass

from sqlalchemy import update

from liraz_tools.core.auth.password import hash_password
from liraz_tools.infrastructure.db.database import (
    create_all_tables,
    dispose_engine,
    session_scope,
)
from liraz_tools.infrastructure.db.models import UserModel
from liraz_tools.infrastructure.repositories.user_repository import (
    SQLAlchemyUserRepository,
    UserAlreadyExistsError,
)


async def _run(email: str, senha: str, nome: str | None) -> int:
    await create_all_tables()
    try:
        async with session_scope() as session:
            repo = SQLAlchemyUserRepository(session)
            existing = await repo.get_with_hash_by_email(email)
            if existing is None:
                user = await repo.add(
                    email=email,
                    password_hash=hash_password(senha),
                    nome=nome,
                    is_admin=True,
                )
                print(f"OK — admin criado: {user.email} (id={user.id})")
                return 0
            # Já existe — promove pra admin + opcionalmente troca senha
            if not existing.is_admin:
                await session.execute(
                    update(UserModel)
                    .where(UserModel.id == str(existing.id))
                    .values(is_admin=True, is_active=True),
                )
                print(f"OK — usuário {existing.email} promovido a admin")
            else:
                print(f"INFO — usuário {existing.email} já é admin")
            if senha:
                await repo.update_password_hash(existing.id, hash_password(senha))
                print("OK — senha atualizada")
            return 0
    except UserAlreadyExistsError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cria ou promove um usuário admin.",
    )
    parser.add_argument("--email", required=True, help="E-mail do admin")
    parser.add_argument(
        "--senha", default=None,
        help="Senha (mín 8 chars). Se omitir, pergunta interativamente.",
    )
    parser.add_argument("--nome", default=None, help="Nome de exibição (opcional)")
    args = parser.parse_args()

    senha = args.senha
    if not senha:
        senha = getpass("Senha: ")
        confirma = getpass("Confirme: ")
        if senha != confirma:
            print("ERRO: senhas não conferem", file=sys.stderr)
            return 1
    if len(senha) < 8:
        print("ERRO: senha precisa ter ao menos 8 caracteres", file=sys.stderr)
        return 1

    return asyncio.run(_run(args.email.strip().lower(), senha, args.nome))


if __name__ == "__main__":
    sys.exit(main())
