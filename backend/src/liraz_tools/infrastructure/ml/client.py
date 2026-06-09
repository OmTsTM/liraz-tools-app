"""Cliente HTTP autenticado pra API do Mercado Livre.

Responsabilidades:
- Trocar authorization code por TokenSet (OAuth callback)
- Refresh de token quando expira
- Fazer chamadas autenticadas reusando token do perfil
- Tratar erros 401 (token revogado pelo ML) com retry de refresh

Uso típico:

    async with MLClient(credentials, tokens, on_tokens_refreshed=callback) as ml:
        me = await ml.get("/users/me")
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from liraz_tools.core.config import get_settings
from liraz_tools.core.logging import get_logger
from liraz_tools.domain.oauth.entity import AppCredentials, TokenSet

logger = get_logger(__name__)


class MLAuthError(Exception):
    """Erro de autenticação com o ML — token revogado, refresh falhou, etc."""


class MLAPIError(Exception):
    """Erro genérico da API ML (não-auth)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


TokenRefreshCallback = Callable[[TokenSet], Awaitable[None]]
"""Callback chamado quando tokens são atualizados via refresh. O caller
salva no banco. Sem callback, refresh ainda funciona mas não persiste."""


class MLAuthHelper:
    """Funções stateless de OAuth — trocar code por tokens, refresh, etc.

    Não precisa de token prévio. Útil pra callback do OAuth.
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    async def exchange_code_for_tokens(
        self,
        credentials: AppCredentials,
        code: str,
    ) -> TokenSet:
        """Troca authorization_code por access_token + refresh_token.

        Chamado no callback do OAuth, logo após o usuário autorizar no ML.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self._settings.ml_token_url,
                headers={
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "authorization_code",
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "code": code,
                    "redirect_uri": credentials.redirect_uri,
                },
            )

        if response.status_code != 200:
            logger.warning(
                "oauth_exchange_failed",
                status=response.status_code,
                body_snippet=response.text[:200],
            )
            raise MLAuthError(
                f"falha ao trocar code por tokens: HTTP {response.status_code}"
            )

        return TokenSet.from_ml_response(response.json())

    async def refresh_tokens(
        self,
        credentials: AppCredentials,
        refresh_token: str,
        ml_user_id: int,
    ) -> TokenSet:
        """Usa refresh_token pra obter novo access_token.

        ml_user_id é repassado caso a resposta do ML não inclua user_id
        (acontece em algumas variações do endpoint).
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self._settings.ml_token_url,
                headers={
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "refresh_token": refresh_token,
                },
            )

        if response.status_code != 200:
            logger.warning(
                "oauth_refresh_failed",
                status=response.status_code,
                body_snippet=response.text[:200],
            )
            raise MLAuthError(
                f"falha ao refrescar tokens: HTTP {response.status_code}. "
                f"O usuário pode precisar reautorizar."
            )

        return TokenSet.from_ml_response(response.json(), fallback_user_id=ml_user_id)


class MLClient:
    """Cliente HTTP autenticado.

    Use como async context manager. Faz refresh automático em 401 ou quando
    detecta token expirado.
    """

    def __init__(
        self,
        credentials: AppCredentials,
        tokens: TokenSet,
        on_tokens_refreshed: TokenRefreshCallback | None = None,
        max_concurrent: int = 12,
    ) -> None:
        self._credentials = credentials
        self._tokens = tokens
        self._callback = on_tokens_refreshed
        self._auth_helper = MLAuthHelper()
        self._http: httpx.AsyncClient | None = None
        self._settings = get_settings()
        # Semaphore controla quantas requests paralelas o cliente faz contra a
        # API do ML. 12 (era 8): o gargalo do scan reverso é latência de rede
        # (~472 round-trips), então mais paralelismo encurta direto. Conservador
        # de propósito — writes têm semáforo próprio menor (ver campaign_skus_apply)
        # e leituras em massa dependem do retry-on-429 pra não derrubar item.
        self._semaphore = asyncio.Semaphore(max_concurrent)
        # Cache em memória pra GETs idempotentes durante a sessão
        # (ex: detalhes de item não mudam durante a geração do relatório)
        self._cache: dict[str, Any] = {}

    async def __aenter__(self) -> MLClient:
        self._http = httpx.AsyncClient(
            base_url=self._settings.ml_api_base,
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=24,
                max_keepalive_connections=16,
                keepalive_expiry=30.0,
            ),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def current_tokens(self) -> TokenSet:
        """Tokens atualmente em uso (podem ter sido refrescados)."""
        return self._tokens

    async def _ensure_fresh_token(self) -> None:
        """Refresca o token se já expirou (com margem de segurança)."""
        if not self._tokens.is_expired():
            return

        logger.info("ml_token_proactive_refresh", user_id=self._tokens.ml_user_id)
        new_tokens = await self._auth_helper.refresh_tokens(
            self._credentials,
            self._tokens.refresh_token,
            self._tokens.ml_user_id,
        )
        self._tokens = new_tokens
        if self._callback is not None:
            await self._callback(new_tokens)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "Accept": "application/json",
        }

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        use_cache: bool = False,
        max_retries: int = 0,
        retry_on_status: set[int] | None = None,
    ) -> Any:
        """GET autenticado com paralelismo controlado por semaphore.

        Args:
            path: path relativo à base do ML (ex: '/items/MLB123')
            params: query string
            use_cache: se True, cacheia resposta em memória pela sessão
            max_retries: número de retries em caso de erro retornável
            retry_on_status: status codes que disparam retry (default: 429, 5xx)

        Refresca token em 401 automaticamente.
        """
        assert self._http is not None, "use 'async with MLClient(...) as ml:'"

        retry_statuses = retry_on_status or {424, 429, 500, 502, 503, 504}

        cache_key: str | None = None
        if use_cache:
            cache_key = f"GET {path}?{sorted((params or {}).items())}"
            if cache_key in self._cache:
                return self._cache[cache_key]

        async with self._semaphore:
            await self._ensure_fresh_token()

            backoff = 1.0
            last_exception: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    response = await self._http.get(
                        path, headers=self._headers(), params=params
                    )

                    # 401 = refresh do token (sempre, não conta como retry)
                    if response.status_code == 401:
                        logger.info("ml_401_retrying_with_refresh")
                        new_tokens = await self._auth_helper.refresh_tokens(
                            self._credentials,
                            self._tokens.refresh_token,
                            self._tokens.ml_user_id,
                        )
                        self._tokens = new_tokens
                        if self._callback is not None:
                            await self._callback(new_tokens)
                        response = await self._http.get(
                            path, headers=self._headers(), params=params
                        )

                    # Status retornável: faz backoff e tenta de novo
                    if (
                        response.status_code in retry_statuses
                        and attempt < max_retries
                    ):
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue

                    if response.status_code >= 400:
                        raise MLAPIError(
                            f"ML GET {path} falhou: HTTP {response.status_code} "
                            f"{response.text[:200]}",
                            status_code=response.status_code,
                        )

                    data = response.json()
                    if cache_key is not None:
                        self._cache[cache_key] = data
                    return data

                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    last_exception = e
                    if attempt < max_retries:
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue
                    raise MLAPIError(f"ML GET {path} timeout/conexão: {e}") from e

            # Não deveria chegar aqui, mas pra satisfazer o type checker
            if last_exception is not None:
                raise MLAPIError(str(last_exception)) from last_exception
            raise MLAPIError(f"ML GET {path} esgotou retries")

    async def put(
        self,
        path: str,
        json_body: dict[str, Any],
        *,
        max_retries: int = 2,
        retry_on_status: set[int] | None = None,
    ) -> Any:
        """PUT autenticado. Mesmo padrão de auth/retry do GET.

        Default `max_retries=2` porque writes podem falhar transitoriamente
        e queremos resiliência — diferente do GET cacheado.
        """
        return await self._write_request(
            "PUT", path, json_body, max_retries=max_retries, retry_on_status=retry_on_status
        )

    async def post(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        max_retries: int = 2,
        retry_on_status: set[int] | None = None,
    ) -> Any:
        """POST autenticado.

        Aceita `json` como alias de `json_body` pra compatibilidade com a
        convenção do httpx/requests.
        """
        body = json if json is not None else json_body
        if body is None:
            body = {}
        return await self._write_request(
            "POST", path, body,
            params=params,
            max_retries=max_retries, retry_on_status=retry_on_status,
        )

    async def delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_retries: int = 2,
        retry_on_status: set[int] | None = None,
    ) -> Any:
        """DELETE autenticado. Aceita params na query string."""
        return await self._write_request(
            "DELETE", path, None,
            params=params,
            max_retries=max_retries, retry_on_status=retry_on_status,
        )

    async def _write_request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None,
        *,
        params: dict[str, Any] | None = None,
        max_retries: int,
        retry_on_status: set[int] | None,
    ) -> Any:
        """Implementação compartilhada de PUT/POST/DELETE. Trata auth + retries + erros."""
        assert self._http is not None, "use 'async with MLClient(...) as ml:'"

        retry_statuses = retry_on_status or {429, 500, 502, 503, 504}

        async with self._semaphore:
            await self._ensure_fresh_token()

            backoff = 1.0
            last_exception: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    # DELETE não envia body
                    req_kwargs: dict[str, Any] = {
                        "headers": self._headers(),
                    }
                    if params is not None:
                        req_kwargs["params"] = params
                    if json_body is not None and method != "DELETE":
                        req_kwargs["json"] = json_body

                    response = await self._http.request(
                        method, path, **req_kwargs,
                    )

                    # 401 = refresh + retry uma vez (não conta como retry user)
                    if response.status_code == 401:
                        logger.info("ml_401_retrying_with_refresh", method=method, path=path)
                        new_tokens = await self._auth_helper.refresh_tokens(
                            self._credentials,
                            self._tokens.refresh_token,
                            self._tokens.ml_user_id,
                        )
                        self._tokens = new_tokens
                        if self._callback is not None:
                            await self._callback(new_tokens)
                        response = await self._http.request(
                            method, path, **req_kwargs,
                        )

                    # Retry em status retornáveis
                    if response.status_code in retry_statuses and attempt < max_retries:
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue

                    if response.status_code >= 400:
                        raise MLAPIError(
                            f"ML {method} {path} falhou: HTTP {response.status_code} "
                            f"{response.text[:300]}",
                            status_code=response.status_code,
                        )

                    # 204 No Content (alguns endpoints de write retornam isso)
                    if response.status_code == 204 or not response.content:
                        return {}
                    return response.json()

                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    last_exception = e
                    if attempt < max_retries:
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue
                    raise MLAPIError(
                        f"ML {method} {path} timeout/conexão: {e}"
                    ) from e

            if last_exception is not None:
                raise MLAPIError(str(last_exception)) from last_exception
            raise MLAPIError(f"ML {method} {path} esgotou retries")
