# Implementation Plan

## MVP scope

Build an async Python Telegram bot that stores user uploads locally, creates moderation requests, and uploads approved files to the administrator's Yandex Disk using only the administrator OAuth token.

## Decisions

- Use aiogram 3 routers and inline callback data with explicit admin checks.
- Use PostgreSQL through SQLAlchemy 2 async and Alembic migrations.
- Keep upload processing synchronous for MVP: approval callback invokes the upload worker directly.
- Keep temporary files on local disk. Successful uploads delete temp files; failed uploads keep temp files for retry.
- Users cannot provide arbitrary Yandex Disk paths. Folder selection is limited to folders assigned in `users.allowed_folders` and rooted under each user's root folder.
- No public Yandex Disk links are created.

## Work breakdown

1. Create project structure and Python package metadata.
2. Implement configuration and safe logging helpers.
3. Implement database models, async session setup, repositories, and first Alembic migration.
4. Implement security, naming, formatting, storage, file policy, approval, audit, Telegram file, and Yandex Disk services.
5. Implement Telegram routers for common, user, admin, and file flows.
6. Implement upload worker and conflict handling.
7. Add tests for security, paths, authorization, status transitions, Yandex Disk client behavior, conflicts, and admin callback protection.
8. Add Docker Compose, Dockerfile, `.env.example`, `.gitignore`, and README.
9. Run pytest, ruff, Alembic upgrade, and import/startup checks.
