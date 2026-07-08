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

## Runtime Yandex Disk root QA

1. As a non-admin Telegram user, send `/diskroot` and `/setdiskroot disk:/Nope`; the bot must not change settings.
2. As an admin from `TELEGRAM_ADMIN_IDS`, send `/diskroot`; verify it shows either the admin-defined setting or fallback `.env` value.
3. Send `/setdiskroot` without arguments; verify the bot asks for `disk:/Telegram Uploads`, then send a valid path such as `disk:/Telegram Uploads/Test`.
4. Verify the bot creates the folder on Yandex Disk before saving. If Yandex Disk returns an error, the setting must not be saved.
5. Approve a new user and verify their folder is created inside the new root.
6. Verify users approved before the change keep their old assigned folders and uploads still use `user.root_folder`.

For Docker `.env`, use `postgres` and `redis` hostnames and JSON arrays for `TELEGRAM_ADMIN_IDS` and `CORS_ORIGINS`. For non-Docker local runs, `localhost` is appropriate for PostgreSQL and Redis.
