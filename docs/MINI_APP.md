# Telegram Mini App

## Architecture

The Mini App adds a FastAPI backend in `app/api` and a vanilla HTML/CSS/JS frontend in `app/webapp`. The bot process remains separate. Both bot and API use the same PostgreSQL database, SQLAlchemy models, temporary storage, audit log, and administrator-owned Yandex Disk OAuth token.

## Authentication

Every frontend request sends `X-Telegram-Init-Data` from `window.Telegram.WebApp.initData`. The API validates the Telegram hash with the bot token, checks `auth_date` against `WEBAPP_AUTH_MAX_AGE_SECONDS`, and extracts the Telegram user from signed initData. User IDs from the browser body/query are never trusted. Admin APIs additionally require the signed Telegram ID to be present in `TELEGRAM_ADMIN_IDS`.

## API

- `GET /health`
- `GET /api/me`
- `POST /api/uploads`, `GET /api/uploads`
- `GET /api/files`
- `GET /api/admin/users`
- `POST /api/admin/users/{user_id}/approve|reject|block`
- `GET /api/admin/uploads`, `GET /api/admin/uploads/{request_id}`
- `GET /api/admin/uploads/{request_id}/download-temp`
- `GET /api/admin/uploads/{request_id}/allowed-folders`
- `GET /api/admin/uploads/{request_id}/folder-items`
- `POST /api/admin/uploads/{request_id}/approve|copy|overwrite|retry|reject`
- `PATCH /api/admin/uploads/{request_id}`
- `GET /api/admin/audit`

## Security

The frontend never receives Telegram bot tokens, Yandex OAuth tokens, Authorization headers, or public Yandex links. `/api/files` lists only the current user's assigned root folder using the admin token server-side. Upload size is limited by `MAX_FILE_SIZE_MB`, filenames are sanitized, and folder changes are restricted to `user.allowed_folders`.

## Local run

For Docker Compose, run `docker compose up --build`, then open `http://localhost:8000/` for a smoke test. Compose runs Alembic through the one-shot `migrate` service after PostgreSQL becomes healthy; `api` and `bot` start only after `migrate` completes successfully and do not run migrations in parallel.

For non-Docker local runs, execute migrations manually and then start the API:

```bash
alembic upgrade head
uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```

Real Telegram Mini Apps require a public HTTPS URL, so expose port 8000 via ngrok, cloudflared, or another HTTPS tunnel and set `WEBAPP_URL=https://...`.

## Production

Deploy the API behind HTTPS, set `WEBAPP_URL`, keep `CORS_ORIGINS` restricted to trusted origins, run Alembic migrations once before starting API and bot processes, and configure the bot menu/button in BotFather or use the `/app` command.

## Limitations

The vanilla UI is intentionally lightweight. Richer batch actions and folder browsing can be improved later without changing the security model.


## Local verification

Check the backend health endpoint:

```bash
curl http://localhost:8000/health
```

To test the Mini App locally, run the API, publish it through `ngrok` or `cloudflared`, and configure Telegram with a `WEBAPP_URL` that points to the public `/app` URL. Open the Mini App from Telegram so requests include signed WebApp `initData`.

Security notes for local testing:

- Protected temp-file downloads are performed by `fetch` with the `X-Telegram-Init-Data` header; no bot token or Yandex token is exposed to the frontend.
- The regular `/api/uploads` response does not expose the administrator's internal Yandex Disk `target_path`.
- Admin folder changes are selected from `/api/admin/uploads/{request_id}/allowed-folders` and are still validated server-side against `user.allowed_folders`.


## Runtime Yandex Disk root

- `YANDEX_DISK_ROOT` is now a fallback/default. Docker `.env` should point `DATABASE_URL` to `postgres` and `REDIS_URL` to `redis`; use `localhost` only for non-Docker local runs.
- Admins can run `/diskroot` to see the active root and whether it comes from `.env` or DB.
- Admins can run `/setdiskroot disk:/New Root` or `/setdiskroot` interactively to change the active root for new uploads of all active users.
- The bot validates the path and creates the Yandex Disk folder before saving; if folder creation fails, the setting is not saved.
- Changing the root affects only new users approved after the change. Existing active users keep their current folders and are not migrated.
- Mini App user approval uses the same runtime root with fallback to `YANDEX_DISK_ROOT`. Before each new upload and file listing, the backend ensures the user folder exists under the active root.

## Раздельное изменение имени файла и расширения

Администратор в Mini App модерирует пользователей русскими кнопками `Одобрить`, `Отклонить` и `Заблокировать`. Для заявок на файлы используются отдельные русские подписи действий: `Загрузить`, `Загрузить как копию`, `Перезаписать`, `Повторить` и `Отклонить`, поэтому одинаковое API-действие `approve` отображается по контексту.

Администратор в Mini App меняет параметры заявки отдельными действиями:

- `Изменить имя` отправляет в API только `filename_stem`. Backend сохраняет текущее расширение: `old.txt` + `тест` превращается в `тест.txt`.
- `Изменить расширение` отправляет только `filename_extension`. Backend сохраняет имя: `old.txt` + `pdf` превращается в `old.pdf`.
- `Сменить папку этой заявки` отправляет только `target_folder`, который повторно проверяется на принадлежность `user.allowed_folders`.

`original_filename`, `local_path` и временный файл не изменяются. После каждого допустимого изменения `target_path` безопасно пересобирается из разрешённой папки и безопасного имени файла. Интерфейс Mini App использует русские пользовательские подписи и русские названия статусов; машинные значения API остаются внутренними.


## UI filters and multi-file upload

The Mini App uses vanilla HTML/CSS/JS and Telegram theme variables with fallback colors. User-provided and server-provided values rendered through `innerHTML` are escaped in the frontend before display.

Users can choose multiple files in the upload card. The selected-file list shows the order, file name, and size before sending. Files are uploaded one by one through the existing `POST /api/uploads` endpoint, so every selected file creates its own request. The comment textarea is shared and is attached to every uploaded file. If one file fails validation or upload, the UI records that error and continues with the next file.

The user request list has status chips: all, pending review, uploaded, failed, and rejected. Filtering is performed in the frontend over `/api/uploads`. The admin upload list has chips for all, pending review, uploaded, failed, rejected, and requests waiting for action. Admin status filters use optional query parameters on `GET /api/admin/uploads` where possible. The admin search field calls the same endpoint with `user_query` and matches Telegram ID, username, or full name without changing authorization rules.

## Корневая папка

Во вкладке `Администратор → Корневая папка` администратор видит активную корневую папку и источник (`.env` или database), а также может сохранить новое значение. Это общая папка, внутри которой создаются папки пользователей. После изменения новые загрузки всех пользователей идут в новую root; если папки пользователя там ещё нет, backend создаёт её повторно. Старые файлы не переносятся, старые заявки не мигрируются, старые папки не удаляются. Кнопка `Сменить папку этой заявки` меняет только конкретную заявку.

## Переименование папок пользователей

Mini App поддерживает сохранение профиля папки пользователя через `/api/me/folder-profile`: номер договора, дату договора, ФИО и итоговое безопасное имя папки. Для новых pending-пользователей Mini App не должен отправлять администратору неполную заявку без `folder_name`.

В админской части добавлена вкладка «Заявки на переименование». В ней есть:

- выпадающий поиск пользователей с debounce по Telegram ID, username, Telegram ФИО, ФИО по договору, номеру договора и имени папки;
- карточка «Переименовать папку» для админского rename без заявки;
- selector source folder, наполненный только серверными candidates текущего пользователя;
- список pending-заявок пользователей на переименование с approve/reject.

Все динамические данные в obvious render functions проходят через `escapeHtml`. Frontend не получает `YANDEX_DISK_TOKEN` и не формирует target path самостоятельно: он отправляет только выбранный server-side candidate и новое имя папки, а backend валидирует source и строит target path.
