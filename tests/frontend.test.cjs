const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/app.js', 'utf8');
// Execute the actual helpers without requiring the application DOM.
const extract = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const sandbox = { URL };
vm.createContext(sandbox);
vm.runInContext(extract('function safeExternalURL', 'function catalogHTML'), sandbox);
test('syllabus links reject executable and credential-bearing URLs', () => {
  for (const input of ['javascript:alert(1)', 'data:text/html,<script>x</script>', '//evil.example', 'https://a:b@example.com', null]) {
    assert.equal(sandbox.safeExternalURL(input), null);
  }
  assert.equal(sandbox.safeExternalURL('https://example.com/syllabus'), 'https://example.com/syllabus');
});
test('failed HTTP writes reject rather than report success', async () => {
  sandbox.api = async () => ({ok: false});
  vm.runInContext(extract('async function checkedWrite', 'function saveError'), sandbox);
  await assert.rejects(sandbox.checkedWrite('/api/account/prefs', {}), /weren't saved/);
});

for (const exit of ['complete', 'skip', 'close', 'escape']) {
  test(`guide ${exit} persists dismissal before the queued close event`, () => {
    const nodes = new Map(), saved = new Map();
    let closeQueued = false;
    const node = (id) => {
      if (!nodes.has(id)) nodes.set(id, {
        handlers: {}, hidden: false,
        addEventListener(type, fn) { this.handlers[type] = fn; },
        focus() {}, scrollIntoView() {}, showModal() {},
        close() { closeQueued = true; }, // Deliberately do not dispatch the asynchronous close event.
      });
      return nodes.get(id);
    };
    const context = {
      $: node, store: { set: (k, v) => saved.set(k, v), get: (k, fallback) => saved.get(k) ?? fallback },
      closeAll() {}, toggleFilters() {}, acctMenu: {},
    };
    vm.createContext(context);
    vm.runInContext(source.slice(source.indexOf('const guideSteps =')), context);
    if (exit === 'complete') {
      for (let i = 0; i < 4; i++) node('#guide-next').handlers.click();
    } else if (exit === 'escape') {
      let prevented = false;
      node('#quick-guide').handlers.cancel({ preventDefault() { prevented = true; } });
      assert.equal(prevented, true);
    } else {
      node(exit === 'skip' ? '#guide-skip' : '#guide-close').handlers.click();
    }
    assert.equal(closeQueued, true);
    assert.equal(saved.get('qurated.guideSeen.v1'), true);
    assert.equal(node('#guide-welcome').hidden, true);
  });
}

vm.runInContext(extract('function dataCoverageText', 'api("/api/meta")'), sandbox);
test('coverage distinguishes empty, catalog-only, historical-only and mixed data', () => {
  assert.match(sandbox.dataCoverageText({courses: 0}), /No course data/);
  assert.match(sandbox.dataCoverageText({courses: 4, catalog_courses: 4, reviewed_courses: 0}), /No Q Guide reviews/);
  assert.match(sandbox.dataCoverageText({courses: 4, catalog_courses: 0, reviewed_courses: 4}), /catalog is missing/);
  const mixed = sandbox.dataCoverageText({courses: 10, catalog_courses: 8, reviewed_courses: 3, terms: ['2025 Fall', '2026 Spring']});
  assert.match(mixed, /8 courses in the catalog/);
  assert.match(mixed, /3 courses with Q Guide reports/);
  assert.match(mixed, /2025 Fall, 2026 Spring/);
  assert.doesNotMatch(mixed, /undefined/);
});
