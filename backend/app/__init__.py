def create_app(lifespan) -> "FastAPI":
    # Defer heavy imports so that `import app.*` works in lightweight environments (e.g. unit tests).
    from fastapi import FastAPI

    from .routers import chat, config, model, note, provider

    app = FastAPI(title="BiliNote", lifespan=lifespan)
    app.include_router(note.router, prefix="/api")
    app.include_router(provider.router, prefix="/api")
    app.include_router(model.router, prefix="/api")
    app.include_router(config.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")

    return app
