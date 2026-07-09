from pathlib import Path


def test_disk_root_admin_ui_static_contract() -> None:
    html = Path("app/webapp/index.html").read_text()
    js = Path("app/webapp/static/app.js").read_text()
    docs = Path("README.md").read_text() + Path("docs/MINI_APP.md").read_text()
    assert 'data-tab="disk-root"' in html
    assert "Корневая папка" in html
    assert "function renderDiskRootSettings" in js
    assert 'api("/api/admin/disk-root")' in js
    assert 'api("/api/admin/disk-root", { method: "PUT"' in js
    assert "внутри которой создаются папки пользователей" in js
    assert "Сменить папку этой заявки" in js
    assert "только для новых пользователей" not in html + js + docs
