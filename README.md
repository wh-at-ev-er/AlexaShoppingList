# AlexaShoppingList

Scrape the Alexa shopping list from amazon.co.uk, append it to Google Sheets, then clear the items from Alexa so nothing is read twice.

Designed to run unattended from cron on a headless Linux server. It drives a real Chrome through [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver), signs in with email + password + TOTP, reads the list, and appends it to your worksheet.

---

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Google Sheets credentials](#google-sheets-credentials)
- [Amazon account and OTP secret](#amazon-account-and-otp-secret)
- [Configuration](#configuration)
- [Usage and the `--debug` flag](#usage-and-the---debug-flag)
- [What the log looks like](#what-the-log-looks-like)
- [Running on a schedule (cron)](#running-on-a-schedule-cron)
- [Troubleshooting](#troubleshooting)
- [Notes and limitations](#notes-and-limitations)

---

## How it works

1. Generates a fresh TOTP code from your stored secret.
2. Launches headless Chrome with undetected-chromedriver. The library downloads and patches a matching ChromeDriver on every run, so Chrome must be installed and the machine must have working DNS.
3. Loads `amazon.co.uk` briefly to seed cookies, then the sign-in URL.
4. Waits for the email field, detects whether Amazon is serving a single-stage (email + password on one page) or two-stage form, and submits both.
5. If an OTP challenge appears within 10 seconds, types the generated code and submits it. If none appears, it carries on — Amazon often accepts a session without one.
6. Lands on the shopping list page and waits up to 20 seconds for the first item.
7. Reads every item title.
8. Deletes the items from Alexa (unless disabled).
9. Appends the items to the bottom of column A on your worksheet.
10. Closes the browser.

Each run is a complete, isolated login: there is **no saved Chrome profile**, so every run goes through email → password → OTP. That is deliberate — it is the behaviour that has proven stable on the server this runs on.

**Exit codes:** `0` on success, including a genuinely empty list. A non-zero exit means the run failed and cron will report it (if it is configured to mail you).

## Requirements

- Python 3.9 or newer (developed and tested on 3.10)
- Google Chrome installed (`google-chrome` on the test server, version 156)
- No display needed — the browser is headless
- Outbound HTTPS to `amazon.co.uk` and to Google, plus working DNS at launch time

## Installation

```bash
git clone https://github.com/wh-at-ev-er/AlexaShoppingList.git
cd AlexaShoppingList

python3 -m venv venv
source venv/bin/activate

pip install undetected-chromedriver selenium pyotp gspread gspread-dataframe pandas \
            oauth2client python-dotenv keyring
```

The packages that matter:

| Package | Used for |
| --- | --- |
| `undetected-chromedriver` | Launching and patching Chrome/ChromeDriver |
| `selenium` | `By`, `WebDriverWait`, `expected_conditions`, `ActionChains` |
| `pyotp` | Generating the two-factor code |
| `gspread`, `gspread-dataframe` | Opening the sheet and writing the DataFrame |
| `oauth2client` | Loading the Google service-account key |
| `keyring` | Reading the Amazon password and OTP secret from the OS keyring |
| `python-dotenv` | Loads a `.env` file if one is present |

## Google Sheets credentials

The script authenticates with a **service account**, not an OAuth client ID. (The original version of this README described an OAuth *client ID* and a `client_secrets.json` file; that flow was never what the code used, so it has been corrected here.)

1. Log into the [Google Cloud Console](https://console.cloud.google.com/) with the Google account whose spreadsheets you want to write to. Create or select a project.
2. Under **APIs & Services → Library**, enable both:
   - Google Sheets API
   - Google Drive API
3. Under **APIs & Services → Credentials → Create credentials → Service account**, create a service account.
4. Open it, go to the **Keys** tab, **Add key → Create new key → JSON**, and download the file.
5. Save it as **`~/scripts/gdrive.json`** — this exact path is hard-coded in the script (`os.environ['HOME'] + '/scripts/gdrive.json'`).
6. Copy the service account's email address (`something@your-project.iam.gserviceaccount.com`) and **share your spreadsheet with it as an Editor**. This is the single most common setup mistake: a service account cannot see a spreadsheet that has not been shared with it, and the failure is a bare `403`.
7. Create a spreadsheet called **Shopping List**, containing a worksheet called **Shopping List**. Both names are hard-coded. Items are appended below the last non-empty cell in column A, with no header row.

## Amazon account and OTP secret

**Two values, both stored in the system keyring** under the service name `shoplist`:

| Entry | Key |
| --- | --- |
| Amazon password | `amazon` |
| TOTP secret | `aws_otp_secret` |

Set them with:

```bash
python3 -c "import keyring; keyring.set_password('shoplist', 'amazon', 'YOUR_AMAZON_PASSWORD')"
python3 -c "import keyring; keyring.set_password('shoplist', 'aws_otp_secret', 'YOUR_TOTP_SECRET')"
```

Check they are readable before trusting cron:

```bash
python3 -c "import keyring; print(bool(keyring.get_password('shoplist', 'amazon')), bool(keyring.get_password('shoplist','aws_otp_secret')))"
```

This version reads the password and secret **only** from the keyring — there is no environment-variable fallback. On a headless server the keyring backend therefore has to work for the user cron runs as; if it does not, the script stops at the first line with a `NoKeyringError`, which is at least unambiguous.

**The Amazon email address is set in the script itself:**

```python
amz_login = <AMAZON_ACCOUNT_EMAIL>
```

Replace `<AMAZON_ACCOUNT_EMAIL>` with your address as a quoted string, e.g. `amz_login = 'you@example.com'`. As committed, the placeholder is not valid Python and the file will not run until it is filled in.

### Getting your TOTP secret

*Your Account → Login & Security → 2-step verification*

**If 2-step verification is not enabled:**

1. Log in to <https://www.amazon.co.uk/>
2. Go to **Your Account → Login & Security** and click **Turn On** under 2-step verification
3. Choose **Authentication App**
4. Click **Can't scan the barcode** and save the key (13 groups of 4 characters)
5. Remove the spaces, leaving something like `ASDMASDFMSKDMKSFMKLASDDADABB6JNRNF7WFEHQW23H238R7843`

**If it is already enabled with an authenticator app:**

1. Log in to <https://www.amazon.co.uk/>
2. Go to **Your Account → Login & Security** and click **Manage** under 2-step verification
3. Under Authenticator App, click **Add New App**
4. Click **Can't scan the barcode** and save the key
5. Remove the spaces, as above

**If you currently use SMS verification, switch to an authenticator app first** — this script generates codes from a shared secret and cannot receive texts.

## Configuration

Everything is set in the script; there are no environment variables for behaviour.

| Item | Where | Default | Notes |
| --- | --- | --- | --- |
| `amz_login` | line ~63 | `<AMAZON_ACCOUNT_EMAIL>` | **Must be filled in** before the script will run |
| `secret` | keyring | — | TOTP secret, keyring service `shoplist` |
| `amz_password` | keyring | — | Amazon password, keyring service `shoplist` |
| credentials path | in code | `~/scripts/gdrive.json` | Google service-account key |
| `sheet_name` | in code | `Shopping List` | Spreadsheet name |
| `main_worksheet` | in code | `Shopping List` | Worksheet name |
| `delete_after_download` | in code | `True` | Set to `False` to leave items on the Alexa list |
| `version_main` | in code | `156` | **Must match your installed Chrome's major version** |
| `DEBUG` | `--debug` flag or in code | `False` | Step-by-step trace |

Two of these will bite you eventually:

- **`version_main=156`** is pinned to the Chrome on the server. Chrome ships a new major version roughly every four weeks; when it updates, the pinned driver no longer matches and the run fails with `ChromeDriver only supports Chrome version 156`. Update the number to the new major version (`google-chrome --version`) after a Chrome upgrade.
- **`delete_after_download`** lives in the code rather than a flag. Change it and commit, or pass nothing and accept deletions.

**URLs for other regions:** change `amz_signin_url` and `amz_shoppinglist_url` to your country's equivalents, and note that the `openid.assoc_handle` in the sign-in URL (`gbflex` for the UK) and the login form's element ids are region-specific.

## Usage and the `--debug` flag

```bash
# Normal run: results, warnings and failures only
python3 alexa_shopping_list.py

# Full step-by-step trace, for working out where a run went wrong
python3 alexa_shopping_list.py --debug

# In a virtualenv, from cron, etc.
/home/USER/AlexaShoppingList/venv/bin/python /home/USER/AlexaShoppingList/alexa_shopping_list.py
```

| Flag | Effect |
| --- | --- |
| *(none)* | Prints only what a scheduled run needs: items read, deletions, rows written, and any failure |
| `--debug` | Adds the full trace: every step (1–10), the URL and page title at each landing, the driver version, per-pass delete counts, and an elapsed-time counter on each line |

`--debug` also prints `debug trace is ON` as its first line, so any log tells you which mode produced it. If you would rather not change the cron line, set `DEBUG = True` near the top of the script instead — the flag sets that same variable.

There is no headless/visible switch and no dry-run: the script always runs headless, always deletes, and always writes. To test safely, set `delete_after_download = False` and point `sheet_name` at a scratch spreadsheet.

## What the log looks like

**Normal run with items:**

```
[07:00:04] read 3 item(s): ['milk', 'banana', 'cheese']
[07:00:11] deleted 3 item(s) from the Alexa list
[07:00:12] wrote 3 row(s) to 'Shopping List' at row 6
```

**Empty list** — the normal case most mornings:

```
[08:00:04] no items on the list - nothing to do (page title='Alexa Shopping List')
```

**Empty list because Amazon served an error page** — same line, different title, and this is the whole point of putting the title there:

```
[08:00:04] no items on the list - nothing to do (page title='503 - Service Unavailable Error')
```

**Some items could not be deleted:**

```
[07:00:11] delete click failed (StaleElementReferenceException: stale element reference: stale element not found); leaving the rest in place
[07:00:11] deleted 2 of 3 item(s) from the Alexa list
```

**Login failed:**

```
[18:50:14] FAILED at login: no email field after 60s (TimeoutException: )
[18:50:14]   at failure: url=https://www.amazon.co.uk/ap/signin?... title='Amazon Sign-In'
[18:50:14]   screenshot: /home/USER/scripts/error_page.png
```

**With `--debug`,** the same run is preceded by the trace:

```
[18:50:14] debug trace is ON
[18:50:14] (+   0.0s) start: loading credentials from keyring
[18:50:14] (+   0.0s) credentials: login=you@example.com otp_secret=present password=present
[18:50:15] (+   0.6s) generating OTP
[18:50:15] (+   0.6s) creating the driver with uc.Chrome(version_main=156) - a stall here is the driver download/patch
[18:51:16] (+  62.0s) driver created: browser 156.0.8078.47
[18:51:16] (+  62.0s) STEP 1: loading amazon.co.uk to seed cookies
...
```

The `(+ elapsed)` counter is there to answer "what took 62 seconds?" — a stall between `creating the driver` and `driver created` is the ChromeDriver download or Chrome failing to start, not Amazon.

## Running on a schedule (cron)

Cron runs with a minimal environment and does not expand `~`, so use absolute paths. Run from a writable directory, because a login failure writes `error_page.png` to the current working directory:

```cron
0 * * * * cd /home/USER/scripts && /home/USER/scripts/venv/bin/python /home/USER/scripts/alexa_shopping_list.py >> /home/USER/logs/alexa-$(date +\%Y-\%m).log 2>&1
```

Escape bare `%` as `\%` — cron turns an unescaped `%` into a newline. One crontab entry must stay on one logical line, so the lines here are long on purpose.

### Suppressing the output entirely

```cron
0 * * * * cd /home/USER/scripts && /home/USER/scripts/venv/bin/python /home/USER/scripts/alexa_shopping_list.py >/dev/null 2>&1
```

The trade-off: you also lose the failure output and any mail cron would have sent. If you silence it, the spreadsheet itself becomes your only signal that the job has stopped working.

### Keeping a log, cleared automatically

**Weekly truncate** — simplest:

```cron
0 * * * *  cd /home/USER/scripts && /home/USER/scripts/venv/bin/python /home/USER/scripts/alexa_shopping_list.py >> /home/USER/logs/alexa.log 2>&1
0 3 * * 0  : > /home/USER/logs/alexa.log
```

Truncating is safe while runs are appending: `>>` opens with `O_APPEND`, so writes always go to the current end of the file.

**Monthly files, pruned after 90 days** — keeps history and cleans itself up:

```cron
0 * * * *  cd /home/USER/scripts && /home/USER/scripts/venv/bin/python /home/USER/scripts/alexa_shopping_list.py >> /home/USER/logs/alexa-$(date +\%Y-\%m).log 2>&1
0 3 1 * *  find /home/USER/logs -name 'alexa-*.log' -mtime +90 -delete
```

**logrotate** — proper rotation with compression. Install as `/etc/logrotate.d/alexa-shopping` (needs root once; the system then runs it from its own daily cron):

```
/home/USER/logs/alexa.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
```

`copytruncate` matters here: the script is a short-lived process that appends and exits, so there is no long-lived handle to signal for a reopen.

### Keeping runs from overlapping

A hung run (a stalled driver download can take a minute, and a wedged tab longer) can still be alive when the next tick fires. `flock` makes the second run exit instead of stacking up:

```cron
0 * * * * cd /home/USER/scripts && flock -n /tmp/alexa-shopping.lock /home/USER/scripts/venv/bin/python /home/USER/scripts/alexa_shopping_list.py >> /home/USER/logs/alexa.log 2>&1
```

Note that this version has **no retries**: one transient failure loses that hour. Frequent enough, and you may want to run it more often than hourly, or add a retry — see [Notes and limitations](#notes-and-limitations).

## Troubleshooting

Run the script by hand with `--debug` before trusting the next cron tick:

```bash
cd /home/USER/scripts && ./venv/bin/python alexa_shopping_list.py --debug
```

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `Temporary failure in name resolution` | DNS was down while ChromeDriver was being downloaded — undetected-chromedriver fetches a driver on every launch | Nothing to fix in the script; the next run tries again. If it recurs nightly, the resolver on the box is flaky |
| `SessionNotCreatedException: cannot connect to chrome at 127.0.0.1:…` | Chrome exited at launch. Most often a leftover Chrome from a crashed earlier run, or too little memory | Check `ps -eo pid,args \| grep -i chrom \| grep -v grep` and kill stragglers. This version uses a fresh temporary profile each run, so a profile lock is not the cause |
| `ChromeDriver only supports Chrome version 156` | Chrome updated past the pinned `version_main=156` | Change `version_main` to the installed major version (`google-chrome --version`) |
| `selenium.common.exceptions.TimeoutException: … receiving message from renderer` | A wedged renderer, usually under memory pressure | Not retried by this version. Raise memory, or re-run |
| `WebDriverException: Message: tab crashed` | Chrome tab died — memory, or `/dev/shm` too small | Add `--disable-dev-shm-usage` to the `options.add_argument` block |
| `no items on the list - nothing to do (page title='503 - Service Unavailable Error')` | Amazon served an error page rather than the list. **The run exits 0**, so check the title in the log, not the exit code | Nothing was written or deleted. The next run usually succeeds |
| `no items on the list … (page title='Amazon Sign-In')` | The session was not established — the login or OTP step did not take | Run with `--debug` and look at steps 3–6 |
| `FAILED at login: no email field after 60s` | Amazon served a CAPTCHA, a device-approval page, or something else entirely | Open the `error_page.png` the script saved next to it in the log |
| `no OTP challenge (TimeoutException)` in the debug trace | Amazon did not ask for a code on this run, **or** the field was renamed | Check step 6's URL: if it is the list page, all is well; if it is still a sign-in page, the OTP field id needs updating (see the selector table) |
| `StaleElementReferenceException` on a delete click | The virtualised list re-rendered a row between finding the button and clicking it. The delete loop stops rather than spinning | The run carries on and still writes the items it read; the undeleted item simply stays on the Alexa list, so it will be picked up again next run |
| `NoKeyringError` | No usable keyring backend for the user cron runs as | Configure a backend (e.g. `keyrings.alt` file backend) or run as the desktop user whose keyring is unlocked |
| `gspread.exceptions.APIError: 403` | The spreadsheet has not been shared with the service account | Share it with the `…iam.gserviceaccount.com` address as Editor |
| `FileNotFoundError: /home/USER/scripts/gdrive.json` | The Google key is not where the script expects it | Save it at exactly that path |
| Nothing in the log at all | Cron is not running it: wrong absolute path, or the venv's interpreter | Run the exact cron command by hand |

### Selector reference

Amazon reshuffles this page periodically. These are the locators in use, and the values confirmed against `amazon.co.uk` with Chrome 156:

| Stage | Locator |
| --- | --- |
| Email field | `By.NAME, 'email'` (waited on); `#ap_email_login` (typed into) |
| Email submit | `#continue` (via `.submit()`) |
| Password field | `#ap_password` |
| Password submit | `#signInSubmit` |
| One-time code | `#ap_otp_code`, falling back to `#auth-mfa-otpcode` |
| One-time code submit | `#auth-signin-button`, falling back to submitting the form |
| List items | `.virtual-list .item-title` |
| Delete controls | `//button[contains(text(), 'Delete')]` |

When a rename breaks the run, the debug trace names the id that matched (`OTP field present (id=…)`), and step 7 logs the page title it actually got — which is usually enough to see whether the markup moved or the page is simply an error.

## Notes and limitations

- **Items are deleted from Alexa before they are written to the sheet.** That is the original order and it is preserved deliberately to keep the script simple, but it means a failure in the Sheets step happens *after* the list has already been emptied. The item names are in the log (`read N item(s): [...]`), so a failed write is recoverable from the log — but check for it. If you want the safer order, write first and delete second.
- **No retries.** A DNS blip, a crashed tab or a wedged renderer loses that hour; the next scheduled run starts from scratch. The debug trace makes the cause obvious when it happens.
- **A full login on every run.** No saved profile, so every run spends time on email, password and OTP, and every run is a fresh chance for Amazon to challenge it.
- **The TOTP code is generated before Chrome starts.** The 30-second window can expire if the driver download takes longer than that; the sign-in then fails at the OTP step. A slow first run after a Chrome update is the usual place to see this.
- **Undetected-chromedriver has not been updated since February 2024**, while Chrome ships a major version roughly every four weeks. `version_main` will need bumping periodically, and a future Chrome release may stop working entirely.
- Written and verified against **amazon.co.uk** with a UK account, Chrome 156 and Python 3.10. No test suite is committed.
- **This automates your own account.** Check that it is consistent with Amazon's Conditions of Use before relying on it, and be aware that repeated automated sign-ins are exactly the pattern their risk systems look for.
- Google's `oauth2client` is deprecated in favour of `google-auth`, and `base64` is imported but unused. Both are harmless and left alone to keep the script identical to the version that works.
