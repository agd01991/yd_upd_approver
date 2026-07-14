# Manual QA Checklist

## 1. Подготовка `.env`

1. Скопируйте `.env.example` в `.env`.
2. Заполните `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `YANDEX_DISK_TOKEN`.
3. Проверьте `YANDEX_DISK_ROOT`, `DATABASE_URL`, `TEMP_STORAGE_DIR`, `MAX_FILE_SIZE_MB`.

## 2. Docker Compose запуск

Для обычного Docker-запуска достаточно одной команды:

```bash
docker compose up --build
```

Compose запускает PostgreSQL с healthcheck, затем one-shot сервис `migrate` выполняет `alembic upgrade head`, и только после успешного завершения миграций стартуют `api`, `bot`, `outbox-worker`, `worker` и зависимости. `api` и `bot` не запускают Alembic самостоятельно.

Если локальная БД уже была частично обновлена старой версией Compose после race condition, обычно достаточно повторить запуск без удаления PostgreSQL volume:

```bash
docker compose up --build
```

Либо выполнить шаги отдельно:

```bash
docker compose up -d migrate
docker compose up -d api bot outbox-worker worker
```

Не используйте `docker compose down -v` как основной способ восстановления, потому что он удаляет локальные данные PostgreSQL.

## 3. Non-Docker local: миграции

```bash
alembic upgrade head
```

## 4. Non-Docker local: запуск бота и outbox worker

После миграций одновременно держите запущенными два процесса. Терминал 1 — бот принимает Telegram updates и пишет durable-события в PostgreSQL:

```bash
python -m app.main
```

Терминал 2 — отдельный worker доставляет Telegram outbox notifications:

```bash
python -m app.workers.telegram_outbox_worker
```

Один только `python -m app.main` не доставляет durable outbox notifications, поэтому администратор не получит moderation card без worker.

или API:

```bash
uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```

## 5. Новый пользователь

1. Напишите `/start` от не-admin Telegram-аккаунта.
2. Проверьте, что пользователь получил сообщение ожидания.
3. Проверьте, что админ получил карточку пользователя.

## 6. Approve пользователя

1. Нажмите `Одобрить` в карточке пользователя.
2. Проверьте уведомление пользователя.
3. Проверьте, что папка пользователя создана в `YANDEX_DISK_ROOT`.

## 7. Отправка файла

1. Отправьте документ пользователем.
2. Проверьте ответ: файл отправлен на проверку.
3. Проверьте, что пользователь не получил admin-кнопки.
4. Проверьте карточку файла у администратора.

## 8. Open

1. Нажмите `Открыть файл`.
2. Проверьте, что бот отправил админу временный файл.

## 9. List

1. Нажмите `Содержимое папки`.
2. Проверьте список файлов или сообщение о пустой/несозданной папке.

## 10. Approve/upload

1. Нажмите `Загрузить`.
2. Проверьте уведомления пользователя и админа.
3. Проверьте файл в Яндекс.Диске.
4. Проверьте удаление временного файла после успеха.

## 11. Reject

1. Отправьте новый файл.
2. Нажмите `Отклонить`.
3. Выберите причину.
4. Проверьте уведомление пользователя и audit log.

## 12. Retry

1. Смоделируйте ошибку загрузки или конфликт.
2. Нажмите `Повторить` после failed.
3. Проверьте, что временный файл не удалён до успеха.

## 13. Copy/overwrite

1. Создайте конфликт имени.
2. Проверьте `Как копию` и формат имени копии.
3. Проверьте `Перезаписать` только из admin callback.

## 14. `/myfiles`

1. Выполните `/myfiles` пользователем.
2. Проверьте, что отображается только личная папка пользователя.

## 15. `/status`

1. Выполните `/status` пользователем.
2. Проверьте последние заявки и статусы.

## 16. `/audit`

1. Выполните `/audit` админом.
2. Проверьте последние действия.
3. Выполните `/audit` обычным пользователем и проверьте отсутствие доступа.


## Runtime Yandex Disk root

- `YANDEX_DISK_ROOT` is now a fallback/default. Docker `.env` should point `DATABASE_URL` to `postgres` and `REDIS_URL` to `redis`; use `localhost` only for non-Docker local runs.
- Admins can run `/diskroot` to see the active root and whether it comes from `.env` or DB.
- Admins can run `/setdiskroot disk:/New Root` or `/setdiskroot` interactively to change the active root for new uploads of all active users.
- The bot validates the path and creates the Yandex Disk folder before saving; if folder creation fails, the setting is not saved.
- Changing the root affects only new users approved after the change. Existing active users keep their current folders and are not migrated.
- Mini App user approval uses the same runtime root with fallback to `YANDEX_DISK_ROOT`. Before each new upload and file listing, the backend ensures the user folder exists under the active root.

## Проверка раздельного изменения имени и расширения

### Telegram-бот
1. Отправьте активным пользователем файл `old.txt` и откройте заявку администратором.
2. Нажмите `Изменить имя`, введите `тест` и проверьте, что имя в карточке стало `тест.txt`: расширение сохранено, `original_filename` и локальный временный файл не меняются, а путь на Яндекс.Диске пересобран с новым безопасным именем.
3. Нажмите `Изменить расширение`, введите `pdf` или `.pdf` и проверьте, что имя стало `тест.pdf`: имя файла сохранено, меняется только расширение.
4. Попробуйте в поле имени ввести `тест.pdf` для файла с расширением `.txt` — бот должен показать русское сообщение об ошибке и не менять заявку.
5. Для заявок в статусах `uploaded` и `rejected` кнопки изменения должны быть запрещены.

### Mini App
1. Откройте Mini App из Telegram и войдите администратором.
2. Во вкладке пользователей проверьте русские кнопки `Одобрить`, `Отклонить` и `Заблокировать`, а также строку с папкой пользователя на Яндекс.Диске (`folder_name`/`root_folder_label` или `не назначена`).
3. В карточке заявки проверьте русские кнопки `Загрузить`, `Загрузить как копию`, `Перезаписать`, `Повторить`, `Отклонить`, а также отдельные кнопки `Изменить имя`, `Изменить расширение` и `Сменить папку этой заявки`.
4. Проверьте, что Mini App отправляет `filename_stem` для имени и `filename_extension` для расширения, не отправляя `safe_filename` в новом flow.
5. Все пользовательские подписи, статусы, кнопки и ошибки должны отображаться на русском языке.


## Manual QA: Mini App filters, search, multi-file upload

### User scenario

1. Open the Mini App from Telegram as an approved user.
2. Verify the auth card, upload card, request cards, file list, badges, and empty states are readable on a narrow mobile viewport. The auth card must show the user folder name; if no folder is assigned yet, it must show `не назначена`.
3. Select several files and verify the pre-upload list shows number, filename, and size for each file.
4. Add one shared comment and submit. Verify the progress text shows `Загружается X из N`.
5. Confirm every successful file creates a separate request and the request list refreshes after completion.
6. Include one invalid/oversized file if possible and verify the remaining files continue uploading while the failed file is shown as an error.
7. Switch request status chips and verify empty states say that no requests exist for the selected status when applicable.

### Administrator scenario

1. Open the Mini App as a Telegram ID listed in `TELEGRAM_ADMIN_IDS`.
2. In `Администратор → Заявки`, verify the redesigned cards show request code, status badge, file name, size, user, short SHA-256, Yandex Disk path, and comment/error/reject reason.
3. Test status chips: all, pending review, uploaded, failed, rejected, and waiting for action.
4. Search requests by Telegram ID, username, and full name; verify the clear button resets the list.
5. Confirm grouped actions still work: open temp file, upload, copy, overwrite, retry, rename stem, change extension, change folder, and reject.
6. Verify a non-admin Telegram user cannot open admin endpoints or see admin data.

## QA: активная корневая папка Яндекс.Диска

1. Создать или иметь active user с root `disk:/Telegram Uploads/...`.
2. В Mini App админом открыть `Администратор → Корневая папка`.
3. Задать `disk:/Test Root`.
4. Проверить, что root сохранён.
5. Проверить, что папки active users созданы внутри `disk:/Test Root`.
6. Отправить новый файл от старого active user.
7. Проверить, что новая заявка получила `target_folder` внутри `disk:/Test Root`.
8. Проверить, что старые заявки остались со старым `target_path`.
9. Проверить, что старые файлы не переносились автоматически.

## Manual QA: имена и переименование папок пользователей

1. Новый пользователь отправляет `/start`.
2. Бот спрашивает номер договора, дату договора и ФИО.
3. Бот формирует имя папки в формате `12345 от 09.07.2026 Иванов Иван Иванович`.
4. Пользователь подтверждает имя или выбирает изменение имени/данных.
5. После подтверждения администратор получает карточку с Telegram ID, username, Telegram ФИО, договором, датой, ФИО по договору, именем папки и текущей root folder.
6. Администратор одобряет пользователя.
7. Папка создаётся в текущей корневой папке Яндекс.Диска с подтверждённым именем.
8. Active user создаёт заявку на переименование через Mini App `/api/me/folder-rename-requests`.
9. Администратор открывает Mini App → «Заявки на переименование».
10. Администратор выбирает source folder из селектора candidates: текущую или предыдущую папку из `allowed_folders`/истории загрузок.
11. Администратор одобряет заявку.
12. Backend выполняет Yandex Disk move/rename с `overwrite=false`.
13. `user.root_folder` обновляется, если переименована текущая папка пользователя; общая root folder не меняется.
14. Совпадающие `allowed_folders` и старые `upload_requests.target_folder`/`target_path` обновляются на новый путь.
15. Поиск пользователей в Mini App показывает выпадающий список, результаты экранируются, по клику пользователь выбирается для переименования без заявки.

## Transactional Telegram outbox manual QA

PostgreSQL is the source of truth for durable Telegram notifications. Redis is not used as a queue. Telegram delivery is at-least-once: if the outbox worker crashes after Telegram accepts a message but before the row is marked `sent`, a rare duplicate notification can be delivered.

1. Stop only the `outbox-worker` service.
2. Create an upload request from Telegram or the Mini App.
3. Verify that the upload request is saved and a `telegram_outbox` row exists with `status='pending'`.
4. Start `outbox-worker` and verify the row moves to `sent` with `sent_at` and `telegram_message_id` populated.
5. Repeat the same admin action or callback and verify audit/outbox rows are not duplicated.
6. Temporarily make Telegram unavailable and verify `attempt_count`, `last_error`, and `next_attempt_at` are updated with retry/backoff; permanent forbidden/bad-request errors move to `dead`.
7. Repeat a Mini App upload with the same `Idempotency-Key` and verify the existing `request_code`/`status` is returned.
8. Inspect stuck rows safely, for example: `select id,event_type,status,attempt_count,next_attempt_at,last_error from telegram_outbox where status in ('pending','dead') order by id;`.
