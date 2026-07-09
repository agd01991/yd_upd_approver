from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import user_moderation_keyboard
from app.config import Settings
from app.db.models import UserStatus
from app.db.repositories import get_or_create_user
from app.services.naming import (
    FolderNameValidationError,
    build_recommended_user_folder_name,
    validate_user_folder_name,
)
from app.utils.formatting import format_user_card

router = Router()
_ONBOARDING: dict[int, dict[str, str]] = {}


def webapp_keyboard(settings: Settings) -> InlineKeyboardMarkup | None:
    if not settings.webapp_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть приложение", web_app=WebAppInfo(url=settings.webapp_url)
                )
            ]
        ]
    )


@router.message(Command("start"))
async def start(message: Message, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    user, created = await get_or_create_user(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    await session.commit()

    if user.status == UserStatus.active:
        await message.answer(
            "Вы уже одобрены. Можете отправлять файлы.", reply_markup=webapp_keyboard(settings)
        )
        return
    if user.status == UserStatus.blocked:
        await message.answer("Ваш доступ заблокирован. Обратитесь к администратору.")
        return
    if user.status == UserStatus.rejected:
        await message.answer("Ваша заявка на доступ отклонена.")
        return

    if user.status == UserStatus.pending and not user.folder_name:
        _ONBOARDING[user.telegram_id] = {"step": "contract_number"}
        await message.answer("Введите номер договора:")
        return
    await message.answer(
        "Заявка уже отправлена администратору. Дождитесь одобрения.",
        reply_markup=webapp_keyboard(settings),
    )


@router.message(Command("app"))
async def app_cmd(message: Message, settings: Settings) -> None:
    if not settings.webapp_url:
        await message.answer("Mini App URL не настроен.")
        return
    await message.answer("Откройте Mini App:", reply_markup=webapp_keyboard(settings))


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer("Команды: /profile, /status, /myfiles. Отправьте файл после одобрения.")


@router.message()
async def onboarding_answers(
    message: Message, bot: Bot, session: AsyncSession, settings: Settings
) -> None:
    state = _ONBOARDING.get(message.from_user.id)
    if not state:
        return
    user, _ = await get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    text = (message.text or "").strip()
    step = state.get("step")
    if step == "contract_number":
        state["contract_number"] = text
        state["step"] = "contract_date"
        await message.answer("Введите дату договора (например, 09.07.2026):")
        return
    if step == "contract_date":
        state["contract_date"] = text
        state["step"] = "contract_full_name"
        await message.answer("Введите ФИО по договору:")
        return
    if step == "contract_full_name":
        state["contract_full_name"] = text
        try:
            state["folder_name"] = build_recommended_user_folder_name(
                state["contract_number"], state["contract_date"], state["contract_full_name"]
            )
        except FolderNameValidationError as exc:
            state.clear()
            state["step"] = "contract_number"
            await message.answer(
                f"Данные дают небезопасное имя папки: {exc}. Введите номер договора заново:"
            )
            return
        state["step"] = "confirm"
        await message.answer(
            f"Предлагаемое имя папки:\n{state['folder_name']}\n\nПодтвердить или изменить?",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Подтвердить")],
                    [KeyboardButton(text="Изменить имя папки")],
                    [KeyboardButton(text="Изменить данные")],
                ],
                resize_keyboard=True,
            ),
        )
        return
    if step == "confirm" and text == "Изменить данные":
        _ONBOARDING[user.telegram_id] = {"step": "contract_number"}
        await message.answer("Введите номер договора:", reply_markup=ReplyKeyboardRemove())
        return
    if step == "confirm" and text == "Изменить имя папки":
        state["step"] = "manual_name"
        await message.answer("Введите итоговое имя папки:", reply_markup=ReplyKeyboardRemove())
        return
    if step == "manual_name":
        try:
            state["folder_name"] = validate_user_folder_name(text)
        except FolderNameValidationError as exc:
            await message.answer(f"Имя папки небезопасно: {exc}. Введите другое имя:")
            return
        state["step"] = "confirm"
        await message.answer(
            f"Итоговое имя папки:\n{state['folder_name']}\n\nПодтвердить?",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="Подтвердить")],
                    [KeyboardButton(text="Изменить имя папки")],
                    [KeyboardButton(text="Изменить данные")],
                ],
                resize_keyboard=True,
            ),
        )
        return
    if step == "confirm" and text == "Подтвердить":
        user.contract_number = state["contract_number"]
        user.contract_date = state["contract_date"]
        user.contract_full_name = state["contract_full_name"]
        user.folder_name = state["folder_name"]
        await session.commit()
        _ONBOARDING.pop(user.telegram_id, None)
        for admin_id in settings.telegram_admin_ids:
            await bot.send_message(
                admin_id, format_user_card(user), reply_markup=user_moderation_keyboard(user.id)
            )
        await message.answer(
            "Заявка отправлена администратору. Дождитесь одобрения.",
            reply_markup=ReplyKeyboardRemove(),
        )
