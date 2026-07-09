const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const initData = tg?.initData || "";
const auth = document.querySelector("#auth");
const userEl = document.querySelector("#user");
const adminEl = document.querySelector("#admin");
const adminContent = document.querySelector("#admin-content");

async function readError(response) {
  try {
    const data = await response.json();
    return data.detail || JSON.stringify(data);
  } catch {
    return await response.text();
  }
}

async function api(path, opts = {}) {
  opts.headers = { ...(opts.headers || {}), "X-Telegram-Init-Data": initData };
  const response = await fetch(path, opts);
  if (response.status === 401) {
    throw new Error("Откройте приложение через Telegram");
  }
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}


const STATUS_LABELS = { pending: "ожидает одобрения", active: "активен", rejected: "отклонён", blocked: "заблокирован", stored: "сохранён временно", new: "новый", pending_approval: "ожидает проверки", approved: "одобрено", uploading: "загружается", uploaded: "загружено", failed: "ошибка загрузки", cancelled: "отменено", deleted_temp: "временный файл удалён" };
const ACTION_LABELS = { approve: "Загрузить", copy: "Загрузить как копию", overwrite: "Перезаписать", retry: "Повторить", reject: "Отклонить", block: "Заблокировать" };
const AUDIT_LABELS = { upload_filename_stem_change: "изменение имени файла", upload_filename_extension_change: "изменение расширения файла", upload_patch: "изменение заявки", upload_folder_change: "изменение папки" };
function statusLabel(value) { return STATUS_LABELS[value] || value || "—"; }
function actionLabel(value) { return ACTION_LABELS[value] || value; }
function auditLabel(value) { return AUDIT_LABELS[value] || value; }

function fmtSize(n) {
  return n ? `${(n / 1048576).toFixed(2)} MB` : "—";
}

function showAdminError(message) {
  const error = document.querySelector("#admin-error");
  if (error) {
    error.textContent = message;
  } else {
    alert(message);
  }
}

async function renderUser(me) {
  if (me.status === "pending") {
    userEl.innerHTML = '<div class="card">Доступ ожидает одобрения администратора.</div>';
    return;
  }
  if (["blocked", "rejected"].includes(me.status)) {
    userEl.innerHTML = '<div class="card">Загрузка файлов для аккаунта недоступна.</div>';
    return;
  }
  userEl.innerHTML = `
    <div class="card">
      <h2>Загрузить файл</h2>
      <form id="up">
        <input type="file" name="file" required>
        <textarea name="caption" placeholder="Комментарий"></textarea>
        <button>Отправить</button>
      </form>
      <div id="upmsg"></div>
    </div>
    <div class="card"><h2>Мои заявки</h2><div id="reqs"></div></div>
    <div class="card"><h2>Файлы</h2><div id="files"></div></div>`;
  document.querySelector("#up").onsubmit = async (event) => {
    event.preventDefault();
    const fd = new FormData(event.target);
    document.querySelector("#upmsg").textContent = "Файл загружается...";
    try {
      const result = await api("/api/uploads", { method: "POST", body: fd });
      document.querySelector("#upmsg").textContent = `Создана заявка ${result.request_code}`;
      await loadUserLists();
    } catch (err) {
      document.querySelector("#upmsg").textContent = err.message;
    }
  };
  await loadUserLists();
}

async function loadUserLists() {
  const reqs = await api("/api/uploads");
  document.querySelector("#reqs").innerHTML = reqs.map((r) => `
    <div class="card">
      <b>${r.request_code}</b> ${statusLabel(r.status)}<br>
      ${r.safe_filename}<br>
      <span class="muted">${r.reject_reason || r.error_message || ""}</span>
    </div>`).join("") || "Нет заявок";

  const files = await api("/api/files");
  document.querySelector("#files").innerHTML = (files.items || []).map((f) =>
    `<div>${f.type === "dir" ? "📁" : "📄"} ${f.name} ${fmtSize(f.size)}</div>`
  ).join("") || (files.message || "Пусто");
}

async function loadAdmin(tab) {
  adminContent.innerHTML = '<div id="admin-error" class="muted"></div>';
  try {
    if (tab === "users") {
      await renderAdminUsers();
      return;
    }
    if (tab === "audit") {
      await renderAudit();
      return;
    }
    await renderAdminUploads();
  } catch (err) {
    showAdminError(err.message);
  }
}

async function renderAdminUploads() {
  const rows = await api("/api/admin/uploads");
  adminContent.innerHTML = '<div id="admin-error" class="muted"></div>' + rows.map((r) => `
    <div class="card" id="upload-${r.id}">
      <b>${r.request_code}</b> ${statusLabel(r.status)}<br>
      ${r.safe_filename} (${fmtSize(r.size_bytes)})<br>
      Пользователь: ${r.user?.telegram_id || "—"}<br>
      SHA-256: ${(r.sha256 || "").slice(0, 12)}<br>
      <span class="small">${r.target_path}</span><br>
      <span class="muted">${r.caption || r.error_message || r.reject_reason || ""}</span>
      <div class="row">
        <button onclick="downloadTemp(${r.id}, decodeURIComponent('${encodeURIComponent(r.safe_filename)}'))">Открыть файл</button>
        ${["approve", "copy", "overwrite", "retry"].map((a) =>
          `<button onclick="uploadAction(${r.id}, '${a}')">${actionLabel(a)}</button>`
        ).join("")}
        <button class="danger" onclick="rejectUpload(${r.id})">Отклонить</button>
        <button onclick="changeStem(${r.id})">Изменить имя</button>
        <button onclick="changeExtension(${r.id})">Изменить расширение</button>
        <button onclick="changeFolder(${r.id})">Сменить папку</button>
      </div>
    </div>`).join("");
}

async function renderAdminUsers() {
  const rows = await api("/api/admin/users");
  adminContent.innerHTML = '<div id="admin-error" class="muted"></div>' + rows.map((u) => `
    <div class="card">
      <b>${u.full_name || "—"}</b> @${u.username || "—"}<br>
      ID Telegram: ${u.telegram_id}; ${statusLabel(u.status)}
      <div class="row">
        ${u.status === "pending" ? ["approve", "reject", "block"].map((a) =>
          `<button onclick="moderateUser(${u.id}, '${a}')">${actionLabel(a)}</button>`
        ).join("") : ""}
      </div>
    </div>`).join("");
}

async function renderAudit() {
  const rows = await api("/api/admin/audit");
  adminContent.innerHTML = '<div id="admin-error" class="muted"></div>' + rows.map((a) => `
    <div class="card">
      <b>${auditLabel(a.action)}</b><br>
      Администратор: ${a.actor_telegram_id}; заявка: ${a.request_id || "—"}<br>
      <span class="small">${JSON.stringify(a.new_value)}</span>
    </div>`).join("");
}

async function downloadTemp(id, filename) {
  try {
    const response = await fetch(`/api/admin/uploads/${id}/download-temp`, {
      headers: { "X-Telegram-Init-Data": initData },
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "file";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showAdminError(err.message);
  }
}

async function moderateUser(id, action) {
  await api(`/api/admin/users/${id}/${action}`, { method: "POST" });
  await loadAdmin("users");
}

async function uploadAction(id, action) {
  await api(`/api/admin/uploads/${id}/${action}`, { method: "POST" });
  await loadAdmin("uploads");
}

async function rejectUpload(id) {
  const reason = prompt("Причина", "Отклонено администратором");
  if (reason) {
    await api(`/api/admin/uploads/${id}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
  }
  await loadAdmin("uploads");
}

async function changeStem(id) {
  try {
    const filenameStem = prompt("Введите новое имя файла без расширения. Текущее расширение будет сохранено.");
    if (!filenameStem) return;
    await api(`/api/admin/uploads/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename_stem: filenameStem }),
    });
    await loadAdmin("uploads");
  } catch (err) {
    showAdminError(err.message);
  }
}

async function changeExtension(id) {
  try {
    const filenameExtension = prompt("Введите новое расширение файла, например: pdf или .pdf");
    if (!filenameExtension) return;
    await api(`/api/admin/uploads/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename_extension: filenameExtension }),
    });
    await loadAdmin("uploads");
  } catch (err) {
    showAdminError(err.message);
  }
}

async function changeFolder(id) {
  try {
    const folders = await api(`/api/admin/uploads/${id}/allowed-folders`);
    const choices = folders.items.map((f, index) => `${index + 1}. ${f.label}`).join("\n");
    const selected = prompt(`Выберите новую папку по номеру:\n${choices}`);
    if (!selected) return;
    const index = Number.parseInt(selected, 10) - 1;
    if (!folders.items[index]) throw new Error("Выберите номер папки из списка");
    await api(`/api/admin/uploads/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_folder: folders.items[index].path }),
    });
    await loadAdmin("uploads");
  } catch (err) {
    showAdminError(err.message);
  }
}

async function load() {
  try {
    const me = await api("/api/me");
    auth.innerHTML = `<b>${me.full_name || me.username || me.telegram_id}</b><br>Статус: ${statusLabel(me.status)}<br>Папка на Яндекс.Диске: ${me.root_folder_assigned ? "назначена" : "не назначена"}`;
    await renderUser(me);
    if (me.is_admin) {
      adminEl.classList.remove("hidden");
      await loadAdmin("uploads");
    }
  } catch (err) {
    auth.textContent = err.message;
  }
}

document.querySelectorAll("nav button").forEach((button) => {
  button.onclick = () => loadAdmin(button.dataset.tab);
});

load();
