from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.api.router import compatibility_router, router as api_router
from app.core.config import get_settings

settings = get_settings()


def load_app(application: FastAPI) -> FastAPI:
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router)
    application.include_router(compatibility_router)
    return application


def create_app() -> FastAPI:
    application = FastAPI(title="App Development API", version="0.1.0")

    @application.get("/health")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    return load_app(application)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=settings.api_port, reload=True)
