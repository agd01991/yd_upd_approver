from pathlib import Path


def test_folder_rename_migration_and_models_exist() -> None:
    migration = Path("alembic/versions/0004_user_folder_names.py").read_text()
    models = Path("app/db/models.py").read_text()
    assert "folder_name" in migration
    assert "folder_rename_requests" in migration
    assert "class FolderRenameRequest" in models
    assert "FolderRenameRequestStatus" in models


def test_frontend_folder_rename_ui_contract() -> None:
    html = Path("app/webapp/index.html").read_text()
    js = Path("app/webapp/static/app.js").read_text()
    assert 'data-tab="renames"' in html
    assert "Переименовать папку" in js
    assert "Заявки на переименование" in js
    assert "rename-source" in js
    assert "escapeHtml(u.folder_name" in js
    assert "YANDEX_DISK_TOKEN" not in js
