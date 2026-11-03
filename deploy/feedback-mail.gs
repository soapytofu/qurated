// Deploy as a web app, executing as the Harvard account that owns this script.
// Only this fixed recipient can receive messages; callers cannot supply one.
const FEEDBACK_RECIPIENT = 'sophiezeng@college.harvard.edu';

function setup() {
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('FEEDBACK_MAIL_SECRET')) {
    props.setProperty('FEEDBACK_MAIL_SECRET', Utilities.getUuid() + Utilities.getUuid());
  }
  // Copy this once to the VM's private .env; never put it in GitHub or the frontend.
  console.log('FEEDBACK_MAIL_SECRET=' + props.getProperty('FEEDBACK_MAIL_SECRET'));
  console.log('Remaining email quota: ' + MailApp.getRemainingDailyQuota());
}

function jsonReply(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  let request;
  try {
    if (!e || !e.postData || e.postData.contents.length > 50000) return jsonReply({ok: false});
    request = JSON.parse(e.postData.contents);
  } catch (_) { return jsonReply({ok: false}); }
  const props = PropertiesService.getScriptProperties();
  const secret = props.getProperty('FEEDBACK_MAIL_SECRET');
  if (!secret || !request || request.secret !== secret) return jsonReply({ok: false});
  const f = request.feedback;
  if (!f || !/^[a-f0-9-]{36}$/.test(f.id) ||
      !['idea', 'bug', 'other'].includes(f.kind) ||
      typeof f.message !== 'string' || !f.message.trim() || Array.from(f.message).length > 3000 ||
      typeof f.at !== 'string' || f.at.length > 50 ||
      (f.email !== null && (typeof f.email !== 'string' ||
       !/^[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+$/.test(f.email) || f.email.length > 254))) {
    return jsonReply({ok: false});
  }
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return jsonReply({ok: false});
  try {
    // Persist acknowledgments so retrying after an HTTP timeout normally cannot
    // resend an email. Retain 30 days; the feedback ID is also in every email.
    const key = 'sent_' + f.id;
    if (props.getProperty(key)) return jsonReply({ok: true, id: f.id});
    const cutoff = Date.now() - 30 * 86400000;
    const saved = props.getProperties();
    let count = 0;
    Object.keys(saved).forEach(k => {
      if (k.startsWith('sent_')) {
        if (Number(saved[k]) < cutoff) props.deleteProperty(k);
        else count++;
      }
    });
    // Stay below the script property store's size limit. The VM retains and retries.
    if (count >= 4000 || MailApp.getRemainingDailyQuota() < 1) return jsonReply({ok: false});
    const mail = {
      to: FEEDBACK_RECIPIENT,
      name: 'Qurated Feedback',
      subject: '[Qurated feedback] ' + f.kind + ' — ' + f.id.slice(0, 8),
      body: 'New Qurated feedback\n\nType: ' + f.kind + '\nSubmitted: ' + f.at +
        '\nContact: ' + (f.email || 'Not shared (sender did not opt in)') +
        '\n\n' + f.message + '\n\nFeedback ID: ' + f.id
    };
    if (f.email) mail.replyTo = f.email;
    MailApp.sendEmail(mail);
    props.setProperty(key, String(Date.now()));
    return jsonReply({ok: true, id: f.id});
  } catch (_) {
    return jsonReply({ok: false});
  } finally { lock.releaseLock(); }
}
