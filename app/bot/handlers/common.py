from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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

from app.config import Settings
from app.db.models import UserStatus
from app.db.repositories import get_or_create_user
from app.services.naming import (
    FolderNameValidationError,
    build_recommended_user_folder_name,
    validate_user_folder_name,
)
from app.services.telegram_outbox import enqueue_admin_user_pending

router = Router()


class FolderProfileStates(StatesGroup):
    waiting_for_contract_number = State()
    waiting_for_contract_date = State()
    waiting_for_contract_full_name = State()
    waiting_for_folder_confirm = State()
    waiting_for_manual_folder_name = State()


def _text_value(message: Message) -> str | None:
    text = (message.text or "").strip()
    return text or None


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
async def start(
    message: Message, bot: Bot, session: AsyncSession, settings: Settings, state: FSMContext
) -> None:
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
        await state.clear()
        await state.set_state(FolderProfileStates.waiting_for_contract_number)
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


@router.message(
    StateFilter(FolderProfileStates.waiting_for_contract_number), F.text, ~F.text.startswith("/")
)
async def onboarding_contract_number(message: Message, state: FSMContext) -> None:
    text = _text_value(message)
    if not text:
        await message.answer("Введите номер договора текстом:")
        return
    await state.update_data(contract_number=text)
    await state.set_state(FolderProfileStates.waiting_for_contract_date)
    await message.answer("Введите дату договора (например, 09.07.2026):")


@router.message(
    StateFilter(FolderProfileStates.waiting_for_contract_date), F.text, ~F.text.startswith("/")
)
async def onboarding_contract_date(message: Message, state: FSMContext) -> None:
    text = _text_value(message)
    if not text:
        await message.answer("Введите дату договора текстом:")
        return
    await state.update_data(contract_date=text)
    await state.set_state(FolderProfileStates.waiting_for_contract_full_name)
    await message.answer("Введите ФИО по договору:")


@router.message(
    StateFilter(FolderProfileStates.waiting_for_contract_full_name), F.text, ~F.text.startswith("/")
)
async def onboarding_contract_full_name(message: Message, state: FSMContext) -> None:
    text = _text_value(message)
    if not text:
        await message.answer("Введите ФИО по договору текстом:")
        return
    data = await state.get_data()
    try:
        folder_name = build_recommended_user_folder_name(
            data["contract_number"], data["contract_date"], text
        )
    except FolderNameValidationError as exc:
        await state.clear()
        await state.set_state(FolderProfileStates.waiting_for_contract_number)
        await message.answer(
            f"Данные дают небезопасное имя папки: {exc}. Введите номер договора заново:"
        )
        return
    await state.update_data(contract_full_name=text, folder_name=folder_name)
    await state.set_state(FolderProfileStates.waiting_for_folder_confirm)
    await message.answer(
        f"Предлагаемое имя папки:\n{folder_name}\n\nПодтвердить или изменить?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Подтвердить")],
                [KeyboardButton(text="Изменить имя папки")],
                [KeyboardButton(text="Изменить данные")],
            ],
            resize_keyboard=True,
        ),
    )


@router.message(
    StateFilter(FolderProfileStates.waiting_for_folder_confirm), F.text, ~F.text.startswith("/")
)
async def onboarding_confirm(
    message: Message, bot: Bot, session: AsyncSession, settings: Settings, state: FSMContext
) -> None:
    text = _text_value(message)
    if not text:
        await message.answer("Выберите действие кнопкой или отправьте текст команды.")
        return
    user, _ = await get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    if text == "Изменить данные":
        await state.clear()
        await state.set_state(FolderProfileStates.waiting_for_contract_number)
        await message.answer("Введите номер договора:", reply_markup=ReplyKeyboardRemove())
        return
    if text == "Изменить имя папки":
        await state.set_state(FolderProfileStates.waiting_for_manual_folder_name)
        await message.answer("Введите итоговое имя папки:", reply_markup=ReplyKeyboardRemove())
        return
    if text != "Подтвердить":
        await message.answer("Подтвердите имя папки или выберите изменение данных.")
        return
    data = await state.get_data()
    user.contract_number = data["contract_number"]
    user.contract_date = data["contract_date"]
    user.contract_full_name = data["contract_full_name"]
    user.folder_name = data["folder_name"]
    await enqueue_admin_user_pending(session, settings, user)
    await session.commit()
    await state.clear()
    await message.answer(
        "Заявка отправлена администратору. Дождитесь одобрения.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(
    StateFilter(FolderProfileStates.waiting_for_manual_folder_name), F.text, ~F.text.startswith("/")
)
async def onboarding_manual_name(message: Message, state: FSMContext) -> None:
    text = _text_value(message)
    if not text:
        await message.answer("Введите непустое имя папки:")
        return
    try:
        folder_name = validate_user_folder_name(text)
    except FolderNameValidationError as exc:
        await message.answer(f"Имя папки небезопасно: {exc}. Введите другое имя:")
        return
    await state.update_data(folder_name=folder_name)
    await state.set_state(FolderProfileStates.waiting_for_folder_confirm)
    await message.answer(
        f"Итоговое имя папки:\n{folder_name}\n\nПодтвердить?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Подтвердить")],
                [KeyboardButton(text="Изменить имя папки")],
                [KeyboardButton(text="Изменить данные")],
            ],
            resize_keyboard=True,
        ),
    )
