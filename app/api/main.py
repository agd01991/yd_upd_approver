from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import admin, files, uploads, user
from app.config import get_settings

settings = get_settings()
app = FastAPI(title="Yandex Disk Upload Approver Mini App")
origins = settings.cors_origins
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["X-Telegram-Init-Data", "Content-Type"],
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(user.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.mount("/", StaticFiles(directory="app/webapp", html=True), name="webapp")
