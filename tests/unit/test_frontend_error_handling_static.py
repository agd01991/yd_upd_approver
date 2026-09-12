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
    assert "let idempotencyFallbackCounter = 0;" in js
    assert "function createIdempotencyKey()" in js
    assert "function createUploadEntry(file)" in js
    assert "idempotencyKey: createIdempotencyKey()" in js
    assert (
        "selectedUploadEntries = Array.from(files || []).map((file) => createUploadEntry(file));"
        in js
    )
    upload_body = js[
        js.index("async function uploadSelectedFiles") : js.index("async function loadUserLists")
    ]
    assert "createIdempotencyKey()" not in upload_body
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


def test_create_idempotency_key_has_webview_safe_fallbacks() -> None:
    js = source()
    helper_body = js[
        js.index("function createIdempotencyKey") : js.index("function createUploadEntry")
    ]
    assert 'typeof cryptoApi?.randomUUID === "function"' in helper_body
    assert "cryptoApi.randomUUID()" in helper_body
    assert "catch {" in helper_body
    assert 'typeof cryptoApi?.getRandomValues === "function"' in helper_body
    assert "new Uint8Array(16)" in helper_body
    assert "cryptoApi.getRandomValues(bytes)" in helper_body
    assert 'toString(16).padStart(2, "0")' in helper_body
    assert "webcrypto-${hex}" in helper_body
    assert "idempotencyFallbackCounter += 1;" in helper_body
    assert "Date.now()" in helper_body
    assert "globalThis.performance?.now" in helper_body
    assert "Math.random()" in helper_body
    assert ".slice(0, 128)" in helper_body
    assert "/^[A-Za-z0-9._:-]{1,128}$/" in helper_body
    assert "btoa" not in helper_body
    assert "base64" not in helper_body.lower()
    assert "+" not in "webcrypto-${hex}"


def test_upload_rejects_invalid_idempotency_key_before_request() -> None:
    js = source()
    upload_body = js[
        js.index("async function uploadSelectedFiles") : js.index("async function loadUserLists")
    ]
    validation_index = upload_body.index(
        'if (!/^[A-Za-z0-9._:-]{1,128}$/.test(entry.idempotencyKey || ""))'
    )
    api_index = upload_body.index('await api("/api/uploads"')
    assert validation_index < api_index
    assert "Не удалось подготовить безопасный ключ загрузки" in upload_body


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


def test_upload_resets_user_uploads_pager_only_after_success_before_refresh() -> None:
    js = source()
    upload_body = js[
        js.index("async function uploadSelectedFiles") : js.index("async function loadUserLists")
    ]
    success_count_index = upload_body.index(
        'const successCount = selectedUploadEntries.filter((entry) => entry.status === "done").length;'
    )
    retain_failed_index = upload_body.index(
        'selectedUploadEntries = selectedUploadEntries.filter((entry) => entry.status !== "done");'
    )
    reset_index = upload_body.index('if (successCount > 0) resetPager("userUploads");')
    refresh_index = upload_body.index("await loadUserLists();")
    assert success_count_index < retain_failed_index < reset_index < refresh_index
    assert 'headers: { "Idempotency-Key": entry.idempotencyKey }' in upload_body
    assert "createIdempotencyKey()" not in upload_body
