# Feedback email

New feedback is saved durably in `app/data/feedback-mail.db`, then a background worker emails it to
`sophiezeng@college.harvard.edu`. The browser receives an acknowledgment of storage/queuing, not a
claim of inbox delivery. Temporary failures retry with a delay up to one hour; pending entries survive
server restarts. Keep the database private and include it in backups. Old `feedback.jsonl` entries
are retained but are not automatically emailed retroactively.

## Google Apps Script relay

1. In the owner's Harvard Google account, create a private Apps Script project and paste
   [`feedback-mail.gs`](feedback-mail.gs) into its code file.
2. Run `setup` once and authorize its send-email permission. It does not request access to the inbox.
   Copy the generated `FEEDBACK_MAIL_SECRET` from the execution log directly into the VM's private
   `app/.env`. Treat it as a credential; do not paste it into GitHub, frontend code, or public logs.
3. Deploy as a **Web app**, executing as the owner, with access set to **Anyone**. The handler rejects
   requests without the secret and fixes the recipient in source, so it is not an open mail relay.
4. Add the deployed `/macros/s/.../exec` URL as `FEEDBACK_MAIL_URL` in the same private `.env`.
5. Restart Qurated and submit one clearly labeled test through the signed-in feedback form. Check
   both the database's `sent_at` and the destination inbox before considering delivery verified.

The relay uses only `MailApp`, `PropertiesService`, `LockService`, `Utilities`, and `ContentService`.
It records feedback IDs (not message bodies or sender addresses) for 30 days to suppress most
duplicate retries. As with other mail systems, a crash immediately after sending can cause a
duplicate; the stable feedback ID is included in each email. Google quotas or the 4,000-ID retention
cap pause delivery without discarding the server's saved queue. Monitor `sent_at IS NULL` and
`last_error` in the feedback database if notifications stop arriving. Provider acceptance alone does
not prove inbox delivery; check spam/filtering as needed.

For changes to the relay code, update the Apps Script deployment to a new version as well as GitHub.
Changing Python/UI code alone does not update the separate Google relay. Keep the same secret and
deployment URL when updating versions. To disable sending, unset `FEEDBACK_MAIL_URL` and restart;
new feedback will still be saved and will be delivered after configuration is restored.
