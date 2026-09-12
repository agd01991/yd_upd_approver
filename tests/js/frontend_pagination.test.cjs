const assert = require("node:assert/strict");

global.__PAGINATION_TEST__ = true;
global.window = { Telegram: null };

const staticElements = new Map();
let dynamicElements = [];
function dataName(name) { return name.replace(/-([a-z])/g, (_, c) => c.toUpperCase()); }
class TestElement {
  constructor({ id = "", attrs = {}, owner = null } = {}) {
    this.id = id; this.owner = owner; this.dataset = {}; this.disabled = false;
    this.value = attrs.value || ""; this.textContent = ""; this.onclick = null; this.oninput = null;
    this.classList = { add() {}, remove() {}, toggle() {} };
    Object.entries(attrs).forEach(([key, value]) => {
      if (key === "disabled") this.disabled = true;
      if (key.startsWith("data-")) this.dataset[dataName(key.slice(5))] = value;
    });
    this._innerHTML = "";
  }
  set innerHTML(html) {
    this._innerHTML = String(html);
    if (this.id === "admin-content") {
      dynamicElements = [];
      const tagPattern = /<(input|button|div|select|textarea)([^>]*)>/g;
      for (const match of this._innerHTML.matchAll(tagPattern)) {
        const attrs = {};
        for (const attr of match[2].matchAll(/([\w-]+)(?:=["']([^"']*)["'])?/g)) attrs[attr[1]] = attr[2] ?? "";
        if (!attrs.id && !Object.keys(attrs).some((key) => key.startsWith("data-"))) continue;
        const node = new TestElement({ id: attrs.id, attrs, owner: this });
        const close = new RegExp(`<${match[1]}[^>]*${attrs.id ? `id=["']${attrs.id}["']` : ""}[^>]*>([^<]*)`);
        node.textContent = this._innerHTML.match(close)?.[1] || "";
        dynamicElements.push(node);
      }
    }
  }
  get innerHTML() { return this._innerHTML; }
  querySelector(selector) { return query(selector); }
  querySelectorAll(selector) { return queryAll(selector); }
  appendChild() {} remove() {}
  click() { if (!this.disabled && this.onclick) return this.onclick(); }
}
function matches(node, selector) {
  if (selector.startsWith("#")) return node.id === selector.slice(1);
  const attribute = selector.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
  if (!attribute) return false;
  const key = attribute[1].startsWith("data-") ? dataName(attribute[1].slice(5)) : attribute[1];
  const value = attribute[1].startsWith("data-") ? node.dataset[key] : node[key];
  return value !== undefined && (attribute[2] === undefined || value === attribute[2]);
}
function queryAll(selector) { return [...dynamicElements, ...staticElements.values()].filter((node) => matches(node, selector)); }
function query(selector) { return queryAll(selector)[0] || null; }
for (const id of ["auth", "user", "admin", "admin-content", "up", "selected-files", "upmsg", "files", "reqs"]) {
  staticElements.set(`#${id}`, new TestElement({ id }));
}
global.document = {
  querySelector: query,
  querySelectorAll: queryAll,
  createElement: () => new TestElement(),
  body: new TestElement(),
};
global.alert = () => {};

const pagination = require("../../app/webapp/static/app.js");
const pending = [];
global.fetch = (url, opts = {}) => new Promise((resolve, reject) => pending.push({ url: String(url), opts, resolve, reject }));
const response = (items, next = null) => ({
  ok: true, status: 200,
  text: async () => JSON.stringify({ items, has_more: next !== null, next_cursor: next }),
});

async function independentPagersAndReset() {
  pagination.resetPager("userUploads");
  pagination.resetPager("adminUploads");
  const user = pagination.guardedPage("userUploads", "/user");
  const admin = pagination.guardedPage("adminUploads", "/admin");
  pending[1].resolve(response(["admin"]));
  assert.deepEqual(await admin, ["admin"]);
  pending[0].resolve(response(["user"]));
  assert.deepEqual(await user, ["user"]);

  const stale = pagination.guardedPage("userUploads", "/stale");
  const old = pagination.pager("userUploads");
  pagination.resetPager("userUploads");
  const fresh = pagination.guardedPage("userUploads", "/fresh");
  pending[3].resolve(response(["fresh"]));
  assert.deepEqual(await fresh, ["fresh"]);
  pending[2].resolve(response(["stale"]));
  assert.equal(await stale, null);
  assert.notEqual(old.generation, pagination.pager("userUploads").generation);
  assert.equal(pagination.pager("userUploads").loading, false);
}

async function atomicNavigationFailureRetryAndDoubleClick() {
  pagination.resetPager("audit");
  await startAndResolve("audit", null, ["one"], "cursor-2");
  const p = pagination.pager("audit");
  const proposed = { cursor: "cursor-2", previous: [null], page: 2 };
  const failed = pagination.guardedPage("audit", "/page-2", proposed);
  pending.at(-1).reject(new Error("network"));
  await assert.rejects(failed);
  assert.deepEqual({ cursor: p.cursor, previous: p.previous, page: p.page }, { cursor: null, previous: [], page: 1 });

  const retry = pagination.guardedPage("audit", "/page-2-retry", proposed);
  const duplicate = pagination.guardedPage("audit", "/duplicate", proposed);
  assert.equal(await duplicate, null);
  pending.at(-1).resolve(response(["two"], "cursor-3"));
  assert.deepEqual(await retry, ["two"]);
  assert.deepEqual({ cursor: p.cursor, previous: p.previous, page: p.page }, proposed);

  const back = { cursor: null, previous: [], page: 1 };
  const backRequest = pagination.guardedPage("audit", "/back", back);
  pending.at(-1).resolve(response(["one"], "cursor-2"));
  assert.deepEqual(await backRequest, ["one"]);
  assert.equal(p.page, 1);
}

async function startAndResolve(name, navigation, items, next) {
  const promise = pagination.guardedPage(name, `/${name}`, navigation);
  pending.at(-1).resolve(response(items, next));
  return promise;
}

async function adminViewGeneration() {
  const uploads = pagination.setAdminView("uploads");
  const disk = pagination.setAdminView("disk-root");
  assert.equal(pagination.isAdminView(uploads), false);
  assert.equal(pagination.isAdminView(disk), true);
}

async function reopenedAdminViewSupersedesPendingWork() {
  pagination.resetPager("adminUploads");
  const first = pagination.loadAdmin("uploads");
  const firstRequest = pending.at(-1);
  const second = pagination.loadAdmin("uploads");
  const secondRequest = pending.at(-1);
  assert.notEqual(firstRequest, secondRequest, "reopening must start a fresh request");
  secondRequest.resolve(response([{ id: 2, request_code: "new", status: "uploaded" }], "next"));
  await second;
  assert.match(document.querySelector("#admin-content").innerHTML, /new/);
  firstRequest.resolve(response([{ id: 1, request_code: "stale", status: "uploaded" }]));
  await first;
  assert.doesNotMatch(document.querySelector("#admin-content").innerHTML, /stale/);
  assert.equal(pagination.pager("adminUploads").loading, false);
  assert.equal(pagination.pager("adminUploads").cursor, null);

  const oldNavigation = pagination.guardedPage(
    "adminUploads", "/old-next", { cursor: "old", previous: [null], page: 2 }, () => false,
  );
  pending.at(-1).reject(new Error("stale failure"));
  await assert.rejects(oldNavigation);
  assert.equal(pagination.pager("adminUploads").page, 1, "cancelled navigation is not committed");

  pagination.resetPager("userUploads");
  const user = pagination.guardedPage("userUploads", "/parallel-user");
  pagination.loadAdmin("users");
  const usersRequest = pending.at(-1);
  pending.at(-2).resolve(response(["user-row"]));
  assert.deepEqual(await user, ["user-row"], "admin views do not invalidate user pagination");
  usersRequest.resolve(response([]));
  await Promise.resolve();
}

async function diskRootSaveSurvivesViewReplacement() {
  const opened = pagination.loadAdmin("disk-root");
  pending.at(-1).resolve({ ...response([]), text: async () => JSON.stringify({ value: "old", source: "env" }) });
  await opened;
  const input = document.querySelector("#disk-root-input");
  input.value = "new";
  const saving = document.querySelector("#save-disk-root").onclick();
  const putRequest = pending.at(-1);
  assert.equal(input.disabled, true);
  // A second click shares the one active save instead of issuing another PUT.
  assert.equal(document.querySelector("#save-disk-root").onclick(), saving);

  const other = pagination.loadAdmin("users");
  pending.at(-1).resolve(response([]));
  await other;
  const otherHtml = document.querySelector("#admin-content").innerHTML;
  putRequest.resolve({ ...response([]), text: async () => JSON.stringify({ value: "new" }) });
  await saving;
  assert.equal(document.querySelector("#admin-content").innerHTML, otherHtml, "PUT must not repaint another tab");

  const reopened = pagination.loadAdmin("disk-root");
  pending.at(-1).resolve({ ...response([]), text: async () => JSON.stringify({ value: "new", source: "database" }) });
  await reopened;
  assert.match(document.querySelector("#disk-root-current").innerHTML, /new/);
  assert.match(document.querySelector("#disk-root-message").textContent || document.querySelector("#admin-content").innerHTML, /сохранена/);
}

const diskRootResponse = (value, source = "database") => ({
  ...response([]), text: async () => JSON.stringify({ value, source }),
});
const requestMethod = (request) => request.opts.method || "GET";
async function drainMicrotasks() { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }

async function diskRootReadinessAndRetry() {
  const requestCount = pending.length;
  const loading = pagination.loadAdmin("disk-root");
  const firstGet = pending.at(-1);
  const input = document.querySelector("#disk-root-input");
  const save = document.querySelector("#save-disk-root");
  assert.equal(input.disabled, true);
  assert.equal(save.disabled, true);
  save.click();
  assert.equal(save.onclick(), null, "the handler itself must reject a premature save");
  assert.equal(pending.length, requestCount + 1);

  firstGet.reject(new Error("GET failed"));
  await loading;
  assert.equal(save.disabled, true);
  assert.match(document.querySelector("#admin-error").textContent, /Не удалось загрузить/);
  const retry = document.querySelector("#retry-disk-root");
  assert.equal(retry.disabled, false);
  const retried = retry.click();
  assert.equal(requestMethod(pending.at(-1)), "GET");
  pending.at(-1).resolve(diskRootResponse("disk:/loaded"));
  await retried;
  assert.equal(document.querySelector("#disk-root-input").value, "disk:/loaded");
  assert.equal(document.querySelector("#disk-root-input").disabled, false);
  assert.equal(document.querySelector("#save-disk-root").disabled, false);
}

async function diskRootSaveLockDoesNotWaitForRefresh() {
  const input = document.querySelector("#disk-root-input");
  const save = document.querySelector("#save-disk-root");
  input.value = "   "; input.oninput();
  assert.equal(save.disabled, true);
  const beforeBlankSave = pending.length;
  assert.equal(save.onclick(), null);
  assert.equal(pending.length, beforeBlankSave);

  input.value = " disk:/new "; input.oninput();
  const saving = save.click();
  const put = pending.at(-1);
  assert.equal(requestMethod(put), "PUT");
  assert.equal(JSON.parse(put.opts.body).root, "disk:/new");
  assert.equal(save.onclick(), saving);
  assert.equal(pending.filter((item) => requestMethod(item) === "PUT").at(-1), put);
  put.resolve(diskRootResponse("disk:/new"));
  await saving;
  const staleRefresh = pending.at(-1);
  assert.equal(requestMethod(staleRefresh), "GET");
  assert.equal(document.querySelector("#save-disk-root").textContent, "Сохранить корневую папку");
  assert.equal(document.querySelector("#save-disk-root").disabled, true, "refresh readiness, not the PUT lock, controls the new form");

  const users = pagination.loadAdmin("users");
  pending.at(-1).resolve(response([]));
  await users;
  const reopened = pagination.loadAdmin("disk-root");
  const newGet = pending.at(-1);
  newGet.resolve(diskRootResponse("disk:/new"));
  await reopened;
  const currentInput = document.querySelector("#disk-root-input");
  assert.equal(currentInput.disabled, false);
  currentInput.value = "disk:/edited"; currentInput.oninput();
  const currentHtml = document.querySelector("#disk-root-current").innerHTML;
  staleRefresh.resolve(diskRootResponse("disk:/stale"));
  await drainMicrotasks();
  assert.equal(document.querySelector("#disk-root-input"), currentInput);
  assert.equal(currentInput.value, "disk:/edited");
  assert.equal(document.querySelector("#disk-root-current").innerHTML, currentHtml);

  const staleOpen = pagination.loadAdmin("disk-root");
  const staleFailure = pending.at(-1);
  const latestOpen = pagination.loadAdmin("disk-root");
  pending.at(-1).resolve(diskRootResponse("disk:/latest"));
  await latestOpen;
  staleFailure.reject(new Error("late GET failure"));
  await staleOpen;
  assert.equal(document.querySelector("#disk-root-input").value, "disk:/latest");
  assert.equal(document.querySelector("#admin-error").textContent, "");
}

async function diskRootPutFailureAndRefreshFailure() {
  let opened = pagination.loadAdmin("disk-root");
  pending.at(-1).resolve(diskRootResponse("disk:/before-failure"));
  await opened;
  let input = document.querySelector("#disk-root-input");
  input.value = "disk:/will-fail"; input.oninput();
  const failedSave = document.querySelector("#save-disk-root").click();
  const failedPut = pending.at(-1);
  const users = pagination.loadAdmin("users"); pending.at(-1).resolve(response([])); await users;
  opened = pagination.loadAdmin("disk-root");
  const pendingGet = pending.at(-1);
  failedPut.reject(new Error("PUT failed"));
  await failedSave;
  assert.match(document.querySelector("#admin-error").textContent, /Нет соединения/);
  assert.equal(document.querySelector("#save-disk-root").disabled, true);
  pendingGet.resolve(diskRootResponse("disk:/still-current")); await opened;
  assert.equal(document.querySelector("#save-disk-root").disabled, false);

  input = document.querySelector("#disk-root-input");
  input.value = "disk:/saved"; input.oninput();
  const successfulSave = document.querySelector("#save-disk-root").click();
  pending.at(-1).resolve(diskRootResponse("disk:/saved"));
  await successfulSave;
  const failedRefresh = pending.at(-1);
  failedRefresh.reject(new Error("refresh failed"));
  await drainMicrotasks();
  assert.match(document.querySelector("#disk-root-message").textContent, /сохранена/);
  assert.match(document.querySelector("#admin-error").textContent, /Не удалось загрузить/);
  const putsBeforeRetry = pending.filter((item) => requestMethod(item) === "PUT").length;
  const retry = document.querySelector("#retry-disk-root").click();
  pending.at(-1).resolve(diskRootResponse("disk:/saved")); await retry;
  assert.equal(pending.filter((item) => requestMethod(item) === "PUT").length, putsBeforeRetry);
  assert.equal(document.querySelector("#save-disk-root").disabled, false);
}

async function staleSearchTimerCannotActAsNewView() {
  const callbacks = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  global.setTimeout = (callback) => { callbacks.push(callback); return callbacks.length; };
  global.clearTimeout = () => {};
  try {
    const renames = pagination.loadAdmin("renames");
    pending.at(-1).resolve(response([]));
    await renames;
    const search = document.querySelector("#rename-user-search");
    search.value = "old query";
    search.oninput();
    const requestCount = pending.length;
    const users = pagination.loadAdmin("users");
    pending.at(-1).resolve(response([]));
    await users;
    await callbacks.at(-1)();
    assert.equal(pending.length, requestCount + 1, "stale timer must not issue a search request");
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
}

(async () => {
  await independentPagersAndReset();
  await atomicNavigationFailureRetryAndDoubleClick();
  await adminViewGeneration();
  await reopenedAdminViewSupersedesPendingWork();
  await diskRootReadinessAndRetry();
  await diskRootSaveLockDoesNotWaitForRefresh();
  await diskRootPutFailureAndRefreshFailure();
  await diskRootSaveSurvivesViewReplacement();
  await staleSearchTimerCannotActAsNewView();
  process.stdout.write("frontend pagination regressions passed\n");
})().catch((error) => { console.error(error); process.exitCode = 1; });
