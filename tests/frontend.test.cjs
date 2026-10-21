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
