from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Credential, CredentialProvider, EmbeddingRoute
from app.services.vault import CredentialVault, VaultError


class EmbeddingError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddingResult:
    values: list[float]
    provider: str
    model: str


class EmbeddingGateway:
    def __init__(self, timeout_seconds: float = 30):
        self.timeout = timeout_seconds

    async def embed(
        self,
        db: AsyncSession,
        text: str,
        *,
        fixed_route: dict | None = None,
        route_is_fixed: bool = False,
    ) -> EmbeddingResult | None:
        if route_is_fixed:
            route = fixed_route
        else:
            route = await db.scalar(
                select(EmbeddingRoute)
                .where(EmbeddingRoute.active.is_(True))
                .order_by(EmbeddingRoute.updated_at.desc())
            )
        if route is None:
            return None
        provider_name = (
            route.get("provider") if isinstance(route, dict) else route.provider
        )
        model = route.get("model") if isinstance(route, dict) else route.model
        dimensions = (
            route.get("dimensions") if isinstance(route, dict) else route.dimensions
        )
        try:
            provider = CredentialProvider(provider_name)
        except (TypeError, ValueError) as exc:
            raise EmbeddingError(
                f"Unsupported embedding provider: {provider_name}"
            ) from exc
        credential = await db.scalar(
            select(Credential).where(
                Credential.provider == provider, Credential.active.is_(True)
            )
        )
        if credential is None:
            return None
        try:
            api_key = CredentialVault().decrypt(credential.encrypted_value)
        except VaultError as exc:
            raise EmbeddingError(str(exc)) from exc
        vector = await self._call(provider_name, model, api_key, text)
        if dimensions and len(vector) != dimensions:
            raise EmbeddingError(
                f"Embedding dimension mismatch: expected {dimensions}, got {len(vector)}"
            )
        return EmbeddingResult(vector, provider_name, model)

    async def _call(
        self, provider: str, model: str, api_key: str, text: str
    ) -> list[float]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if provider == "openai":
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, "input": text[:8000]},
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                return payload["data"][0]["embedding"]
            if provider == "gemini":
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent",
                    headers={"x-goog-api-key": api_key},
                    json={
                        "model": f"models/{model}",
                        "content": {"parts": [{"text": text[:8000]}]},
                    },
                )
                response.raise_for_status()
                return response.json()["embedding"]["values"]
        raise EmbeddingError(f"Unsupported embedding provider: {provider}")
