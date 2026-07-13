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


def test_upload_entries_retain_idempotency_keys_for_retry() -> None:
    js = source()
    assert "let selectedUploadEntries = [];" in js
    assert "let uploadInProgress = false;" in js
    assert "function createUploadEntry(file)" in js
    assert "idempotencyKey: crypto.randomUUID()" in js
    assert (
        "selectedUploadEntries = Array.from(files || []).map((file) => createUploadEntry(file));"
        in js
    )
    upload_body = js[
        js.index("async function uploadSelectedFiles") : js.index("async function loadUserLists")
    ]
    assert "crypto.randomUUID()" not in upload_body
    assert 'headers: { "Idempotency-Key": entry.idempotencyKey }' in upload_body
    assert 'entry.status = "failed";' in upload_body
    assert (
        'selectedUploadEntries = selectedUploadEntries.filter((entry) => entry.status !== "done");'
        in upload_body
    )
    assert (
        "if (failedCount === 0) clearSelectedUploadFiles(form); else renderSelectedFiles();"
        in upload_body
    )
    assert "form.reset();" not in upload_body


def test_upload_new_selection_replaces_entries_with_new_keys() -> None:
    js = source()
    assert "input.onchange = () => setSelectedUploadFiles(input.files);" in js
    selection_body = js[
        js.index("function setSelectedUploadFiles") : js.index("function clearSelectedUploadFiles")
    ]
    assert (
        "selectedUploadEntries = Array.from(files || []).map((file) => createUploadEntry(file));"
        in selection_body
    )
    assert "renderSelectedFiles();" in selection_body
