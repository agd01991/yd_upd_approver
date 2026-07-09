# Telegram Mini App

## Architecture

The Mini App adds a FastAPI backend in `app/api` and a vanilla HTML/CSS/JS frontend in `app/webapp`. The bot process remains separate. Both bot and API use the same PostgreSQL database, SQLAlchemy models, temporary storage, audit log, runtime `app_settings`, and administrator-owned Yandex Disk OAuth token.

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

Run `uvicorn app.api.main:app --host 0.0.0.0 --port 8000` or `docker compose up --build`, then open `http://localhost:8000/` for a smoke test. Real Telegram Mini Apps require a public HTTPS URL, so expose port 8000 via ngrok, cloudflared, or another HTTPS tunnel and set `WEBAPP_URL=https://...`.

## Production

Deploy the API behind HTTPS, set `WEBAPP_URL`, keep `CORS_ORIGINS` restricted to trusted origins, run Alembic migrations, and configure the bot menu/button in BotFather or use the `/app` command.

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
