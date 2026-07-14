# Yandex Disk Upload Approver Bot

MVP Telegram-бота для контролируемой загрузки пользовательских файлов на Яндекс.Диск администратора. Пользователь не получает доступ к Яндекс.Диску: он отправляет файл боту, файл временно хранится локально, администратор вручную одобряет заявку, а загрузка выполняется только от имени администратора через `YANDEX_DISK_TOKEN`.

## Что реализовано

- `/start` регистрирует пользователя со статусом `pending` и отправляет администраторам карточку с кнопками одобрения/отклонения/блокировки.
- После одобрения пользователю назначается папка внутри активной корневой папки Яндекс.Диска с обязательным Telegram ID в имени. `YANDEX_DISK_ROOT` теперь используется как fallback/default, пока администратор не изменит runtime-настройку через бота.
- Активный пользователь отправляет документ в Telegram: бот скачивает его во временное хранилище, считает реальный SHA256, создаёт заявку и отправляет администраторам карточку файла.
- Пользователь получает только текстовое подтверждение и не видит admin inline-кнопки.
- Администратор может открыть временный файл, посмотреть содержимое целевой папки, одобрить загрузку, загрузить как копию, перезаписать, повторить failed-загрузку или отклонить заявку. Команды `/diskroot` и `/setdiskroot` показывают и меняют активную корневую папку.
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
# Терминал 1: Telegram polling bot (принимает updates и пишет состояние/outbox в PostgreSQL)
python -m app.main
# Терминал 2: upload worker (забирает approved-заявки и загружает файлы на Яндекс.Диск)
python -m app.workers.upload_worker
# Терминал 3: Telegram outbox delivery (доставляет durable-уведомления администраторам и пользователям)
python -m app.workers.telegram_outbox_worker
```

Все три non-Docker процесса должны оставаться запущенными: `python -m app.main` сам не выполняет загрузки на Яндекс.Диск и не доставляет durable outbox notifications.

Docker:

```bash
docker compose up --build
```

В Docker Compose миграции выполняет отдельный one-shot сервис `migrate`. Он ждёт healthy-состояния PostgreSQL, один раз запускает `alembic upgrade head`, а сервисы `api` и `bot` стартуют только после успешного завершения миграций. `api` и `bot` больше не запускают Alembic параллельно, поэтому при `docker compose up --build` не возникает race condition на создании таблиц.

Для non-Docker local запуска миграции по-прежнему выполняются вручную, затем одновременно запускаются три долгоживущих процесса:

```bash
alembic upgrade head
```

Терминал 1 — `app.main` принимает Telegram updates и пишет состояние/outbox в PostgreSQL:

```bash
python -m app.main
```

Терминал 2 — `upload_worker` забирает одобренные заявки и загружает файлы на Яндекс.Диск:

```bash
python -m app.workers.upload_worker
```

Терминал 3 — `telegram_outbox_worker` доставляет durable Telegram-уведомления:

```bash
python -m app.workers.telegram_outbox_worker
```

Если запустить только `python -m app.main`, заявки будут создаваться, но одобренные/retry-загрузки не дойдут до `uploaded` или `failed`, а durable outbox notifications (например, moderation card администратору) не будут доставлены.

Если тестируется Mini App или HTTP-интерфейс, отдельно запустите четвёртый процесс API:

```bash
alembic upgrade head
uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```

Если локальная БД уже попала в состояние после старой гонки миграций, обычно достаточно повторно запустить Compose без удаления volume:

```bash
docker compose up --build
```

При необходимости можно явно выполнить миграционный сервис, а затем поднять приложения:

```bash
docker compose up -d migrate
docker compose up -d api bot
```

Не используйте `docker compose down -v` как основной способ восстановления: эта команда удаляет PostgreSQL volume и локальные данные.

## Переменные окружения

- `TELEGRAM_BOT_TOKEN` — токен Telegram Bot API.
- `TELEGRAM_ADMIN_IDS` — числовые Telegram ID администраторов через запятую.
- `YANDEX_DISK_TOKEN` — OAuth-токен Яндекс.Диска администратора. Именно этим токеном выполняются загрузки.
- `YANDEX_DISK_ROOT` — fallback/default корневой папки для пользовательских папок, например `disk:/Telegram Uploads`. Активное значение можно посмотреть через `/diskroot`; `/setdiskroot disk:/New Root` сохраняет runtime-настройку в БД после успешного создания папки на Яндекс.Диске. После изменения новые загрузки всех пользователей идут в папки пользователей внутри новой корневой папки; если папки пользователя там нет, backend создаёт её повторно. Старые файлы не переносятся, старые заявки не мигрируются, старые папки не удаляются.
- `DATABASE_URL` — async SQLAlchemy URL PostgreSQL.
- `REDIS_URL` — зарезервировано для будущей очереди.
- `TEMP_STORAGE_DIR` — локальная папка временных файлов до одобрения.
- `MAX_FILE_SIZE_MB` — лимит размера файла на стороне приложения.
- `ALLOW_USER_DOWNLOADS`, `ALLOW_USER_FOLDER_SELECTION` — будущие политики доступа.

Для Docker `.env` используйте имена сервисов Compose:

```env
DATABASE_URL=postgresql+asyncpg://bot:bot@postgres:5432/bot
REDIS_URL=redis://redis:6379/0
```

Для non-Docker local можно использовать localhost:

```env
DATABASE_URL=postgresql+asyncpg://bot:bot@localhost:5432/bot
REDIS_URL=redis://localhost:6379/0
```

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
- `Сменить папку этой заявки` — даёт выбрать только папку из `user.allowed_folders`, произвольный путь не принимается.

### `/status` и `/myfiles`

- `/status` показывает последние заявки пользователя и их статусы.
- `/myfiles` ходит в Yandex Disk API с admin OAuth token и показывает содержимое только личной папки пользователя.

## Проверки

```bash
ruff format .
ruff check .
pytest
alembic upgrade head
# Терминал 1 — Telegram polling и создание durable-событий
python -m app.main
# Терминал 2 — выполнение загрузок на Яндекс.Диск
python -m app.workers.upload_worker
# Терминал 3 — доставка Telegram outbox notifications
python -m app.workers.telegram_outbox_worker
```

Для Docker-проверки достаточно `docker compose up --build`: Compose сначала запускает `migrate`, затем `api`, `bot`, `outbox-worker`, `worker` и необходимые зависимости. После старта API healthcheck можно проверить командой `curl http://localhost:8000/health`.

## Telegram transactional outbox

### Docker Compose

Отдельные команды для outbox не нужны: `docker compose up --build` автоматически запускает `bot`, `outbox-worker`, `worker`, `api` и необходимые зависимости после миграций.

### Non-Docker local mode

После `alembic upgrade head` держите запущенными три процесса: `python -m app.main` принимает Telegram updates и пишет состояние/outbox в PostgreSQL, `python -m app.workers.upload_worker` забирает одобренные заявки и загружает файлы на Яндекс.Диск, а `python -m app.workers.telegram_outbox_worker` доставляет durable Telegram-уведомления. API (`uvicorn app.api.main:app --host 0.0.0.0 --port 8000`) нужен отдельно для Mini App и HTTP-интерфейса.

### Диагностика worker

Для проверки доставки без Docker запустите outbox worker отдельным третьим процессом:

```bash
python -m app.workers.telegram_outbox_worker
```


## Runtime root Яндекс.Диска

- `/diskroot` показывает активный root и источник: fallback из `.env` или настройка администратора.
- `/setdiskroot` меняет активный root для новых загрузок всех пользователей. Active users переводятся на папки внутри новой root; старые файлы и старые заявки не мигрируются.
- Перед сохранением бот валидирует путь и создаёт папку через Yandex Disk API. Если создание папки не удалось, setting не сохраняется.
- Upload flow перед созданием новой заявки проверяет текущий root и при необходимости создаёт пользовательскую папку внутри него.
- Mini App approve использует то же runtime-значение.

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


### Mini App local hardening checks

Check the API is alive:

```bash
curl http://localhost:8000/health
```

For local Telegram Mini App testing, expose the API with `ngrok` or `cloudflared`, set `WEBAPP_URL` to the public `/app` URL used by Telegram, and open the app from Telegram so WebApp `initData` is available. Protected temp-file downloads in the Mini App use `fetch` with the `X-Telegram-Init-Data` header instead of query tokens or unauthenticated links. The user uploads API intentionally does not return internal Yandex Disk `target_path`; admins still see moderation paths in the admin API. Admin folder edits must choose one of the user's allowed folders and the backend validates the folder again before saving.

### Раздельное изменение имени и расширения

Администратор может отдельно менять имя файла и расширение в Telegram-боте и Mini App:

- `Изменить имя` меняет только часть имени без расширения. Например, для `old.txt` ввод `тест` даст `тест.txt`; ввод текущего полного имени `тест.txt` не создаёт двойное расширение.
- `Изменить расширение` меняет только расширение и принимает значения вида `pdf` или `.pdf`. Например, `old.txt` + `pdf` даст `old.pdf`.
- Попытка сменить расширение через поле имени, path traversal, `/`, `\\`, control characters и `..` отклоняются русским сообщением об ошибке.
- `original_filename`, `local_path` и временный файл не меняются; после изменения безопасно пересобирается только `target_path`.

Mini App и бот показывают основные пользовательские подписи, кнопки, статусы и audit-поля на русском языке. Новый frontend использует поля API `filename_stem`, `filename_extension` и `target_folder`; `safe_filename` сохранён только для обратной совместимости.


## Mini App UI: filters, search, and multi-file upload

Telegram Mini App keeps the existing Telegram WebApp initData authentication and the single-file `POST /api/uploads` endpoint, but the UI now supports selecting multiple files at once. The frontend sends selected files sequentially; each file creates a separate upload request, the shared comment is applied to every request, and a failed file does not stop the remaining uploads.

The Mini App has a cleaner mobile-first layout with cards, status badges, compact file rows, grouped admin actions, empty states, and long Yandex Disk paths that wrap on narrow screens. User requests can be filtered by status: all, pending review, uploaded, failed, and rejected. Admin upload requests can be filtered by status and searched by Telegram ID, username, or full name. Admin search is only a display filter; authorization still uses the validated Telegram WebApp user and numeric `TELEGRAM_ADMIN_IDS`.

### Активная корневая папка Яндекс.Диска

Корневая папка — активная общая папка, внутри которой backend создаёт папки пользователей и куда направляет новые загрузки. Её можно изменить через bot-команду `/setdiskroot` или Mini App: `Администратор → Корневая папка`.

После изменения новые загрузки всех active users идут в пользовательские папки внутри новой root. Если папки пользователя в новой root ещё нет, backend создаёт её повторно. Старые файлы не переносятся, старые upload requests не мигрируются, старые папки не удаляются. Кнопка `Сменить папку этой заявки` меняет только `target_folder` конкретной заявки и не меняет глобальную корневую папку.

### Именование и переименование папок пользователей

Новый пользователь после `/start` проходит анкету: номер договора, дату договора и ФИО по договору. Бот формирует рекомендованное имя папки в формате `<НОМЕР_ДОГОВОРА> от <ДАТА> <ФАМИЛИЯ> <ИМЯ> <ОТЧЕСТВО>` и просит подтвердить его или ввести ручное безопасное имя. Формат рекомендован, но не является жёстким ограничением; backend запрещает полный путь `disk:/...`, slash/backslash, control chars, `..` и слишком длинные сегменты.

Папка пользователя строится backend-ом как `<активная корневая папка>/<folder_name>/`. Если `folder_name` отсутствует у старого пользователя, используется прежний fallback по Telegram ID и имени. При одобрении администратором создаётся папка внутри текущей корневой папки Яндекс.Диска.

Активный пользователь может создать заявку на переименование папки через Mini App endpoint `/api/me/folder-rename-requests`. Команда `/renamefolder` подсказывает открыть Mini App для подачи заявки. Администратор видит pending-заявки во вкладке «Заявки на переименование», выбирает source folder из серверного списка кандидатов и подтверждает или отклоняет заявку.

Администратор также может переименовать папку без заявки: во вкладке «Заявки на переименование» есть поиск пользователей с выпадающим списком и карточка «Переименовать папку». Source folder выбирается только из backend candidates: текущая папка, разрешённые папки и папки из истории upload requests. Переименование выполняется backend-side через Yandex Disk move/rename с `overwrite=false`; токены не передаются во frontend.

Переименование меняет именно папку пользователя, а не общую root folder. Старые файлы физически остаются в переименованной папке на Яндекс.Диске. Если переименована текущая папка пользователя, обновляется `user.root_folder` и `user.folder_name`; совпадающие записи в `allowed_folders` заменяются на новый путь; старые `upload_requests` с этим `target_folder` получают новый `target_folder` и пересчитанный `target_path`.

## Безопасный Docker-запуск

Основная `docker-compose.yml` безопасна по умолчанию: PostgreSQL и Redis доступны только внутри Compose-сети и не публикуют порты на host. API в основной конфигурации только `expose`-ит порт `8000` для внутренних сервисов. Миграции выполняет единственный one-shot сервис `migrate`; API и bot ждут его успешного завершения и сами Alembic не запускают.

### Локальный запуск для разработки

1. Создайте локальный `.env` на основе `.env.example` и задайте реальные секреты только в локальном файле. Не коммитьте `.env`.
2. Запустите основную конфигурацию вместе с dev override:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Dev override публикует только loopback-порты:

- API: `127.0.0.1:8000:8000`;
- PostgreSQL: `127.0.0.1:55432:5432`;
- Redis наружу не публикуется.

`docker compose up --build` собирает image локально и не публикует его ни в Docker Hub, ни в GHCR. В конфигурации нет `docker push`, registry login или публикации images.

### Подключение DBeaver к PostgreSQL

Используйте dev-конфигурацию и подключайтесь к PostgreSQL так:

- host: `127.0.0.1`;
- port: `55432`;
- database: значение `POSTGRES_DB` из `.env` (по умолчанию в примере `bot`);
- user: значение `POSTGRES_USER` из `.env`;
- password: значение `POSTGRES_PASSWORD` из `.env`.

Проверить подключение без публикации стандартного порта PostgreSQL можно командой:

```bash
psql "postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:55432/$POSTGRES_DB"
```

### Проверка состояния, healthcheck и логов

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml ps
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs api bot migrate
docker inspect --format '{{json .State.Health}}' yd_upd_approver-postgres-1
docker inspect --format '{{json .State.Health}}' yd_upd_approver-redis-1
curl http://127.0.0.1:8000/health
```

Логи Docker ограничены через json-file rotation (`max-size` и `max-file`), чтобы долгий локальный запуск не раздувал файлы логов бесконтрольно.

### Пароль PostgreSQL и существующие volumes

`POSTGRES_PASSWORD` применяется официальным образом PostgreSQL image только при первичной инициализации нового data volume. Если volume уже существует, изменение `.env` не меняет пароль роли внутри базы. Удалять volume для смены пароля не нужно.

Безопасные варианты смены пароля существующей роли:

```sql
ALTER ROLE bot WITH PASSWORD 'new-strong-password';
```

или интерактивно в `psql`:

```sql
\password bot
```

После изменения пароля роли обновите `.env` так, чтобы `POSTGRES_PASSWORD` и `DATABASE_URL` совпадали с новым значением.

> Внимание: не выполняйте `docker compose down -v` без актуальной резервной копии. Эта команда удаляет volumes, включая данные PostgreSQL.

### Просмотр локальных images

Безопасные команды просмотра локально собранных images:

```bash
docker image ls yd-upd-approver
docker image inspect yd-upd-approver:security-test --format '{{.Id}} {{.Config.User}}'
```

## Формат ошибок API

Успешные ответы API сохраняют существующий формат. Ошибки `4xx`, `422`, `5xx`, ошибки базы данных и ошибки внешних сервисов возвращаются в едином JSON-контракте:

```json
{
  "error": {
    "code": "file_too_large",
    "message": "Файл превышает допустимый размер.",
    "details": null
  },
  "request_id": "2bc31deef74b4ce582004ff9d23be419"
}
```

- `error.code` — стабильный машинный код ошибки в `snake_case`, на который можно опираться во frontend и при поддержке пользователей.
- `error.message` — безопасное сообщение на русском языке для пользователя.
- `error.details` — `null` либо безопасные структурированные сведения, например расположение и тип ошибки валидации без исходного тела запроса.
- `request_id` — идентификатор запроса, который также возвращается в заголовке `X-Request-ID` для успешных и ошибочных ответов.

Основные коды ошибок:

| Код | Ситуация |
| --- | --- |
| `authentication_required` | Mini App открыт без действительной Telegram-аутентификации |
| `invalid_telegram_init_data` | Некорректные или повреждённые Telegram initData |
| `telegram_init_data_expired` | Истёк срок действия Telegram initData |
| `admin_access_required` | Нужны права администратора |
| `user_not_active` | Пользователь не активирован для загрузки файлов |
| `user_not_found` | Пользователь не найден |
| `request_not_found` | Заявка или временный файл заявки не найдены |
| `file_too_large` | Файл превышает допустимый размер |
| `invalid_request` | Некорректное бизнес-действие или параметр |
| `invalid_request_state` | Действие невозможно в текущем состоянии ресурса |
| `folder_not_allowed` | Пользователю недоступна выбранная папка |
| `resource_conflict` | Конфликт ресурса, например на Яндекс.Диске |
| `yandex_disk_unavailable` | Яндекс.Диск временно недоступен или вернул ошибку авторизации |
| `yandex_disk_insufficient_storage` | Недостаточно места на Яндекс.Диске |
| `database_unavailable` | База данных временно недоступна |
| `validation_error` | Ошибка структуры или валидации запроса |
| `internal_error` | Неожиданная внутренняя ошибка сервера |

Если пользователь сообщает об ошибке, администратору достаточно передать `request_id` / `X-Request-ID`, время операции и краткое описание действия. Traceback, SQL, токены, Telegram initData, credentials, тела запросов и внутренние пути клиенту не возвращаются; подробности неожиданных ошибок остаются только в серверном логе с тем же `request_id`.

## Фоновый worker загрузок

Жизненный цикл upload request теперь отделяет модерацию от передачи файла на Яндекс.Диск:

1. `pending_approval` — файл сохранён локально и ждёт решения администратора.
2. `approved` — заявка поставлена в durable queue PostgreSQL и ожидает worker (`approved` в API означает «в очереди на загрузку»).
3. `uploading` — отдельный worker забрал job через `SELECT ... FOR UPDATE SKIP LOCKED`, выставил lease и выполняет потоковую загрузку.
4. `uploaded` — worker подтвердил загрузку, закоммитил итоговый статус и только после этого удалил временный файл.
5. `failed` — подтверждённая ошибка загрузки; временный файл сохраняется для ручного retry/copy/overwrite.

API и Telegram callback больше не ждут Яндекс.Диск: действия `approve`, `copy`, `overwrite` и `retry` только записывают режим (`normal`, `copy`, `overwrite`), `queued_at`, счётчик попыток и lease-поля в PostgreSQL одной транзакцией. Redis остаётся в compose для будущих задач, но не является источником истины очереди: после рестарта API, bot, Redis или worker job остаётся в таблице `upload_requests`.

Запуск worker:

```bash
python -m app.workers.upload_worker
```

В Docker Compose worker — отдельный сервис без опубликованных портов:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f worker
docker compose ps worker
```

Если worker остановлен, enqueue продолжает работать: заявки остаются в PostgreSQL со статусом `approved`. После запуска worker продолжит обработку. Два worker можно запустить для проверки конкурентной обработки, например масштабированием сервиса, если это поддерживается вашей compose-версией; `SKIP LOCKED` не позволит двум процессам забрать одну строку одновременно.

Worker использует lease/heartbeat. Claim выставляет `worker_token` и `lease_expires_at`, а heartbeat продлевает lease только для строки с тем же token. Если процесс упал или legacy-строка осталась в `uploading` без lease, следующий цикл recovery вернёт job в `approved` без удаления временного файла и без раскрытия секретов в audit/log.

При падении после принятия файла Яндекс.Диском, но до commit `uploaded`, повторный worker перед загрузкой пытается сверить существующий remote resource через `get_info()`: безопасное завершение возможно только при совпадении размера и SHA-256. Совпадения имени или размера недостаточно; при конфликте `normal`/`copy` job становится `failed`, а `overwrite` может повторить загрузку.

Ручной retry разрешён только из `failed` и сохраняет предыдущий `upload_mode` (или использует `normal`, если режима ещё нет). Диагностировать failed job следует по безопасному `error_message`, audit events `upload_failed`, `upload_recovered`, `upload_started`, `upload_uploaded` и server logs без токенов, upload URL, DSN или содержимого файла.

## Telegram transactional outbox

Durable Telegram notifications are stored in PostgreSQL in `telegram_outbox` in the same transaction as the business state and audit row. API handlers, bot handlers, and the upload worker no longer depend on Telegram availability for saved business events; a separate process sends notifications:

```bash
python -m app.workers.telegram_outbox_worker
```

Configure it with `TELEGRAM_OUTBOX_POLL_SECONDS`, `TELEGRAM_OUTBOX_LEASE_SECONDS`, `TELEGRAM_OUTBOX_MAX_ATTEMPTS`, `TELEGRAM_OUTBOX_BASE_RETRY_SECONDS`, and `TELEGRAM_OUTBOX_MAX_RETRY_SECONDS`. Docker Compose includes a dedicated `outbox-worker` service and heartbeat healthcheck. Redis is not used as the outbox queue; PostgreSQL remains the source of truth.

Delivery is durable at-least-once, not exactly-once. Telegram `sendMessage` has no idempotency key, so if the worker crashes after Telegram accepts a message but before the row is marked `sent`, a duplicate can be delivered. Temporary failures are retried with exponential backoff and jitter. Permanent forbidden/bad-request errors and exhausted retries are moved to `dead`. Operators can inspect pending/dead rows in `telegram_outbox` without exposing tokens or local temp paths.

Mini App uploads require an `Idempotency-Key` header. The frontend generates a UUID per file; duplicate retries return the existing upload request while new keys allow intentional duplicate file submissions.
