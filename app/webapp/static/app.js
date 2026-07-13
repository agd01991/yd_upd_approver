const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const initData = tg?.initData || "";
const auth = document.querySelector("#auth");
const userEl = document.querySelector("#user");
const adminEl = document.querySelector("#admin");
const adminContent = document.querySelector("#admin-content");

let userUploads = [];
let userStatusFilter = "all";
let adminStatusFilter = "all";
let adminUserQuery = "";
let adminSearchTimer;
let selectedRenameUser = null;
let renameFolderCandidates = [];
let renameSelectionVersion = 0;
let selectedUploadEntries = [];
let uploadInProgress = false;

const STATUS_LABELS = {
  pending: "ожидает одобрения",
  active: "активен",
  rejected: "отклонён",
  blocked: "заблокирован",
  stored: "сохранён временно",
  new: "новый",
  pending_approval: "на проверке",
  approved: "в очереди на загрузку",
  uploading: "загружается",
  uploaded: "загружено",
  failed: "ошибка",
  cancelled: "отменено",
  deleted_temp: "временный файл удалён",
};
const UPLOAD_ACTION_LABELS = { approve: "Загрузить", copy: "Загрузить как копию", overwrite: "Перезаписать", retry: "Повторить", reject: "Отклонить" };
const USER_ACTION_LABELS = { approve: "Одобрить", reject: "Отклонить", block: "Заблокировать" };
const AUDIT_LABELS = { upload_filename_stem_change: "изменение имени файла", upload_filename_extension_change: "изменение расширения файла", upload_patch: "изменение заявки", upload_folder_change: "изменение папки" };
const USER_FILTERS = [
  ["all", "Все"],
  ["pending_approval", "На проверке"],
  ["uploaded", "Загружены"],
  ["failed", "Ошибка"],
  ["rejected", "Отклонены"],
];
const ADMIN_FILTERS = [
  ["all", "Все"],
  ["pending_approval", "На проверке"],
  ["uploaded", "Загружены"],
  ["failed", "Ошибка"],
  ["rejected", "Отклонены"],
  ["needs_action", "Ожидают действия"],
];
const NEEDS_ACTION_STATUSES = new Set(["pending_approval", "failed"]);
const QUEUED_STATUSES = new Set(["approved", "uploading"]);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[char]);
}

function statusLabel(value) { return STATUS_LABELS[value] || value || "—"; }
function uploadActionLabel(value) { return UPLOAD_ACTION_LABELS[value] || value; }
function userActionLabel(value) { return USER_ACTION_LABELS[value] || value; }
function auditLabel(value) { return AUDIT_LABELS[value] || value; }
function fmtSize(n) { return n ? `${(n / 1048576).toFixed(2)} MB` : "—"; }
function shortSha(value) { return value ? value.slice(0, 12) : "—"; }
function userFolderLabel(user) { return user?.folder_name || user?.root_folder_label || (user?.root_folder_assigned ? "назначена" : "не назначена"); }

class ApiClientError extends Error {
  constructor({ status = 0, code = "network_error", message, details = null, requestId = null, network = false }) {
    super(message || "Ошибка запроса.");
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
    this.network = network;
  }
}

function safeErrorMessage(error) {
  if (error?.network) return "Нет соединения с сервером. Проверьте интернет и повторите попытку.";
  if (error?.status === 401) return "Откройте Mini App через Telegram заново.";
  if (error?.status === 403) return "Недостаточно прав для выполнения операции.";
  if (error?.status === 413) return "Файл превышает допустимый размер.";
  if (error?.status === 422) return "Проверьте корректность заполненных полей.";
  if (error?.status >= 500) return `${error.message || "Сервис временно недоступен. Повторите попытку позже."}${error.requestId ? ` Код обращения: ${error.requestId}` : ""}`;
  return String(error?.message || "Ошибка запроса.");
}

async function readError(response) {
  const requestId = response.headers.get("X-Request-ID");
  const text = await response.text();
  if (!text) return new ApiClientError({ status: response.status, message: "Ошибка запроса.", requestId });
  try {
    const data = JSON.parse(text);
    if (data?.error && typeof data.error === "object") {
      return new ApiClientError({ status: response.status, code: data.error.code, message: data.error.message, details: data.error.details, requestId: data.request_id || requestId });
    }
    if (typeof data.detail === "string") return new ApiClientError({ status: response.status, message: data.detail, requestId });
    if (typeof data.message === "string") return new ApiClientError({ status: response.status, message: data.message, requestId });
  } catch {
    return new ApiClientError({ status: response.status, message: text, requestId });
  }
  return new ApiClientError({ status: response.status, message: "Ошибка запроса.", requestId });
}

async function api(path, opts = {}) {
  opts.headers = { ...(opts.headers || {}), "X-Telegram-Init-Data": initData };
  let response;
  try {
    response = await fetch(path, opts);
  } catch {
    throw new ApiClientError({ network: true, message: "Нет соединения с сервером. Проверьте интернет и повторите попытку." });
  }
  if (!response.ok) throw await readError(response);
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

function chipHtml(filters, current, prefix) {
  return `<div class="chips">${filters.map(([value, label]) =>
    `<button class="chip ${value === current ? "active" : ""}" data-${prefix}-filter="${value}">${label}</button>`
  ).join("")}</div>`;
}

function showAdminError(message) {
  const error = document.querySelector("#admin-error");
  if (error) error.textContent = message; else alert(message);
}

async function renderUser(me) {
  if (me.status === "pending") {
    userEl.innerHTML = '<div class="card empty">Доступ ожидает одобрения администратора.</div>';
    return;
  }
  if (["blocked", "rejected"].includes(me.status)) {
    userEl.innerHTML = '<div class="card empty">Загрузка файлов для аккаунта недоступна.</div>';
    return;
  }
  userEl.innerHTML = `
    <div class="card upload-card">
      <h2>Загрузить файлы</h2>
      <form id="up">
        <label class="file-picker">Выберите один или несколько файлов<input type="file" name="file" multiple></label>
        <div id="selected-files" class="file-list muted">Файлы не выбраны</div>
        <textarea name="caption" placeholder="Общий комментарий для всех файлов"></textarea>
        <button type="submit">Отправить</button>
      </form>
      <div id="upmsg" class="status-message"></div>
    </div>
    <div class="card"><h2>Мои заявки</h2>${chipHtml(USER_FILTERS, userStatusFilter, "user")}<div id="reqs"></div></div>
    <div class="card"><h2>Файлы</h2><div id="files"></div></div>`;
  const form = document.querySelector("#up");
  const input = form.querySelector('input[type="file"]');
  input.onchange = () => setSelectedUploadFiles(input.files);
  form.onsubmit = async (event) => uploadSelectedFiles(event, form, input);
  document.querySelectorAll("[data-user-filter]").forEach((button) => {
    button.onclick = () => { userStatusFilter = button.dataset.userFilter; renderUserRequests(); };
  });
  await loadUserLists();
}

function createUploadEntry(file) {
  return { file, idempotencyKey: crypto.randomUUID(), status: "pending", error: "" };
}

function setSelectedUploadFiles(files) {
  selectedUploadEntries = Array.from(files || []).map((file) => createUploadEntry(file));
  renderSelectedFiles();
}

function clearSelectedUploadFiles(form) {
  selectedUploadEntries = [];
  form.reset();
  renderSelectedFiles();
}

function renderSelectedFiles() {
  const list = document.querySelector("#selected-files");
  const remaining = selectedUploadEntries.filter((entry) => entry.status !== "done");
  if (remaining.length === 0) { list.textContent = "Файлы не выбраны"; return; }
  list.innerHTML = `<b>Файлы выбраны: ${remaining.length}</b>` + remaining.map((entry, index) => {
    const retryText = entry.status === "failed" ? ` <span class="muted">ожидает повторной отправки</span>` : "";
    return `<div class="file-row"><span>${index + 1}. ${escapeHtml(entry.file.name)}${retryText}</span><span>${fmtSize(entry.file.size)}</span></div>`;
  }).join("");
}

async function uploadSelectedFiles(event, form, input) {
  event.preventDefault();
  const msg = document.querySelector("#upmsg");
  if (uploadInProgress) { msg.textContent = "Загрузка уже выполняется."; return; }
  const entriesToUpload = selectedUploadEntries.filter((entry) => entry.status !== "done");
  const files = entriesToUpload.map((entry) => entry.file);
  if (entriesToUpload.length === 0) { msg.textContent = "Выберите хотя бы один файл."; return; }
  uploadInProgress = true;
  const submitButton = form.querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  const caption = form.querySelector("textarea").value;
  const results = [];
  try {
    for (const [index, entry] of entriesToUpload.entries()) {
      const file = entry.file;
      entry.status = "uploading";
      msg.textContent = `Загружается ${index + 1} из ${files.length}: ${file.name}`;
      const fd = new FormData();
      fd.append("file", file);
      fd.append("caption", caption);
      try {
        const result = await api("/api/uploads", { method: "POST", headers: { "Idempotency-Key": entry.idempotencyKey }, body: fd });
        entry.status = "done";
        entry.error = "";
        results.push(`✅ ${file.name}: создана заявка ${result.request_code}`);
      } catch (err) {
        entry.status = "failed";
        entry.error = safeErrorMessage(err);
        results.push(`❌ ${file.name}: ${entry.error}`);
      }
    }
    selectedUploadEntries = selectedUploadEntries.filter((entry) => entry.status !== "done");
    const failedCount = selectedUploadEntries.length;
    const title = failedCount === 0 ? "Готово" : `Готово. Осталось для повторной отправки: ${failedCount}`;
    msg.innerHTML = `<b>${escapeHtml(title)}</b>${results.map((item) => `<div>${escapeHtml(item)}</div>`).join("")}`;
    if (failedCount === 0) clearSelectedUploadFiles(form); else renderSelectedFiles();
    await loadUserLists();
  } finally {
    uploadInProgress = false;
    if (submitButton) submitButton.disabled = false;
  }
}

async function loadUserLists() {
  userUploads = await api("/api/uploads");
  renderUserRequests();
  const files = await api("/api/files");
  document.querySelector("#files").innerHTML = (files.items || []).map((f) =>
    `<div class="file-row"><span>${f.type === "dir" ? "📁" : "📄"} ${escapeHtml(f.name)}</span><span>${fmtSize(f.size)}</span></div>`
  ).join("") || `<div class="empty">${escapeHtml(files.message || "Файлов пока нет")}</div>`;
}

function renderUserRequests() {
  const visible = userStatusFilter === "all" ? userUploads : userUploads.filter((r) => r.status === userStatusFilter);
  document.querySelectorAll("[data-user-filter]").forEach((b) => b.classList.toggle("active", b.dataset.userFilter === userStatusFilter));
  document.querySelector("#reqs").innerHTML = visible.map((r) => `
    <div class="request-card">
      <div class="card-head"><b>${escapeHtml(r.request_code)}</b><span class="badge status-${escapeHtml(r.status)}">${escapeHtml(statusLabel(r.status))}</span></div>
      <div class="filename">${escapeHtml(r.safe_filename)}</div>
      <div class="muted">${escapeHtml(r.reject_reason || r.error_message || "")}</div>
    </div>`).join("") || '<div class="empty">Заявок с таким статусом пока нет.</div>';
}

async function loadAdmin(tab) {
  adminContent.innerHTML = '<div id="admin-error" class="muted"></div>';
  try {
    if (tab === "users") return await renderAdminUsers();
    if (tab === "renames") return await renderRenameRequests();
    if (tab === "audit") return await renderAudit();
    if (tab === "disk-root") return await renderDiskRootSettings();
    await renderAdminUploads();
  } catch (err) { showAdminError(safeErrorMessage(err)); }
}

async function renderAdminUploads() {
  const params = new URLSearchParams();
  if (adminStatusFilter !== "all" && adminStatusFilter !== "needs_action") params.set("status", adminStatusFilter);
  if (adminUserQuery.trim()) params.set("user_query", adminUserQuery.trim());
  let rows = await api(`/api/admin/uploads${params.toString() ? `?${params}` : ""}`);
  if (adminStatusFilter === "needs_action") rows = rows.filter((r) => NEEDS_ACTION_STATUSES.has(r.status));
  adminContent.innerHTML = `
    <div id="admin-error" class="muted"></div>
    <div class="admin-tools">${chipHtml(ADMIN_FILTERS, adminStatusFilter, "admin")}
      <div class="search-row"><input id="admin-search" value="${escapeHtml(adminUserQuery)}" placeholder="Поиск по Telegram ID, username или имени"><button id="clear-search" class="secondary">Очистить</button></div>
    </div>` + (rows.map(adminUploadCard).join("") || '<div class="card empty">Заявки не найдены.</div>');
  bindAdminUploadControls();
}

function adminUploadCard(r) {
  const user = r.user || {};
  const userTitle = `${user.telegram_id || "—"} · @${user.username || "—"} · ${user.full_name || "—"}`;
  return `<div class="card upload-admin-card" id="upload-${r.id}">
    <div class="card-head"><b>${escapeHtml(r.request_code)}</b><span class="badge status-${escapeHtml(r.status)}">${escapeHtml(statusLabel(r.status))}</span></div>
    <div class="filename">${escapeHtml(r.safe_filename)} <span class="muted">${fmtSize(r.size_bytes)}</span></div>
    <div class="meta">Пользователь: ${escapeHtml(userTitle)}</div>
    <div class="meta">SHA-256: ${escapeHtml(shortSha(r.sha256))}</div>
    <div class="path">${escapeHtml(r.target_path || "—")}</div>
    <div class="muted">${escapeHtml(r.caption || r.error_message || r.reject_reason || "")}</div>
    <div class="actions">${adminUploadActions(r)}</div>
  </div>`;
}

function adminUploadActions(r) {
  const open = `<div><b>Основные</b><button data-download-id="${r.id}" data-download-name="${escapeHtml(r.safe_filename || "file")}">Открыть файл</button></div>`;
  if (QUEUED_STATUSES.has(r.status)) return `${open}<div class="muted">Заявка поставлена в очередь и будет обработана worker.</div>`;
  const canSubmit = r.status === "pending_approval" || r.status === "failed";
  const approve = canSubmit && r.status === "pending_approval" ? `<button onclick="uploadAction(${r.id}, 'approve')">${uploadActionLabel("approve")}</button>` : "";
  const conflict = canSubmit ? ["copy", "overwrite"].concat(r.status === "failed" ? ["retry"] : []).map((a) => `<button onclick="uploadAction(${r.id}, '${a}')">${uploadActionLabel(a)}</button>`).join("") : "";
  const edit = r.status === "pending_approval" || r.status === "failed" ? `<div><b>Редактирование</b><button onclick="changeStem(${r.id})">Изменить имя</button><button onclick="changeExtension(${r.id})">Изменить расширение</button><button onclick="changeFolder(${r.id})">Сменить папку этой заявки</button></div>` : "";
  const reject = ["pending_approval", "failed"].includes(r.status) ? `<div><b>Опасное</b><button class="danger" onclick="rejectUpload(${r.id})">Отклонить</button></div>` : "";
  return `${open}<div><b>Загрузка</b>${approve}${conflict}</div>${edit}${reject}`;
}

function bindAdminUploadControls() {
  document.querySelectorAll("[data-admin-filter]").forEach((button) => {
    button.onclick = () => { adminStatusFilter = button.dataset.adminFilter; renderAdminUploads(); };
  });
  document.querySelectorAll("[data-download-id]").forEach((button) => {
    button.onclick = () => downloadTemp(Number.parseInt(button.dataset.downloadId, 10), button.dataset.downloadName || "file");
  });
  const search = document.querySelector("#admin-search");
  search.oninput = () => { clearTimeout(adminSearchTimer); adminSearchTimer = setTimeout(() => { adminUserQuery = search.value; renderAdminUploads(); }, 300); };
  document.querySelector("#clear-search").onclick = () => { adminUserQuery = ""; renderAdminUploads(); };
}

async function renderAdminUsers() {
  const rows = await api("/api/admin/users");
  adminContent.innerHTML = '<div id="admin-error" class="muted"></div>' + rows.map((u) => `
    <div class="card user-card"><div class="card-head"><b>${escapeHtml(u.full_name || "—")}</b><span class="badge">${escapeHtml(statusLabel(u.status))}</span></div>
      <div class="meta">@${escapeHtml(u.username || "—")} · ID Telegram: ${escapeHtml(u.telegram_id)}</div>
      <div class="meta">Папка на Яндекс.Диске: ${escapeHtml(userFolderLabel(u))}</div>
      <div class="row">${u.status === "pending" ? ["approve", "reject", "block"].map((a) => `<button onclick="moderateUser(${u.id}, '${a}')">${userActionLabel(a)}</button>`).join("") : ""}</div>
    </div>`).join("") || '<div class="card empty">Пользователей пока нет.</div>';
}


async function renderDiskRootSettings() {
  const current = await api("/api/admin/disk-root");
  const source = current.source === "env" ? ".env" : "задано администратором";
  adminContent.innerHTML = `
    <div id="admin-error" class="muted"></div>
    <div class="card">
      <h3>Корневая папка</h3>
      <div class="meta">Текущая корневая папка: <b>${escapeHtml(current.value)}</b></div>
      <div class="meta">Источник: ${escapeHtml(source)}</div>
      <p class="muted">Это общая папка, внутри которой создаются папки пользователей.<br>
      После изменения новые загрузки всех пользователей будут идти в папки внутри новой корневой папки.<br>
      Если папки пользователя там ещё нет, она будет создана повторно.<br>
      Старые файлы не переносятся.</p>
      <label>Новая корневая папка<input id="disk-root-input" value="${escapeHtml(current.value)}" placeholder="disk:/Telegram Uploads"></label>
      <button id="save-disk-root">Сохранить корневую папку</button>
      <div id="disk-root-message" class="status-message"></div>
    </div>`;
  document.querySelector("#save-disk-root").onclick = async () => {
    const msg = document.querySelector("#disk-root-message");
    try {
      const root = document.querySelector("#disk-root-input").value;
      await api("/api/admin/disk-root", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ root }) });
      msg.textContent = "Корневая папка сохранена.";
      await renderDiskRootSettings();
    } catch (err) { showAdminError(safeErrorMessage(err)); }
  };
}

async function renderRenameRequests() {
  const requests = await api("/api/admin/folder-rename-requests?status=pending");
  adminContent.innerHTML = `<div id="admin-error" class="muted"></div>
    <div class="card"><h3>Переименовать папку</h3>
      <div class="search-box"><input id="rename-user-search" placeholder="Поиск пользователя по Telegram ID, username, ФИО, договору или папке"><div id="rename-user-results" class="dropdown"></div></div>
      <div id="rename-user-card" class="muted">Выберите пользователя из выпадающего списка.</div>
      <select id="rename-source"><option value="">Сначала выберите пользователя</option></select>
      <input id="rename-new-name" placeholder="12345 от 09.07.2026 Иванов Иван Иванович">
      <button id="rename-user-button">Переименовать папку</button>
    </div>
    <div class="card"><h3>Заявки на переименование</h3><div id="rename-requests"></div></div>`;
  renameSelectionVersion += 1;
  bindRenameUserSearch();
  document.querySelector("#rename-user-button").onclick = renameSelectedUserFolder;
  document.querySelector("#rename-requests").innerHTML = (requests.items || []).map((r) => `<div class="request-card"><b>${escapeHtml(r.requested_folder_name)}</b><div class="meta">${escapeHtml(r.user?.telegram_id || "—")} · ${escapeHtml(r.contract_full_name || "—")}</div><button onclick="approveRenameRequest(${r.id}, ${r.user_id})">Одобрить</button><button class="danger" onclick="rejectRenameRequest(${r.id})">Отклонить</button></div>`).join("") || '<div class="empty">Нет pending-заявок.</div>';
}
function bindRenameUserSearch() {
  const input = document.querySelector("#rename-user-search");
  input.oninput = () => { clearTimeout(adminSearchTimer); adminSearchTimer = setTimeout(async () => {
    const q = input.value.trim(); const box = document.querySelector("#rename-user-results");
    if (!q) { box.innerHTML = ""; return; }
    let data;
    try { data = await api(`/api/admin/users/search?query=${encodeURIComponent(q)}`); } catch (err) { showAdminError(safeErrorMessage(err)); return; }
    box.innerHTML = (data.items || []).map((u) => `<button class="dropdown-item" data-user-id="${u.id}">${escapeHtml(u.telegram_id)} · ${escapeHtml(u.contract_full_name || u.full_name || "—")}<br><span class="muted">${escapeHtml(u.folder_name || "—")}</span></button>`).join("");
    box.querySelectorAll("[data-user-id]").forEach((b, i) => b.onclick = () => selectRenameUser(data.items[i]));
  }, 300); };
}
function setRenameControlsEnabled(enabled) {
  const source = document.querySelector("#rename-source");
  const button = document.querySelector("#rename-user-button");
  if (source) source.disabled = !enabled;
  if (button) button.disabled = !enabled;
}
function resetRenameSelection(message = "Сначала выберите пользователя") {
  selectedRenameUser = null;
  renameFolderCandidates = [];
  const source = document.querySelector("#rename-source");
  if (source) source.innerHTML = `<option value="">${escapeHtml(message)}</option>`;
}
function selectedRenameSourceFolder() {
  const source = document.querySelector("#rename-source");
  const value = source?.value || "";
  if (!selectedRenameUser || !value || !renameFolderCandidates.some((c) => c.path === value)) return null;
  return value;
}
function isCurrentRenameSelection(selectionVersion) {
  return selectionVersion === renameSelectionVersion;
}
async function selectRenameUser(user, { showErrors = true } = {}) {
  const selectionVersion = ++renameSelectionVersion;
  resetRenameSelection("Загрузка папок пользователя…");
  setRenameControlsEnabled(false);
  const card = document.querySelector("#rename-user-card");
  if (card) card.textContent = "Загрузка папок пользователя…";
  try {
    const candidates = await api(`/api/admin/users/${user.id}/folder-candidates`);
    if (!isCurrentRenameSelection(selectionVersion)) return false;
    const items = candidates.items || [];
    if (items.length === 0) {
      resetRenameSelection("Нет доступных папок для переименования");
      if (card) card.innerHTML = `<b>${escapeHtml(user.contract_full_name || user.full_name || "—")}</b><div class="meta">Нет доступных папок для переименования.</div>`;
      throw new ApiClientError({ status: 400, code: "folder_candidates_empty", message: "У пользователя нет доступных папок для переименования." });
    }
    selectedRenameUser = user;
    renameFolderCandidates = items;
    if (card) card.innerHTML = `<b>${escapeHtml(user.contract_full_name || user.full_name || "—")}</b><div class="meta">${escapeHtml(user.telegram_id || "—")} · ${escapeHtml(user.folder_name || "—")}</div>`;
    document.querySelector("#rename-source").innerHTML = items.map((c) => `<option value="${escapeHtml(c.path)}">${escapeHtml(c.label)} — ${escapeHtml(c.path)}</option>`).join("");
    setRenameControlsEnabled(true);
    return true;
  } catch (err) {
    if (!isCurrentRenameSelection(selectionVersion)) return false;
    resetRenameSelection("Не удалось загрузить папки пользователя");
    if (card) card.textContent = "Выберите пользователя из выпадающего списка.";
    setRenameControlsEnabled(false);
    if (showErrors) showAdminError(safeErrorMessage(err));
    throw err;
  }
}
async function renameSelectedUserFolder() {
  try {
    if (!selectedRenameUser) return showAdminError("Выберите пользователя");
    const sourceFolder = selectedRenameSourceFolder();
    if (!sourceFolder) return showAdminError("Выберите актуальную папку пользователя из списка.");
    await api(`/api/admin/users/${selectedRenameUser.id}/rename-folder`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_folder: sourceFolder, new_folder_name: document.querySelector("#rename-new-name").value }) });
    await renderRenameRequests();
  } catch (err) { showAdminError(safeErrorMessage(err)); }
}
async function approveRenameRequest(id, userId) { try { const selected = await selectRenameUser({ id: userId }, { showErrors: false }); if (!selected) return; const source_folder = selectedRenameSourceFolder(); if (!source_folder) return showAdminError("Выберите актуальную папку пользователя из списка."); await api(`/api/admin/folder-rename-requests/${id}/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_folder }) }); await renderRenameRequests(); } catch (err) { showAdminError(safeErrorMessage(err)); } }
async function rejectRenameRequest(id) { try { const reason = prompt("Причина", "Отклонено администратором") || "Отклонено администратором"; await api(`/api/admin/folder-rename-requests/${id}/reject`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }) }); await renderRenameRequests(); } catch (err) { showAdminError(safeErrorMessage(err)); } }

async function renderAudit() {
  const rows = await api("/api/admin/audit");
  adminContent.innerHTML = '<div id="admin-error" class="muted"></div>' + rows.map((a) => `
    <div class="card audit-card"><b>${escapeHtml(auditLabel(a.action))}</b><br>
      <span class="meta">Администратор: ${escapeHtml(a.actor_telegram_id)}; заявка: ${escapeHtml(a.request_id || "—")}</span>
      <pre>${escapeHtml(JSON.stringify(a.new_value, null, 2))}</pre>
    </div>`).join("") || '<div class="card empty">Аудит пока пуст.</div>';
}

async function downloadTemp(id, filename) {
  try {
    const response = await fetch(`/api/admin/uploads/${id}/download-temp`, { headers: { "X-Telegram-Init-Data": initData } });
    if (!response.ok) throw await readError(response);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename || "file"; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  } catch (err) { showAdminError(safeErrorMessage(err)); }
}

async function moderateUser(id, action) { try { await api(`/api/admin/users/${id}/${action}`, { method: "POST" }); await loadAdmin("users"); } catch (err) { showAdminError(safeErrorMessage(err)); } }
async function uploadAction(id, action) { try { await api(`/api/admin/uploads/${id}/${action}`, { method: "POST" }); showAdminError("Заявка поставлена в очередь"); await loadAdmin("uploads"); } catch (err) { showAdminError(safeErrorMessage(err)); } }
async function rejectUpload(id) { try { const reason = prompt("Причина", "Отклонено администратором"); if (reason) await api(`/api/admin/uploads/${id}/reject`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }) }); await loadAdmin("uploads"); } catch (err) { showAdminError(safeErrorMessage(err)); } }
async function changeStem(id) { try { const filenameStem = prompt("Введите новое имя файла без расширения. Текущее расширение будет сохранено."); if (!filenameStem) return; await api(`/api/admin/uploads/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename_stem: filenameStem }) }); await loadAdmin("uploads"); } catch (err) { showAdminError(safeErrorMessage(err)); } }
async function changeExtension(id) { try { const filenameExtension = prompt("Введите новое расширение файла, например: pdf или .pdf"); if (!filenameExtension) return; await api(`/api/admin/uploads/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename_extension: filenameExtension }) }); await loadAdmin("uploads"); } catch (err) { showAdminError(safeErrorMessage(err)); } }
async function changeFolder(id) { try { const folders = await api(`/api/admin/uploads/${id}/allowed-folders`); const choices = folders.items.map((f, index) => `${index + 1}. ${f.label}`).join("\n"); const selected = prompt(`Выберите новую папку только для этой заявки:\n${choices}\n\nОбщая корневая папка меняется во вкладке «Корневая папка».`); if (!selected) return; const index = Number.parseInt(selected, 10) - 1; if (!folders.items[index]) throw new Error("Выберите номер папки из списка"); await api(`/api/admin/uploads/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target_folder: folders.items[index].path }) }); await loadAdmin("uploads"); } catch (err) { showAdminError(safeErrorMessage(err)); } }

async function load() {
  try {
    const me = await api("/api/me");
    const title = escapeHtml(me.full_name || me.username || me.telegram_id);
    auth.innerHTML = `<div class="card-head"><b>${title}</b><span class="badge">${escapeHtml(statusLabel(me.status))}</span></div><div class="muted">Папка на Яндекс.Диске: ${escapeHtml(userFolderLabel(me))}</div>`;
    await renderUser(me);
    if (me.is_admin) { adminEl.classList.remove("hidden"); await loadAdmin("uploads"); }
  } catch (err) { auth.textContent = safeErrorMessage(err); }
}

document.querySelectorAll("nav button").forEach((button) => { button.onclick = () => loadAdmin(button.dataset.tab); });
load();
