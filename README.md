# Yandex Disk Upload Approver Bot

MVP Telegram-бота для контролируемой загрузки файлов пользователей на Яндекс.Диск администратора. Пользователи отправляют файлы боту, администратор вручную модерирует заявки, а загрузка на Диск выполняется только через OAuth-токен администратора.

## Реализовано

- Регистрация пользователя через `/start` со статусом `pending`.
- Модели `users`, `upload_requests`, `audit_log` и первая Alembic-миграция.
- Безопасная генерация папки пользователя вида `disk:/Telegram Uploads/<telegram_id>_<label>/`.
- Санитизация имён файлов, запрет traversal/slashes/control chars.
- Yandex Disk async client на `httpx`: info, exists, mkdir, recursive mkdir, list, upload URL, upload, copy-name conflict helper.
- Базовые user/admin handlers и inline callback data.
- Проверки прав администратора только по числовому Telegram ID.
- Temp storage helper с SHA256 и удалением успешных uploads.
- Tests для naming, auth, status transitions, Yandex mock client, conflict handling и запрета admin callback.

## Быстрый старт

```bash
cp .env.example .env
# заполните TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS, YANDEX_DISK_TOKEN
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
python -m app.main
```

Docker:

```bash
docker compose up --build
```

## Переменные окружения

- `TELEGRAM_BOT_TOKEN` — токен Telegram Bot API.
- `TELEGRAM_ADMIN_IDS` — числовые Telegram ID администраторов через запятую.
- `YANDEX_DISK_TOKEN` — OAuth-токен Яндекс.Диска администратора.
- `YANDEX_DISK_ROOT` — корневая папка для пользовательских папок.
- `DATABASE_URL` — async SQLAlchemy URL PostgreSQL.
- `REDIS_URL` — зарезервировано для будущей очереди.
- `TEMP_STORAGE_DIR`, `MAX_FILE_SIZE_MB`, `ALLOW_USER_DOWNLOADS`, `ALLOW_USER_FOLDER_SELECTION` — политики хранения и доступа.

## Команды

Пользовательские: `/start`, `/help`, `/profile`, `/status`, `/myfiles`.

Администраторские: `/admin`, `/queue`, `/users`, `/audit` (журнал подготовлен на уровне модели/сервиса, handler MVP минимальный).

## Ограничения MVP

- Очередь Redis не используется; upload worker вызывается приложением напрямую на следующем этапе интеграции callbacks.
- Telegram download в handler MVP заготовлен отдельным сервисом, но полная streaming-интеграция с Bot API требует runtime token.
- Некоторые admin actions (`rename`, `folder`, `list`, `overwrite`, `retry`) представлены callback-кнопками и безопасным заглушечным ответом; бизнес-сервисы для конфликтов уже выделены.
- `/audit` не реализован как полноценная выдача, хотя таблица и сервис записи аудита есть.
- Пользовательский выбор папки ограничивается `allowed_folders`; UI выбора папки нужно расширить на следующем этапе.

## Проверки

```bash
pytest
ruff check .
ruff format --check .
alembic upgrade head
python -m app.main
```
