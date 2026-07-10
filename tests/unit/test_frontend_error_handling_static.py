from pathlib import Path

APP_JS = Path("app/webapp/static/app.js")


def source() -> str:
    return APP_JS.read_text()


def test_download_preserves_api_client_error() -> None:
    js = source()
    assert "new Error(await readError" not in js
    assert "throw await readError(response)" in js


def test_folder_candidates_failure_blocks_stale_rename_posts() -> None:
    js = source()
    assert "let renameFolderCandidates = [];" in js
    assert 'resetRenameSelection("Загрузка папок пользователя…")' in js
    assert "setRenameControlsEnabled(false)" in js
    assert "throw err;" in js
    assert "selectedRenameSourceFolder()" in js
    assert "renameFolderCandidates.some((c) => c.path === value)" in js
    assert "await selectRenameUser({ id: userId }, { showErrors: false })" in js


def test_rename_selection_uses_version_to_ignore_stale_folder_candidates() -> None:
    js = source()
    assert "let renameSelectionVersion = 0;" in js
    assert "const selectionVersion = ++renameSelectionVersion;" in js
    assert "function isCurrentRenameSelection(selectionVersion)" in js
    assert "if (!isCurrentRenameSelection(selectionVersion)) return false;" in js
    assert "const selected = await selectRenameUser({ id: userId }, { showErrors: false })" in js
    assert "if (!selected) return;" in js
    assert "selectedRenameUser = user;" in js
    assert "renameFolderCandidates = items;" in js
    assert "renameSelectionVersion += 1;" in js
