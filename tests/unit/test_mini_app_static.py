from pathlib import Path


def test_webapp_static_files_exist():
    assert Path("app/webapp/index.html").exists()
    assert Path("app/webapp/static/app.js").exists()
    assert Path("app/webapp/static/styles.css").exists()


def test_webapp_root_serves_html():
    pytest = __import__("pytest")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from app.api.main import app

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_webapp_app_js_served():
    pytest = __import__("pytest")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from app.api.main import app

    response = TestClient(app).get("/static/app.js")

    assert response.status_code == 200
    assert "downloadTemp" in response.text


def test_webapp_action_labels_are_context_specific():
    app_js = Path("app/webapp/static/app.js").read_text()

    assert 'UPLOAD_ACTION_LABELS = { approve: "Загрузить"' in app_js
    assert 'USER_ACTION_LABELS = { approve: "Одобрить"' in app_js
    assert "${uploadActionLabel(a)}" in app_js
    assert "${userActionLabel(a)}" in app_js
    assert "${actionLabel(a)}" not in app_js


def test_webapp_supports_multiple_uploads_filters_and_escaping():
    app_js = Path("app/webapp/static/app.js").read_text()

    assert "function escapeHtml(value)" in app_js
    assert 'type="file" name="file" multiple' in app_js
    assert "USER_FILTERS = [" in app_js
    assert "ADMIN_FILTERS = [" in app_js
    assert "user_query" in app_js
    assert "Загружается ${index + 1} из ${files.length}" in app_js
    assert "Ожидают действия" in app_js
    assert "renderAdminUsers" in app_js and "userActionLabel(a)" in app_js
    assert "renderAdminUploads" in app_js and "uploadActionLabel(a)" in app_js


def test_webapp_displays_user_folder_name_safely():
    app_js = Path("app/webapp/static/app.js").read_text()

    assert "function userFolderLabel" in app_js
    assert "user?.folder_name || user?.root_folder_label" in app_js
    assert 'user?.root_folder_assigned ? "назначена" : "не назначена"' in app_js
    assert (
        'Папка на Яндекс.Диске: ${me.root_folder_assigned ? "назначена" : "не назначена"}'
        not in app_js
    )
    assert "Папка на Яндекс.Диске: ${escapeHtml(userFolderLabel(me))}" in app_js
    assert "Папка на Яндекс.Диске: ${escapeHtml(userFolderLabel(u))}" in app_js
