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
    document.querySelector("#upmsg").textContent = "Загрузка...";
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
      <b>${r.request_code}</b> ${r.status}<br>
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
      <b>${r.request_code}</b> ${r.status}<br>
      ${r.safe_filename} (${fmtSize(r.size_bytes)})<br>
      user: ${r.user?.telegram_id || "—"}<br>
      sha: ${(r.sha256 || "").slice(0, 12)}<br>
      <span class="small">${r.target_path}</span><br>
      <span class="muted">${r.caption || r.error_message || r.reject_reason || ""}</span>
      <div class="row">
        <button onclick="downloadTemp(${r.id}, decodeURIComponent('${encodeURIComponent(r.safe_filename)}'))">temp</button>
        ${["approve", "copy", "overwrite", "retry"].map((a) =>
          `<button onclick="uploadAction(${r.id}, '${a}')">${a}</button>`
        ).join("")}
        <button class="danger" onclick="rejectUpload(${r.id})">reject</button>
        <button onclick="patchUpload(${r.id})">edit</button>
      </div>
    </div>`).join("");
}

async function renderAdminUsers() {
  const rows = await api("/api/admin/users");
  adminContent.innerHTML = '<div id="admin-error" class="muted"></div>' + rows.map((u) => `
    <div class="card">
      <b>${u.full_name || "—"}</b> @${u.username || "—"}<br>
      ID ${u.telegram_id}; ${u.status}
      <div class="row">
        ${u.status === "pending" ? ["approve", "reject", "block"].map((a) =>
          `<button onclick="moderateUser(${u.id}, '${a}')">${a}</button>`
        ).join("") : ""}
      </div>
    </div>`).join("");
}

async function renderAudit() {
  const rows = await api("/api/admin/audit");
  adminContent.innerHTML = '<div id="admin-error" class="muted"></div>' + rows.map((a) => `
    <div class="card">
      <b>${a.action}</b><br>
      actor=${a.actor_telegram_id}; request=${a.request_id || "—"}<br>
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

async function patchUpload(id) {
  try {
    const safeFilename = prompt("Новое имя файла (пусто — не менять)");
    const folders = await api(`/api/admin/uploads/${id}/allowed-folders`);
    let targetFolder = null;
    if (folders.items.length) {
      const choices = folders.items.map((f, index) => `${index + 1}. ${f.label}`).join("\n");
      const selected = prompt(`Новая папка (пусто — не менять):\n${choices}`);
      if (selected) {
        const index = Number.parseInt(selected, 10) - 1;
        if (!folders.items[index]) {
          throw new Error("Выберите номер папки из списка");
        }
        targetFolder = folders.items[index].path;
      }
    }
    await api(`/api/admin/uploads/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ safe_filename: safeFilename || null, target_folder: targetFolder }),
    });
    await loadAdmin("uploads");
  } catch (err) {
    showAdminError(err.message);
  }
}

async function load() {
  try {
    const me = await api("/api/me");
    auth.innerHTML = `<b>${me.full_name || me.username || me.telegram_id}</b><br>Статус: ${me.status}<br>Папка: ${me.root_folder_assigned ? "назначена" : "не назначена"}`;
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
