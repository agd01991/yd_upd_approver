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
    assert 'if (!setSelectedUploadFiles(input.files)) input.value = "";' in js
    selection_body = js[
        js.index("function setSelectedUploadFiles") : js.index("function clearSelectedUploadFiles")
    ]
    guard_index = selection_body.index("if (uploadInProgress) return false;")
    assignment_index = selection_body.index(
        "selectedUploadEntries = Array.from(files || []).map((file) => createUploadEntry(file));"
    )
    uuid_index = selection_body.index("createUploadEntry(file)")
    render_index = selection_body.index("renderSelectedFiles();")
    assert guard_index < assignment_index
    assert guard_index < uuid_index
    assert guard_index < render_index
    assert "return true;" in selection_body


def test_upload_selection_guard_blocks_state_replacement_before_entry_creation() -> None:
    js = source()
    selection_body = js[
        js.index("function setSelectedUploadFiles") : js.index("function clearSelectedUploadFiles")
    ]
    guard_index = selection_body.index("if (uploadInProgress) return false;")
    assignment_index = selection_body.index(
        "selectedUploadEntries = Array.from(files || []).map((file) => createUploadEntry(file));"
    )
    entry_creation_index = selection_body.index("createUploadEntry(file)")
    render_index = selection_body.index("renderSelectedFiles();")
    assert guard_index < assignment_index
    assert guard_index < entry_creation_index
    assert guard_index < render_index


def test_upload_disables_file_input_during_in_progress_window() -> None:
    js = source()
    upload_body = js[
        js.index("async function uploadSelectedFiles") : js.index("async function loadUserLists")
    ]
    start_index = upload_body.index("uploadInProgress = true;")
    input_disabled_index = upload_body.index("input.disabled = true;")
    submit_disabled_index = upload_body.index("if (submitButton) submitButton.disabled = true;")
    try_index = upload_body.index("try {")
    finally_index = upload_body.index("} finally {")
    progress_reset_index = upload_body.index("uploadInProgress = false;", finally_index)
    input_enabled_index = upload_body.index("input.disabled = false;", finally_index)
    submit_enabled_index = upload_body.index(
        "if (submitButton) submitButton.disabled = false;", finally_index
    )
    assert start_index < input_disabled_index < submit_disabled_index < try_index
    assert finally_index < progress_reset_index < input_enabled_index < submit_enabled_index


def test_upload_retry_keeps_existing_idempotency_key_and_failed_entries() -> None:
    js = source()
    upload_body = js[
        js.index("async function uploadSelectedFiles") : js.index("async function loadUserLists")
    ]
    assert 'headers: { "Idempotency-Key": entry.idempotencyKey }' in upload_body
    assert "crypto.randomUUID()" not in upload_body
    assert 'entry.status = "failed";' in upload_body
    assert (
        'selectedUploadEntries = selectedUploadEntries.filter((entry) => entry.status !== "done");'
        in upload_body
    )
    assert "clearSelectedUploadFiles(form)" in upload_body
    assert "form.reset();" not in upload_body


def test_upload_selection_change_remains_available_after_upload() -> None:
    js = source()
    selection_body = js[
        js.index("function setSelectedUploadFiles") : js.index("function clearSelectedUploadFiles")
    ]
    upload_body = js[
        js.index("async function uploadSelectedFiles") : js.index("async function loadUserLists")
    ]
    assert "return true;" in selection_body
    assert "uploadInProgress = false;" in upload_body
    assert "input.disabled = false;" in upload_body
    assert "input.onchange = () => {" in js
    assert 'if (!setSelectedUploadFiles(input.files)) input.value = "";' in js
