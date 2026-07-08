from pathlib import Path


def test_webapp_static_files_exist():
    assert Path("app/webapp/index.html").exists()
    assert Path("app/webapp/static/app.js").exists()
    assert Path("app/webapp/static/styles.css").exists()
