# Alexa Shopping List → Google Sheets

Scrape your Alexa shopping list from amazon.co.uk and append it to a Google Sheet, then clear the items from Alexa so nothing is read twice.

Runs unattended on a headless server: a persistent Chrome profile keeps the Amazon session alive between runs, so the login (and the one-time-code dance) only happens when Amazon actually asks for it.

> **Heads up:** earlier versions of this project documented an OAuth *client ID* / `client_secrets.json` flow. That no longer matches the code — it uses a Google **service account** key. See [Google Sheets setup](#google-sheets-setup).

## How it works

1. Launches Chrome via [undetected-chromedriver][uc] using a persistent profile (`~/.cache/amz-profile`).
2. Opens the shopping list URL directly, using an *eager* page-load strategy, so a normal run reaches the list in seconds instead of waiting for every image and ad to finish. The homepage is loaded only on a run that has to sign in.
3. Otherwise signs in: email → password → TOTP, generating the one-time code at the moment it is typed.
4. Reads every item from the list page — or works out that the list is empty, which is the normal case on most mornings, and exits cleanly.
5. Appends the items to your worksheet, below the last populated row of column A.
6. Optionally deletes the items from Alexa, verifying the list actually empties.
7. Logs a clear summary at each step and dumps page HTML + a screenshot if anything goes wrong.

[uc]: https://github.com/ultrafunkamsterdam/undetected-chromedriver

## Requirements

- Python 3.9 or newer
- Google Chrome installed (`google-chrome`, `chromium`, or `$CHROME_BIN`)
- No display needed — the script runs headless by default
- `setuptools` on Python 3.12+, because undetected-chromedriver 3.5.5 still imports `distutils`

The matching ChromeDriver is downloaded automatically, and the Chrome *major version is detected from your installed binary* rather than hard-coded.

## Installation

```bash
git clone https://github.com/wh-at-ev-er/AlexaShoppingList.git
cd AlexaShoppingList

python3 -m venv venv && source venv/bin/activate
pip install -U undetected-chromedriver selenium pandas gspread gspread-dataframe \
                google-auth pyotp python-dotenv keyring setuptools
```

## Google Sheets setup

1. In the [Google Cloud Console](https://console.cloud.google.com/), create (or pick) a project and enable **both** APIs under *APIs & Services → Library*:
   - Google Sheets API
   - Google Drive API
2. Go to *APIs & Services → Credentials → Create credentials → **Service account***, and create one.
3. Open the new service account, go to the **Keys** tab, *Add key → Create new key → JSON*, and download it.
4. Save the file as `~/scripts/gdrive.json` (or point `GDRIVE_JSON` at wherever you put it).
5. **Share your spreadsheet with the service account's email address** (`something@your-project.iam.gserviceaccount.com`) and give it **Editor** access.
   This is the single most common setup mistake — a service account cannot see a spreadsheet it has not been shared on, and the error you get back is a bare `403`.
6. Create a spreadsheet named **Shopping List** containing a worksheet named **Shopping List**, or override the names with `SHEET_NAME` and `WORKSHEET_NAME`.

## Amazon credentials

The password and TOTP secret are read from the system keyring (service `shoplist`), falling back to environment variables:

```bash
python3 -c "import keyring; keyring.set_password('shoplist', 'amazon', 'YOUR_AMAZON_PASSWORD')"
python3 -c "import keyring; keyring.set_password('shoplist', 'aws_otp_secret', 'YOUR_TOTP_SECRET')"
```

On a headless server with no keyring backend, use `AMAZON_PASSWORD` and `AMAZON_OTP_SECRET` instead.

### Getting your TOTP secret

*Your Account → Login & Security → 2-step verification*

- **Not yet enabled:** click **Turn On**, choose **Authentication App**, then **Can't scan the barcode**.
- **Already enabled:** click **Manage → Authenticator App → Add New App**, then **Can't scan the barcode**.

Copy the key (13 groups of 4 characters) and remove the spaces, leaving something like `ASDMASDFMSKDMKSFMKLASDDADABB6JNRNF7WFEHQW23H238R7843`. If you currently use SMS verification, switch to an authenticator app first.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `AMAZON_LOGIN` | — | Amazon account email |
| `AMAZON_PASSWORD` | — | Password (fallback when keyring is unavailable) |
| `AMAZON_OTP_SECRET` | — | TOTP secret (fallback when keyring is unavailable) |
| `GDRIVE_JSON` | `~/scripts/gdrive.json` | Service account JSON key |
| `SHEET_NAME` | `Shopping List` | Spreadsheet name |
| `WORKSHEET_NAME` | `Shopping List` | Worksheet name |
| `DELETE_AFTER_DOWNLOAD` | `1` | Set to `0` to leave items on the Alexa list |
| `AMZ_PROFILE_DIR` | `~/.cache/amz-profile` | Persistent Chrome profile (holds the session) |
| `AMZ_DEBUG_DIR` | `/tmp/amz-debug` | Where HTML dumps, screenshots and the driver log go |
| `AMZ_LOGIN_TIMEOUT` | `60` | Seconds to wait for the sign-in form |
| `CHROME_BIN` | auto-detected | Path to the Chrome binary |

## Usage

```bash
# Read-only first pass: no Sheets write, no deletions, dumps the page HTML
python3 alexa_shopping_list.py --dry-run --dump-html

# Full run
python3 alexa_shopping_list.py

# Ignore the saved session and log in again
python3 alexa_shopping_list.py --force-login

# Visible window, for debugging on a machine with a display
xvfb-run -a python3 alexa_shopping_list.py --no-headless
```

| Flag | Effect |
| --- | --- |
| `--dry-run` | Read and print items only; nothing written, nothing deleted |
| `--dump-html` | Save the list page HTML and screenshot even on success |
| `--force-login` | Discard the saved session and authenticate afresh |
| `--no-headless` | Show a real browser window (pair with `xvfb-run`) |

## Running on a schedule

Cron runs with a minimal environment and does **not** expand `~`, so use absolute
paths. Keep the environment in a `chmod 600` file and source it. This is also how
you supply the credentials: there is no D-Bus session under cron, so a desktop
keyring normally fails there.

```sh
# /home/USER/.alexa-shopping.env      chmod 600
HOME=/home/USER
PATH=/usr/local/bin:/usr/bin:/bin
CHROME_BIN=/usr/bin/google-chrome
AMAZON_PASSWORD=...
AMAZON_OTP_SECRET=...
```

A crontab entry has to be one logical line, so the cron examples below are long on
purpose — paste them as a single line rather than splitting them. `flock` stops a
slow run from overlapping the next one:

```cron
30 7 * * * . /home/USER/.alexa-shopping.env && flock -n /tmp/alexa-shopping.lock /home/USER/AlexaShoppingList/venv/bin/python /home/USER/AlexaShoppingList/alexa_shopping_list.py >> /home/USER/alexa-shopping.log 2>&1
```

`CHROME_BIN` is only needed if `google-chrome` is not on that `PATH`; the script
detects it otherwise.

### Suppressing the output

```cron
30 7 * * * . /home/USER/.alexa-shopping.env && /home/USER/AlexaShoppingList/venv/bin/python /home/USER/AlexaShoppingList/alexa_shopping_list.py >/dev/null 2>&1
```

The trade-off: you also lose the `WARNING: N item(s) are still on the Alexa list`
line, and whatever cron would have mailed you. If you silence it, the sheet itself
is your only signal that something stopped working.

### Keeping a log, cleared automatically

**Weekly truncate** — simplest, keeps roughly one week:

```cron
30 7 * * *  . /home/USER/.alexa-shopping.env && /home/USER/AlexaShoppingList/venv/bin/python /home/USER/AlexaShoppingList/alexa_shopping_list.py >> /home/USER/alexa-shopping.log 2>&1
0  8 * * 0  : > /home/USER/alexa-shopping.log
```

Truncating is safe while other runs are appending: `>>` opens the file with
`O_APPEND`, so every write goes to the current end of the file.

**Monthly files, pruned** — keeps history and cleans itself up:

```cron
30 7 * * *  . /home/USER/.alexa-shopping.env && /home/USER/AlexaShoppingList/venv/bin/python /home/USER/AlexaShoppingList/alexa_shopping_list.py >> /home/USER/logs/alexa-$(date +\%Y-\%m).log 2>&1
0  3 1 * *  find /home/USER/logs -name 'alexa-*.log' -mtime +90 -delete
```

Escape the `%` as `\%`: cron turns a bare `%` into a newline in the command.

**logrotate** — proper weekly rotation with compression and retention. Install as
`/etc/logrotate.d/alexa-shopping` (needs root once; the system then runs it from
its own daily cron):

```
/home/USER/alexa-shopping.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
```

`copytruncate` is the directive that matters here. The script is a short-lived
process that appends and exits, so there is nothing to signal for a reopen;
logrotate copies the file and truncates it in place instead. `rotate 8` with
`weekly` keeps about two months.

If you would rather not manage rotation at all, a systemd timer logs to the
journal, which rotates itself via `SystemMaxUse=`.

## Selector reference

Amazon reshuffles this page periodically. Every stage tries a list of candidate locators and **logs which one matched**, so drift shows up as a message instead of a silent failure. These were confirmed against `amazon.co.uk` with Chrome 156:

| Stage | Locator in use |
| --- | --- |
| Email field | `#ap_email_login` (also matches `name="email"`) |
| Email submit | `#continue` |
| Password field | `#ap_password` |
| Password submit | `#signInSubmit` |
| One-time code | `name="otpCode"` |
| One-time code submit | `#auth-signin-button` |
| List items | `.virtual-list .item-title` |
| Delete controls | per-row button containing "Delete" |

The sign-in URL is deliberately **not** hard-coded: the script requests the list URL and lets Amazon redirect to `/ap/signin?openid.…&assoc_handle=gbflex…`, so changes to those parameters cannot break it.

## Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `WARNING: no items found after 25s` | Either the list is genuinely empty, or the markup moved | Check `AMZ_DEBUG_DIR/*.html`. An empty list is normal and the run still exits 0, so this line plus a dump is the signal to look at |
| `still signed out after login` | CAPTCHA, passkey prompt or a rejected code | Check the dumped screenshot; retry, or run `--no-headless` under `xvfb-run` |
| `no password field appeared` | Amazon is asking for a passkey or device approval | Complete that step once in a visible browser so the profile caches the state |
| `WARNING: N item(s) are still on the Alexa list` | A delete click stopped working | Your data is already in the sheet; the loop stopped on purpose rather than spinning |
| Login every run | Session cookie expired or the profile was wiped | Expected occasionally; pass `--force-login` to do it deliberately |
| Chrome/driver version mismatch | Chrome updated past the last undetected-chromedriver release (Feb 2024) | The version is auto-detected; if that fails, set `CHROME_BIN`. Chrome now ships a major version roughly every two weeks |
| `NoKeyringError` | Headless server with no keyring backend | Use `AMAZON_PASSWORD` / `AMAZON_OTP_SECRET` |

## Changes from the previous version

The original script stopped working silently. The substantive fixes:

- **The one-time-code field is `name="otpCode"`.** The old code waited on `#auth-mfa-otpcode`, which no longer matched; its `except: pass` then skipped the code entirely and carried on unauthenticated.
- **Failures are loud.** Every stage reports the URL and title it actually landed on, and dumps the page, instead of falling through to a silent no-op that wrote nothing and exited 0.
- **The TOTP code is generated at submit time.** It used to be generated at import, before Chrome had even started, so a slow launch could burn the 30-second window.
- **Chrome version is detected, not pinned.** A hard-coded `version_main` goes stale within weeks.
- **The delete loop tolerates the virtual list re-rendering.** A `stale element reference` during a delete means the row was re-rendered, not that the deletion failed; it now re-finds the control and retries, verifies the row count falls, and is bounded by a stall counter and time budget.
- **Sheet data is written before items are deleted**, so a failed delete can no longer cost you the data.
- **A persistent profile** removes the fragile login from every run.
- **`google-auth` instead of the archived `oauth2client`**, and `#disable-dev-shm-usage` for small `/dev/shm` on VPSes.

## Notes and limitations

- Written and verified against **amazon.co.uk**. For another region, change `BASE_URL` and `LIST_URL`, and expect that region's own sign-in handle.
- Verified against one UK account on Chrome 156. There is no test suite committed; the delete-loop behaviour was checked with simulated stale-element and stuck-list cases during development.
- Headless Chrome is detectable. The script overrides the `HeadlessChrome` user agent, but `--no-headless` under `xvfb-run` gives the most convincing session.
- Deletion is best-effort by design: items are always written to the sheet first.
- This automates your own account, so check that it is consistent with Amazon's Conditions of Use before relying on it.
