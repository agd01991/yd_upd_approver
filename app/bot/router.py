from aiogram import Router

from app.bot.handlers import admin, common, files, user


def build_router() -> Router:
    router = Router()
    router.include_router(common.router)
    router.include_router(user.router)
    router.include_router(files.router)
    router.include_router(admin.router)
    return router
