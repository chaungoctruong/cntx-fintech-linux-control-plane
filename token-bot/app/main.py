import base64
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import api
from .bot_registry import BotRegistry
from .config import Settings
from .crypto import BotCipher
from .db import ensure_schema_patches, make_engine, make_session_factory
from .models import Base
from .token_service import TokenService

log = logging.getLogger(__name__)


def build_app() -> FastAPI:
    settings = Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        Base.metadata.create_all(engine)
        ensure_schema_patches(engine)
        session_factory = make_session_factory(engine)

        cipher = BotCipher(base64.b64decode(settings.master_key_b64))
        registry = BotRegistry(settings.source_bot_dir, settings.encrypted_bot_dir, cipher)
        if settings.auto_encrypt_on_startup:
            try:
                encrypted = registry.encrypt_all()
                log.info("auto_encrypt_on_startup completed packages=%d", len(encrypted))
            except Exception as exc:
                log.warning("auto_encrypt_on_startup failed: %s", exc)
        token_service = TokenService(settings.jwt_secret)

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.cipher = cipher
        app.state.registry = registry
        app.state.token_service = token_service
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(api.router)

    @app.get("/health")
    def health():
        return {"ok": True, "service": settings.app_name}

    return app


app = build_app()


if __name__ == "__main__":
    import uvicorn

    s = Settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, reload=False)
