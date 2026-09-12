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

(async () => {
  await independentPagersAndReset();
  await atomicNavigationFailureRetryAndDoubleClick();
  await adminViewGeneration();
  process.stdout.write("frontend pagination regressions passed\n");
})().catch((error) => { console.error(error); process.exitCode = 1; });
