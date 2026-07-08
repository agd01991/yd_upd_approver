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
