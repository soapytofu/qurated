# Setting up Google sign-in

Qurated uses "Sign in with Google" and only lets in verified `@college.harvard.edu` accounts. For that, you need
a free **OAuth Client ID**, which tells Google which website is allowed to show the sign-in button. It takes about
10 minutes, with no billing or credit card.

**Which Google account to use:** a personal Gmail works best. Harvard-managed accounts may not be allowed to create
Cloud projects. The account that *owns* the project doesn't limit who can *sign in* to Qurated.

## 1. Create a project
1. Go to https://console.cloud.google.com/ and sign in. Accept the terms if it asks.
2. Click the project picker at the top left → **New project**.
3. Name it `qurated`, leave Organization as "No organization", then click **Create**.
4. Make sure `qurated` is selected in the project picker.

## 2. Set up the sign-in screen
Google calls this **Google Auth Platform**. Older guides call it the "OAuth consent screen".
1. Open ☰ → **APIs & Services → OAuth consent screen**, or search "Google Auth Platform".
2. Click **Get started** and fill in:
   - **App information:** app name `Qurated`, with your email as the support email.
   - **Audience:** **External**. "Internal" would block Harvard accounts.
   - **Contact information:** your email.
   - Agree to the terms → **Create**.
3. **Branding:** leave the logo blank. Uploading a logo requires Google's app verification, which can take weeks.
4. **Audience → Publishing status:**
   - **Testing** (fine for local use and pilots): add each tester's email under **Test users** (up to 100).
     Anyone not on the list sees "Access blocked".
   - **Publishing** to all students requires an app home page, a privacy policy and an authorized domain on the
     Branding page, which means hosting Qurated on a real domain first. Qurated only asks for name and email,
     so Google shouldn't require a full review.
5. Leave **Data access / Scopes** alone. The default `openid`, `email` and `profile` are all Qurated needs.

## 3. Create the Client ID
1. Left sidebar → **Clients** → **Create client**.
2. **Application type:** Web application. **Name:** `qurated web`.
3. **Authorized JavaScript origins**: add both:
   - `http://localhost`
   - `http://localhost:8765`

   Type them exactly: `http`, no trailing slash, no path. Google may reject `127.0.0.1`, so always open the app
   at `localhost`.
4. **Authorized redirect URIs:** leave empty. Qurated uses the pop-up sign-in.
5. **Create**, then copy the **Client ID** (`…apps.googleusercontent.com`). You can ignore the client secret.

## 4. Configure Qurated
1. In `app/.env`, set `GOOGLE_CLIENT_ID=<your client id>` (no quotes).
2. Restart the server. `.env` is only read at startup.
3. Open **http://localhost:8765**. You should see **Continue with Google**. New origins can take about 5 minutes
   to start working.

## 5. Test it
- A `@college.harvard.edu` account (listed as a test user while in Testing) should land on the home page.
- A personal Gmail should see "Qurated is only for Harvard College students…", which means the gate works.

## How the check works
The browser receives a Google-signed ID token. The server verifies its signature, expiry, issuer and audience
(your Client ID) with `google-auth`. It then requires `email_verified`, an email domain in
`ALLOWED_EMAIL_DOMAINS`, and a matching Google Workspace `hd` claim, before setting a signed session cookie.

## Troubleshooting
| What you see | Fix |
|---|---|
| "Google sign-in isn't configured yet" | `GOOGLE_CLIENT_ID` is empty, or the server wasn't restarted. |
| No button / "origin is not allowed" | The address bar must exactly match an authorized origin, including the port: use `http://localhost:8765`, then wait about 5 minutes. |
| "Access blocked: app has not completed verification" | The app is in Testing and this email isn't a test user. |
| "Access blocked … administrator" on a Harvard account | Harvard's Google admins limit some third-party apps. Basic name/email sign-in normally works. If not, contact Harvard IT (HUIT). |
| Pop-up opens and closes instantly | Allow pop-ups for localhost, and don't block third-party cookies for `accounts.google.com`. |
| A Harvard grad-school account is rejected | Add its domain, e.g. `ALLOWED_EMAIL_DOMAINS=college.harvard.edu,g.harvard.edu`, then restart. |

**Hosting later:** add your real site (e.g. `https://qurated.example.org`) as another Authorized JavaScript
origin, and set `HTTPS_ONLY=1` in `.env`.
