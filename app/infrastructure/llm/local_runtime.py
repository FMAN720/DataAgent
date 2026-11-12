"""Per-operation snapshots for personal vision and embedding configuration."""

from contextlib import asynccontextmanager

from app.infrastructure.llm.local_settings import LocalModelError


class LocalRuntimeModels:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.embedding_usage = {"requests": 0, "input_tokens": 0, "texts": 0, "failures": 0}

    def available(self, purpose):
        try:
            profile = self.store.read(purpose)
            return bool(
                profile and profile.enabled and (purpose != "embedding" or profile.dimensions)
            )
        except LocalModelError:
            return False

    def configured_settings(self, purpose):
        profile = self.store.require(purpose)
        prefix = "vision" if purpose == "vision" else "embedding"
        update = {
            f"{prefix}_enabled": True,
            f"{prefix}_api_base": profile.base_url,
            f"{prefix}_api_key": profile.api_key.get_secret_value(),
            f"{prefix}_model": profile.model,
        }
        if purpose == "embedding":
            if profile.dimensions is None:
                raise LocalModelError("请先测试向量模型并保存维度。", 422)
            update.update(embedding_dimensions=profile.dimensions, embedding_revision="personal-v1")
        return self.settings.model_copy(update=update)

    @asynccontextmanager
    async def embedding(self):
        if not self.available("embedding"):
            yield None
            return
        from app.knowledge.embedding import APIEmbedder

        embedder = APIEmbedder(self.configured_settings("embedding"))
        try:
            yield embedder
        finally:
            try:
                await embedder.aclose()
            finally:
                for key in self.embedding_usage:
                    self.embedding_usage[key] += getattr(embedder, "usage", {}).get(key, 0)

    @asynccontextmanager
    async def vision(self):
        if not self.available("vision"):
            yield None
            return
        from app.vision.provider import VisionProvider

        provider = VisionProvider(self.configured_settings("vision"))
        try:
            yield provider
        finally:
            await provider.aclose()
