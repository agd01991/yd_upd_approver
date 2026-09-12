const assert = require("node:assert/strict");

global.__PAGINATION_TEST__ = true;
global.window = { Telegram: null };
const elements = new Map();
const element = () => ({
  classList: { add() {}, remove() {}, toggle() {} },
  querySelector: () => element(),
  querySelectorAll: () => [],
  appendChild() {}, remove() {}, click() {},
  innerHTML: "", textContent: "", value: "", dataset: {},
});
global.document = {
  querySelector(selector) { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); },
  querySelectorAll() { return []; },
  createElement: element,
  body: element(),
};
global.alert = () => {};

const pagination = require("../../app/webapp/static/app.js");
const pending = [];
global.fetch = (url) => new Promise((resolve, reject) => pending.push({ url: String(url), resolve, reject }));
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
  await diskRootSaveSurvivesViewReplacement();
  await staleSearchTimerCannotActAsNewView();
  process.stdout.write("frontend pagination regressions passed\n");
})().catch((error) => { console.error(error); process.exitCode = 1; });
