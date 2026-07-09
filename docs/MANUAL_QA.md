# Manual QA Checklist

## 1. Подготовка `.env`

1. Скопируйте `.env.example` в `.env`.
2. Заполните `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `YANDEX_DISK_TOKEN`.
3. Проверьте `YANDEX_DISK_ROOT`, `DATABASE_URL`, `TEMP_STORAGE_DIR`, `MAX_FILE_SIZE_MB`.

## 2. Запуск БД

```bash
docker compose up -d postgres redis
```

## 3. Миграции

```bash
alembic upgrade head
```

## 4. Запуск бота

```bash
python -m app.main
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
- Admins can run `/setdiskroot disk:/New Root` or `/setdiskroot` interactively to change the root for newly approved users.
- The bot validates the path and creates the Yandex Disk folder before saving; if folder creation fails, the setting is not saved.
- Changing the root affects only new users approved after the change. Existing active users keep their current folders and are not migrated.
- Mini App user approval uses the same runtime root with fallback to `YANDEX_DISK_ROOT`. Uploads continue to use each user's assigned `user.root_folder`.

## Проверка раздельного изменения имени и расширения

### Telegram-бот
1. Отправьте активным пользователем файл `old.txt` и откройте заявку администратором.
2. Нажмите `Изменить имя`, введите `тест` и проверьте, что имя в карточке стало `тест.txt`: расширение сохранено, `original_filename` и локальный временный файл не меняются, а путь на Яндекс.Диске пересобран с новым безопасным именем.
3. Нажмите `Изменить расширение`, введите `pdf` или `.pdf` и проверьте, что имя стало `тест.pdf`: имя файла сохранено, меняется только расширение.
4. Попробуйте в поле имени ввести `тест.pdf` для файла с расширением `.txt` — бот должен показать русское сообщение об ошибке и не менять заявку.
5. Для заявок в статусах `uploaded` и `rejected` кнопки изменения должны быть запрещены.

### Mini App
1. Откройте Mini App из Telegram и войдите администратором.
2. Во вкладке пользователей проверьте русские кнопки `Одобрить`, `Отклонить` и `Заблокировать`.
3. В карточке заявки проверьте русские кнопки `Загрузить`, `Загрузить как копию`, `Перезаписать`, `Повторить`, `Отклонить`, а также отдельные кнопки `Изменить имя`, `Изменить расширение` и `Сменить папку`.
4. Проверьте, что Mini App отправляет `filename_stem` для имени и `filename_extension` для расширения, не отправляя `safe_filename` в новом flow.
5. Все пользовательские подписи, статусы, кнопки и ошибки должны отображаться на русском языке.


## Manual QA: Mini App filters, search, multi-file upload

### User scenario

1. Open the Mini App from Telegram as an approved user.
2. Verify the auth card, upload card, request cards, file list, badges, and empty states are readable on a narrow mobile viewport.
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
