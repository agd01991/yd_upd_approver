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

## 4. Non-Docker local: запуск бота, upload worker и outbox worker

После миграций одновременно держите запущенными три процесса. Терминал 1 — `app.main` принимает Telegram updates и пишет состояние/outbox в PostgreSQL:

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

Один только `python -m app.main` не выполняет загрузки на Яндекс.Диск и не доставляет durable outbox notifications: после Approve/Retry заявки останутся в очереди и администратор не получит moderation/result notifications без workers.

Если тестируется Mini App или HTTP-интерфейс, отдельно запустите четвёртый процесс API:

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

## PR 5 storage/Yandex performance checks

1. Stop or block Yandex Disk network access, then submit a small file through the Mini App. Expected: the request is accepted as `pending_approval`; actual Yandex folder creation waits for the upload worker.
2. Upload a file close to `MAX_FILE_SIZE_MB`. Expected: the request succeeds when below the limit and fails with a 413-style message above the limit without leaving a visible final temp file.
3. Open a Yandex Disk folder containing more than 50 objects in the Mini App. Expected: the first page renders quickly, the “Показать ещё” button appends the next page, and repeated clicks while loading do not start parallel page requests.
4. Approve an upload and wait for the worker. Expected: after DB state becomes `uploaded`, local temp cleanup can remove the file and the request eventually becomes `deleted_temp`.
5. Start Docker Compose and check `cleanup-worker` health with `docker compose ps cleanup-worker`; logs should show aggregate checked/deleted counts only, not local paths or tokens.

## PR 6: pagination and DB integrity QA

1. Prepare a dataset with more than 25 uploads, users, audit entries, and folder rename requests.
2. Open the Mini App and verify every paginated list shows the contract-backed controls: previous page, next page, current page number, disabled buttons while loading, and no visible total count.
3. Change each status chip/search field and confirm the list returns to page 1 and fetches filtered data from the server. In particular, `needs_action` must be sent as `status=needs_action` and not derived from the first page in the browser.
4. Navigate forward and backward across pages with rows sharing identical `created_at` values; confirm there are no duplicates or missing rows.
5. Approve/reject/block users, uploads, and folder rename requests from a non-first page; confirm the current page refreshes instead of always resetting to the first page.
6. In two browser sessions, try to create two pending folder rename requests for the same user and assign/rename two users to the same canonical folder. One operation should succeed and the other should return a safe validation/conflict response, not a 500.
7. Before production migration, create a database backup. Run `alembic upgrade head`, verify
   `alembic current` reports the new head `0011_upload_index_ownership`, then verify the upload
   ordering index exists exactly once on the application `upload_requests` table with columns
   `(created_at, id)`. Before running the query, replace
   `REPLACE_WITH_APPLICATION_SCHEMA` with the trusted name of the schema that contains the
   application table (do not infer it from `search_path`):

<!-- upload-index-managed-signature-sql:start -->
```sql
WITH qa_parameters(application_schema) AS (
    VALUES ('REPLACE_WITH_APPLICATION_SCHEMA'::pg_catalog.name)
), target_table AS (
    SELECT t.oid, t.relnamespace, n.nspname
    FROM qa_parameters AS q
    JOIN pg_catalog.pg_namespace AS n
      ON n.nspname OPERATOR(pg_catalog.=) q.application_schema
    JOIN pg_catalog.pg_class AS t
      ON t.relnamespace OPERATOR(pg_catalog.=) n.oid
     AND t.relname OPERATOR(pg_catalog.=) 'upload_requests'::pg_catalog.name
     AND (t.relkind OPERATOR(pg_catalog.=) 'r'::pg_catalog."char"
       OR t.relkind OPERATOR(pg_catalog.=) 'p'::pg_catalog."char")
), managed_index AS (
    SELECT t.nspname, t.oid AS table_oid, i.oid AS index_oid,
           i.relname::pg_catalog.text AS index_name,
           i.relnamespace AS index_schema_oid, x.indnkeyatts, x.indnatts,
           am.amname::pg_catalog.text AS access_method,
           x.indisunique, x.indisexclusion, x.indpred, x.indexprs,
           x.indisvalid, x.indisready,
           pg_catalog.pg_get_indexdef(i.oid) AS index_definition,
           pg_catalog.obj_description(i.oid, 'pg_class') AS ownership_comment,
           pg_catalog.array_agg(a.attname::pg_catalog.text ORDER BY k.ordinality)
             FILTER (WHERE k.ordinality OPERATOR(pg_catalog.<=) x.indnkeyatts::pg_catalog.int8)
             AS key_columns,
           pg_catalog.array_agg(o.option ORDER BY k.ordinality)
             FILTER (WHERE k.ordinality OPERATOR(pg_catalog.<=) x.indnkeyatts::pg_catalog.int8)
             AS key_options,
           pg_catalog.array_agg(opc.oid ORDER BY k.ordinality)
             FILTER (WHERE k.ordinality OPERATOR(pg_catalog.<=) x.indnkeyatts::pg_catalog.int8)
             AS actual_opclasses,
           pg_catalog.array_agg(default_opc.oid ORDER BY k.ordinality)
             FILTER (WHERE k.ordinality OPERATOR(pg_catalog.<=) x.indnkeyatts::pg_catalog.int8)
             AS default_opclasses,
           pg_catalog.count(*) FILTER
             (WHERE k.ordinality OPERATOR(pg_catalog.<=) x.indnkeyatts::pg_catalog.int8)
             AS joined_key_count
    FROM target_table AS t
    JOIN pg_catalog.pg_index AS x ON x.indrelid OPERATOR(pg_catalog.=) t.oid
    JOIN pg_catalog.pg_class AS i
      ON i.oid OPERATOR(pg_catalog.=) x.indexrelid
     AND i.relnamespace OPERATOR(pg_catalog.=) t.relnamespace
     AND i.relname OPERATOR(pg_catalog.=) 'ix_upload_requests_created_id'::pg_catalog.name
    JOIN pg_catalog.pg_am AS am ON am.oid OPERATOR(pg_catalog.=) i.relam
    LEFT JOIN LATERAL pg_catalog.unnest(x.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON true
    LEFT JOIN LATERAL pg_catalog.unnest(x.indoption) WITH ORDINALITY AS o(option, ordinality)
      ON o.ordinality OPERATOR(pg_catalog.=) k.ordinality
    LEFT JOIN pg_catalog.pg_attribute AS a
      ON a.attrelid OPERATOR(pg_catalog.=) x.indrelid
     AND a.attnum OPERATOR(pg_catalog.=) k.attnum
    LEFT JOIN LATERAL pg_catalog.unnest(x.indclass) WITH ORDINALITY AS ic(opclass_oid, ordinality)
      ON ic.ordinality OPERATOR(pg_catalog.=) k.ordinality
    LEFT JOIN pg_catalog.pg_opclass AS opc ON opc.oid OPERATOR(pg_catalog.=) ic.opclass_oid
    LEFT JOIN pg_catalog.pg_opclass AS default_opc
      ON default_opc.opcmethod OPERATOR(pg_catalog.=) am.oid
     AND default_opc.opcintype OPERATOR(pg_catalog.=) a.atttypid
     AND default_opc.opcdefault
    GROUP BY t.nspname, t.oid, i.oid, i.relname, i.relnamespace, x.indnkeyatts,
             x.indnatts, am.amname, x.indisunique, x.indisexclusion, x.indpred,
             x.indexprs, x.indisvalid, x.indisready
)
SELECT nspname::pg_catalog.text AS application_schema, table_oid, index_oid, index_name,
       index_definition, ownership_comment, key_columns,
       key_options::pg_catalog.text[] AS key_options,
       actual_opclasses, default_opclasses, access_method, indnkeyatts AS key_count,
       indnatts AS total_column_count, indisunique, indisexclusion,
       (indpred IS NULL) AS is_not_partial, (indexprs IS NULL) AS is_not_expression,
       indisvalid, indisready,
       (index_schema_oid OPERATOR(pg_catalog.=)
          (SELECT relnamespace FROM target_table)
        AND joined_key_count OPERATOR(pg_catalog.=) 2
        AND indnkeyatts OPERATOR(pg_catalog.=) 2
        AND indnatts OPERATOR(pg_catalog.=) 2
        AND key_columns OPERATOR(pg_catalog.=) ARRAY['created_at','id']::pg_catalog.text[]
        AND key_options OPERATOR(pg_catalog.=) ARRAY[0,0]::pg_catalog.int2[]
        AND actual_opclasses OPERATOR(pg_catalog.=) default_opclasses
        AND pg_catalog.cardinality(actual_opclasses) OPERATOR(pg_catalog.=) 2
        AND access_method OPERATOR(pg_catalog.=) 'btree'::pg_catalog.text
        AND NOT indisunique AND NOT indisexclusion
        AND indpred IS NULL AND indexprs IS NULL AND indisvalid AND indisready
        AND ownership_comment OPERATOR(pg_catalog.=)
          'yd_upd_approver:alembic:0010_upload_created_index'::pg_catalog.text) AS qa_pass
FROM managed_index;
```
<!-- upload-index-managed-signature-sql:end -->

The correct result is exactly one row whose `application_schema` equals the trusted schema and
whose `qa_pass` is SQL `TRUE`; every diagnostic field must also match the displayed structural
contract. Zero or multiple rows, `FALSE`, SQL `NULL`, or any mismatch is a QA failure: do not
proceed with the downgrade. The query validates the current state of this explicitly named index;
it neither replaces the migrations' global target/ownership checks nor prevents the object from
changing between this query and downgrade. A matching definition without the exact marker is not
owned by revision 0010 and will not be removed by its downgrade.
The 0010 downgrade additionally requires exactly one index object in the entire database to bear
the exact marker. A second marker fails transactionally even when it is on another schema, table,
name, or incompatible definition; the migration does not remove or repair either object.

Revisions 0010 and 0011 use one cooperative ownership protocol in both online execution and the
complete generated offline SQL: an exclusive, transaction-level
`pg_catalog.pg_advisory_xact_lock(780984123042210011::pg_catalog.int8)` is acquired before the
first ownership-dependent catalog check. While holding it, the migration resolves and validates
the global owner set, takes the candidate relation's DDL lock, revalidates its OID, schema, name,
comment and complete signature, changes the marker, and validates the postconditions. The lock is
released only with the transaction that commits or rolls back the Alembic version update. This
protocol covers 0010 create/adopt/already-owned upgrade and its destructive downgrade, plus 0011
historical adoption/already-owned upgrade and its validating metadata-only downgrade.

Run these migrations and any supported manual marker maintenance at `READ COMMITTED`; they reject
`REPEATABLE READ` and `SERIALIZABLE`, whose transaction snapshot would not refresh after waiting.
For manual maintenance, use one explicit transaction, verify it is `READ COMMITTED`, acquire the
same one-argument `pg_advisory_xact_lock(bigint)` above, then perform a fresh global owner query,
lock and revalidate the exact candidate relation, issue `COMMENT ON INDEX`, repeat both candidate
and global validations, and commit. Never obtain the candidate relation lock before the global
lock.

The advisory lock is cooperative: arbitrary external `COMMENT`/DDL and previously generated SQL
that do not acquire it are not serialized. Such operations require a maintenance window excluding
0010/0011 and participating manual writers. The relation lock still protects the selected
candidate, but cannot protect a different index, and the global owner rescan remains mandatory.

Revision `0011_upload_index_ownership` is a forward-only ownership backfill for databases that
had already applied the original, unmarked revision `0010`. It identifies the application table
without relying on `search_path`: the table must be an ordinary or partitioned user table named
`upload_requests`, and the full ordinary btree signatures of both
`ix_upload_requests_user_created_id (user_id, created_at, id)` and
`ix_upload_requests_status_created_id (status, created_at, id)` must belong to the same table OID.
The managed index must have the full `(created_at, id)` ascending/default-null-order signature.

Two database layouts can therefore report revision `0009_db_integrity`. The historical 0009
created an unmarked `ix_upload_requests_created_id`; the current 0009 leaves its creation to
0010. A direct `0009_db_integrity` to `0008_telegram_outbox` downgrade succeeds for the current
layout when that index is absent. If an index with that name is attached to the fingerprinted
application table, the downgrade stops transactionally before removing any other 0009 object:
neither an absent marker nor a matching shape proves that the historical migration owns it.

For a compatible historical index, run `alembic upgrade 0011_upload_index_ownership`, repeat the
ownership query above and require the exact managed marker, then run
`alembic downgrade 0008_telegram_outbox`. Reconciliation in 0010/0011 validates the target and
complete index signature before adoption; a conflicting definition or a foreign comment is an
explicit failure and must be investigated rather than overwritten. `alembic stamp` is not a
repair: it only changes the recorded revision and neither validates nor removes schema objects.

For a database already at an old, unmarked `0010_upload_created_index`, **first run**
`alembic upgrade head`, verify the current revision and exact ownership comment above, and only
then perform a rollback. A direct downgrade from an unmarked `0010` intentionally fails safely;
it does not guess that an unmarked object is owned. Downgrading `0011` to `0010` validates and
preserves the marker so that the strict `0010` downgrade can remove only the managed index.
A SQL `NULL` from `obj_description` means that no comment is stored. PostgreSQL treats
`COMMENT ON INDEX ... IS ''` like `IS NULL`: it removes the comment, so a subsequent catalog
read returns SQL `NULL`. Such an unmarked index may be adopted only after all identity and
signature checks succeed. Any actually stored, non-empty foreign comment remains foreign
ownership and is never overwritten.

One unavoidable limitation applies only to this one-time backfill: an unmarked, structurally
identical replacement index on the correctly fingerprinted application table cannot be
distinguished from the historical index created by the old `0010`. This limited adoption rule
does not apply to the normal `0010` downgrade, which continues to require the exact marker.

Then verify workers still claim upload and Telegram outbox jobs.

If `0010_upload_created_index` reports an incompatible index named
`ix_upload_requests_created_id`, inspect its table and key columns first. Do not remove an
index blindly: take a backup, analyze the conflicting object, and only then rename or remove it
before retrying the migration.
8. With Docker, validate configuration and service health:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
docker compose up --build
```
