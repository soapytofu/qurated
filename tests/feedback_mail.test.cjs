const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('deploy/feedback-mail.gs', 'utf8');

function relay() {
  const stored = new Map([['FEEDBACK_MAIL_SECRET', 'test-secret']]);
  const sent = [];
  const context = {
    PropertiesService: {getScriptProperties: () => ({
      getProperty: k => stored.get(k), setProperty: (k, v) => stored.set(k, v),
      getProperties: () => Object.fromEntries(stored), deleteProperty: k => stored.delete(k),
    })},
    ContentService: {MimeType: {JSON: 'json'}, createTextOutput: text => ({setMimeType: () => JSON.parse(text)})},
    LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock() {}})},
    MailApp: {getRemainingDailyQuota: () => 100, sendEmail: mail => sent.push(mail)},
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  const feedback = {id: '12345678-1234-1234-1234-123456789abc', kind: 'idea',
    message: 'Synthetic feedback', at: '2026-11-03T15:00:00-05:00', email: null};
  const send = (f = feedback, secret = 'test-secret') => context.doPost({postData: {
    contents: JSON.stringify({secret, feedback: f, to: 'attacker@example.invalid'})}});
  return {context, stored, sent, feedback, send};
}

test('invalid authentication cannot trigger email', () => {
  const r = relay();
  assert.equal(r.send(r.feedback, 'wrong').ok, false);
  assert.equal(r.sent.length, 0);
});

test('recipient is fixed and anonymous feedback has no reply-to address', () => {
  const r = relay();
  assert.equal(r.send().ok, true);
  assert.equal(r.sent[0].to, 'sophiezeng@college.harvard.edu');
  assert.equal(r.sent[0].replyTo, undefined);
  assert.match(r.sent[0].body, /Not shared/);
});

test('opted-in address becomes reply-to, never a recipient', () => {
  const r = relay();
  assert.equal(r.send({...r.feedback, email: 'student@example.invalid'}).ok, true);
  assert.equal(r.sent[0].replyTo, 'student@example.invalid');
  assert.equal(r.sent[0].to, 'sophiezeng@college.harvard.edu');
});

test('retrying the same feedback ID does not send another email', () => {
  const r = relay();
  assert.equal(r.send().ok, true);
  assert.equal(r.send().ok, true);
  assert.equal(r.sent.length, 1);
});

test('send failure is not acknowledged and can be retried', () => {
  const r = relay();
  const original = r.context.MailApp.sendEmail;
  r.context.MailApp.sendEmail = () => { throw Error('provider unavailable'); };
  assert.equal(r.send().ok, false);
  assert.equal(r.stored.has('sent_' + r.feedback.id), false);
  r.context.MailApp.sendEmail = original;
  assert.equal(r.send().ok, true);
  assert.equal(r.sent.length, 1);
});

test('quota exhaustion leaves the message available for retry', () => {
  const r = relay();
  r.context.MailApp.getRemainingDailyQuota = () => 0;
  assert.equal(r.send().ok, false);
  assert.equal(r.sent.length, 0);
});

test('Unicode feedback uses the same character limit as the API', () => {
  const r = relay();
  assert.equal(r.send({...r.feedback, message: '🙂'.repeat(3000)}).ok, true);
});

test('malformed payloads and header injection are rejected', () => {
  const r = relay();
  for (const patch of [{id: '../bad'}, {email: 'x@example.invalid\r\nBcc: other@example.invalid'},
    {message: ' '}, {message: 'x'.repeat(3001)}, {kind: 'idea\nBcc: x'}, {at: null}]) {
    assert.equal(r.send({...r.feedback, ...patch}).ok, false);
  }
  assert.equal(r.context.doPost({postData: {contents: 'invalid json'}}).ok, false);
  assert.equal(r.sent.length, 0);
});
