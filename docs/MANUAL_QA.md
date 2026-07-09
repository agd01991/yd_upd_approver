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
