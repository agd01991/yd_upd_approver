# Yandex Disk Upload Approver Bot

MVP Telegram-бота для контролируемой загрузки пользовательских файлов на Яндекс.Диск администратора. Пользователь не получает доступ к Яндекс.Диску: он отправляет файл боту, файл временно хранится локально, администратор вручную одобряет заявку, а загрузка выполняется только от имени администратора через `YANDEX_DISK_TOKEN`.

## Что реализовано

- `/start` регистрирует пользователя со статусом `pending` и отправляет администраторам карточку с кнопками одобрения/отклонения/блокировки.
- После одобрения пользователю назначается папка внутри `YANDEX_DISK_ROOT` с обязательным Telegram ID в имени.
- Активный пользователь отправляет документ в Telegram: бот скачивает его во временное хранилище, считает реальный SHA256, создаёт заявку и отправляет администраторам карточку файла.
- Пользователь получает только текстовое подтверждение и не видит admin inline-кнопки.
- Администратор может открыть временный файл, посмотреть содержимое целевой папки, одобрить загрузку, загрузить как копию, перезаписать, повторить failed-загрузку или отклонить заявку.
- После успешной загрузки временный файл удаляется. При ошибке файл остаётся на диске для retry.
- Публичные ссылки Яндекс.Диска не создаются.
- Админские действия проверяются только по числовому Telegram ID, username не используется как идентификатор доступа.
- Добавлены SQLAlchemy async модели, Alembic-миграция, сервис Yandex Disk на `httpx`, Docker Compose и тесты.

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

Контейнер bot при старте выполняет миграции и затем запускает приложение:

```bash
alembic upgrade head && python -m app.main
```

## Переменные окружения

- `TELEGRAM_BOT_TOKEN` — токен Telegram Bot API.
- `TELEGRAM_ADMIN_IDS` — числовые Telegram ID администраторов через запятую.
- `YANDEX_DISK_TOKEN` — OAuth-токен Яндекс.Диска администратора. Именно этим токеном выполняются загрузки.
- `YANDEX_DISK_ROOT` — корневая папка для пользовательских папок, например `disk:/Telegram Uploads`.
- `DATABASE_URL` — async SQLAlchemy URL PostgreSQL.
- `REDIS_URL` — зарезервировано для будущей очереди.
- `TEMP_STORAGE_DIR` — локальная папка временных файлов до одобрения.
- `MAX_FILE_SIZE_MB` — лимит размера файла на стороне приложения.
- `ALLOW_USER_DOWNLOADS`, `ALLOW_USER_FOLDER_SELECTION` — будущие политики доступа.

## Сценарий работы

### `/start`

1. Новый пользователь создаётся со статусом `pending`.
2. Администраторы получают карточку пользователя.
3. Если пользователь уже `active`, бот сообщает, что можно отправлять файлы.
4. Для `blocked`/`rejected` бот отвечает понятным запретом.

### Отправка файла

1. Бот разрешает загрузку только пользователю в статусе `active`.
2. Имя файла санитизируется, путь выбирается только из назначенной пользователю папки.
3. Файл скачивается в `TEMP_STORAGE_DIR/REQ-000001/<safe_filename>`.
4. Считается SHA256, создаётся заявка `pending_approval`.
5. Пользователь получает текст “Файл получен и отправлен администратору на проверку: REQ-000001”.
6. Администраторы получают карточку с номером заявки, пользователем, размером, MIME, коротким SHA256, комментарием, целевой папкой, target path, статусом и кнопками.

### Admin callbacks

- `Открыть файл` — отправляет админу локальный временный файл.
- `Содержимое папки` — показывает список файлов в целевой папке Яндекс.Диска.
- `Загрузить` — переводит заявку в `approved`, запускает upload worker без overwrite.
- `Как копию` — меняет target path на `filename__YYYY-MM-DD__REQ-000001.ext` и загружает.
- `Перезаписать` — разрешает overwrite только после admin callback.
- `Повторить` — повторяет failed-загрузку, если временный файл существует.
- `Отклонить` — показывает причины отклонения, ставит `rejected`, уведомляет пользователя и пишет audit log.
- `Переименовать` — запускает admin-only FSM, санитизирует новое имя и обновляет target path.
- `Сменить папку` — даёт выбрать только папку из `user.allowed_folders`, произвольный путь не принимается.

### `/status` и `/myfiles`

- `/status` показывает последние заявки пользователя и их статусы.
- `/myfiles` ходит в Yandex Disk API с admin OAuth token и показывает содержимое только личной папки пользователя.

## Проверки

```bash
ruff format .
ruff check .
pytest
alembic upgrade head
python -m app.main
```

## Важные ограничения

- Telegram Bot API имеет ограничения на размер файлов; дополнительно приложение проверяет `MAX_FILE_SIZE_MB`.
- Для production стоит вынести загрузки в очередь/worker-процесс и добавить регулярный scheduler для cleanup temp-файлов.
- Пользовательский выбор папки должен оставаться только выбором из `users.allowed_folders`; произвольные пути запрещены.
- Не включайте реальные токены в git и не логируйте Authorization headers.

## Получение токенов

### Telegram Bot Token

1. Откройте Telegram и найдите `@BotFather`.
2. Выполните `/newbot`, задайте имя и username бота.
3. Скопируйте выданный token в `TELEGRAM_BOT_TOKEN`.
4. Не публикуйте token и не добавляйте `.env` в git.

### Yandex Disk OAuth Token

1. Создайте OAuth-приложение Яндекса или получите OAuth token подходящим для вашего аккаунта способом.
2. Токен должен принадлежать администратору Яндекс.Диска.
3. Нужны права на чтение ресурсов, создание папок, получение списка файлов и запись/загрузку файлов.
4. Укажите token в `YANDEX_DISK_TOKEN`.

## Cleanup временных файлов

Отклонённые файлы специально не удаляются сразу, чтобы администратор мог проанализировать проблему. Для очистки старых временных файлов используйте:

```bash
python -m app.scripts.cleanup_temp
```

Срок хранения задаётся через `REJECTED_RETENTION_DAYS`.

## CI

В репозитории есть GitHub Actions workflow `.github/workflows/ci.yml`, который запускает форматирование, lint, тесты и Alembic migration check с PostgreSQL service.

## Ручная проверка

Подробный чеклист первого запуска и ручного QA находится в `docs/MANUAL_QA.md`.

## Telegram Mini App

The project now includes a Telegram Mini App: FastAPI serves `/api/*` and the static mobile-first frontend from `app/webapp`. Users can open the app in Telegram, see their status, upload files, view requests, and list files from their assigned Yandex Disk folder. Administrators can moderate users, review upload requests, approve/copy/overwrite/retry/reject uploads, edit target filename/folder, and inspect the audit log.

### Configuration

Add the Mini App settings to `.env`:

```env
WEBAPP_URL=https://your-public-url
WEBAPP_AUTH_MAX_AGE_SECONDS=86400
CORS_ORIGINS=https://your-public-url
```

`WEBAPP_URL` must be a public HTTPS URL for Telegram. For local development, run the API locally and expose it with an HTTPS tunnel such as:

```bash
ngrok http 8000
```

or any equivalent cloudflared/ngrok-style tunnel, then put the public URL into `WEBAPP_URL`.

### Running

```bash
docker compose up --build
```

The API is available at `http://localhost:8000/` for a local browser smoke test. Inside Telegram, use `/app` to receive an “Открыть приложение” WebApp button when `WEBAPP_URL` is configured. You can also configure a persistent Mini App/Menu Button manually in BotFather.

### Manual scenario

1. Open the Mini App or send `/start`.
2. A new user appears as `pending`.
3. An admin approves the user.
4. The active user uploads a file through the Mini App.
5. The admin approves the request through the Mini App.
6. The file appears on Yandex Disk.
7. `/myfiles` and the Mini App files screen show the same assigned-folder contents.

The Mini App validates Telegram `initData`; it does not accept a frontend-provided user ID, does not expose Yandex or Telegram tokens, and does not create public Yandex Disk links.
