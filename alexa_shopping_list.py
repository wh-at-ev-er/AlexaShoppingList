#!/usr/bin/env python3
"""
Read the Alexa shopping list from amazon.co.uk and push it into Google Sheets.

Corrected version of the original script. The changes are all things that made
the old version fail silently or drift out of date:

  * Chrome major version is detected instead of hard-coded (Chrome now ships a
    new major every two weeks, so a pin goes stale fast).
  * The TOTP code is generated at the moment it is typed, not at import time -
    the old code minted it before Chrome had even started.
  * The session is kept in a persistent Chrome profile, so the login (and with
    it the OTP dance) only happens when Amazon actually asks for it.
  * Every stage logs what it matched, and dumps the page HTML on failure, so a
    selector change shows up as a message instead of a silent no-op.
  * Items are written to Sheets *before* they are deleted from Amazon, so a
    failed delete can no longer cost you the data. The delete loop tolerates
    the virtual list re-rendering mid-click and verifies the count actually
    falls, instead of treating a stale element as a hard failure.
  * Google credentials use google-auth; oauth2client is archived.

First run:  python3 alexa_shopping_list.py --dump-html
Useful flags: --no-headless (under xvfb-run), --dry-run, --force-login
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pyotp
import undetected_chromedriver as uc
from dotenv import load_dotenv
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By

try:
    import keyring as kr
except Exception:  # keyring is optional; env vars are the fallback
    kr = None

load_dotenv()

# ------------------------------------------------------------------ config --

AMZ_LOGIN = os.getenv("AMAZON_LOGIN", <AMAZON_ACCOUNT_EMAIL>)
BASE_URL = "https://www.amazon.co.uk"

# Verified 2026-10-27 against amazon.co.uk:
#   * it returns 200 and redirects to sign-in when signed out (a bogus path
#     under the same prefix returns 404, so the endpoint itself still exists)
#   * being signed out, it redirects to
#     /ap/signin?...openid.return_to=<this URL>...&openid.assoc_handle=gbflex
# So there is no need to hard-code the sign-in URL any more: we just ask for
# the list and let Amazon send us wherever it wants us to sign in.
LIST_URL = (
    "https://www.amazon.co.uk/alexaquantum/sp/alexaShoppingList"
    "?ref_=list_d_wl_ys_list_1"
)

CREDENTIALS_PATH = Path(os.getenv("GDRIVE_JSON", Path.home() / "scripts" / "gdrive.json"))
SHEET_NAME = os.getenv("SHEET_NAME", "Shopping List")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Shopping List")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PROFILE_DIR = Path(os.getenv("AMZ_PROFILE_DIR", Path.home() / ".cache" / "amz-profile"))
DEBUG_DIR = Path(os.getenv("AMZ_DEBUG_DIR", "/tmp/amz-debug"))

LOGIN_TIMEOUT = int(os.getenv("AMZ_LOGIN_TIMEOUT", "60"))
DELETE_AFTER_DOWNLOAD = os.getenv("DELETE_AFTER_DOWNLOAD", "1") != "0"

# Locator candidates, most specific first. The first visible+enabled match wins
# and is logged, so when Amazon reshuffles its markup you can see which one
# stopped working.
EMAIL_FIELDS = [
    (By.ID, "ap_email_login"),
    (By.NAME, "email"),
    (By.ID, "ap_email"),
]
PASSWORD_FIELDS = [
    (By.ID, "ap_password"),
    (By.NAME, "password"),
    (By.CSS_SELECTOR, "input[type='password']"),
]
# NOTE (confirmed by a real run 2026-10-28): the live OTP input is
# name="otpCode". The old script waited on By.ID 'auth-mfa-otpcode', which
# never matched, so its `except: pass` skipped the code entirely and the run
# continued unauthenticated. Keep the other candidates as fallbacks.
OTP_FIELDS = [
    (By.NAME, "otpCode"),
    (By.ID, "auth-otp-input"),
    (By.ID, "auth-mfa-otpcode"),
    (By.CSS_SELECTOR, "input[autocomplete='one-time-code']"),
]
EMAIL_SUBMITS = [
    (By.ID, "continue"),
    (By.NAME, "continue"),
    (By.CSS_SELECTOR, "input[type=submit]"),
    (By.CSS_SELECTOR, "button[type=submit]"),
]
PASSWORD_SUBMITS = [
    (By.ID, "signInSubmit"),
    (By.ID, "auth-signin-button"),
    (By.CSS_SELECTOR, "input[type=submit]"),
]
OTP_SUBMITS = [
    (By.ID, "auth-signin-button"),
    (By.ID, "auth-submit-otp"),
    (By.CSS_SELECTOR, "input[type=submit]"),
]
# .virtual-list .item-title confirmed working (11 items read) 2026-10-28.
ITEM_TITLES = [
    (By.CSS_SELECTOR, ".virtual-list .item-title"),
    (By.CSS_SELECTOR, ".item-title"),
    (By.CSS_SELECTOR, "[class*='item-title']"),
]
# An empty shopping list is the normal case on most mornings, so it must be a
# quiet success rather than an error. Several signals are checked before we give
# up on finding items.
EMPTY_MARKERS = [
    "is empty",
    "list is empty",
    "your list is empty",
    "no items",
    "add an item",
    "add items",
    "nothing here",
    "nothing in your list",
]
# The app shell renders before its data does, so the presence of the list
# container with no rows inside it is what an empty list looks like.
ITEM_CONTAINERS = [
    (By.CSS_SELECTOR, ".virtual-list"),
    (By.CSS_SELECTOR, "[class*='virtual-list']"),
    (By.CSS_SELECTOR, "[class*='item-list']"),
    (By.CSS_SELECTOR, "[class*='shopping-list']"),
]
EMPTY_SETTLE = 4.0  # seconds to let rows appear once the container is rendered
# Per-row controls first, scoped to the list, so we never hit a toolbar-wide
# "Delete" button that would silently do nothing with nothing selected.
DELETE_BUTTONS = [
    (By.XPATH, "//*[contains(@class,'virtual-list')]//button[contains(., 'Delete')]"),
    (By.CSS_SELECTOR, "button[aria-label*='Delete' i]"),
    (By.CSS_SELECTOR, "button[aria-label*='Remove' i]"),
    (By.XPATH, "//button[contains(., 'Delete')]"),
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get_secret(kr_name: str | None, env_name: str) -> str:
    """keyring first, env var fallback (a headless server often has no keyring
    backend at all, which fails with a confusing NoKeyringError)."""
    if kr is not None and kr_name:
        try:
            value = kr.get_password("shoplist", kr_name)
            if value:
                return value
        except Exception as exc:  # noqa: BLE001 - fall through to env
            log(f"keyring lookup for {kr_name!r} failed ({exc}); trying {env_name}")
    value = os.getenv(env_name)
    if not value:
        raise SystemExit(f"no secret for {kr_name or env_name}: set {env_name}")
    return value


# ----------------------------------------------------------------- helpers --


def dump(driver, stage: str) -> Path:
    """Save everything you need to see why a stage failed."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    png = DEBUG_DIR / f"{stamp}-{stage}.png"
    html = DEBUG_DIR / f"{stamp}-{stage}.html"
    try:
        driver.save_screenshot(str(png))
        html.write_text(driver.page_source, encoding="utf-8")
        log(f"dumped {stage}: url={driver.current_url} title={driver.title!r}")
        log(f"  {png}\n  {html}")
    except WebDriverException as exc:
        log(f"could not dump {stage}: {exc}")
    return html


def find_first(driver, locators, timeout=10.0, poll=0.25):
    """Return (locator, element) for the first visible+enabled match.

    Requires visibility so we never type into Amazon's hidden password
    autofill-hint input, which also carries name="password".
    """
    deadline = time.time() + timeout
    while True:
        for loc in locators:
            try:
                elements = driver.find_elements(*loc)
            except WebDriverException:
                continue
            for el in elements:
                try:
                    if el.is_displayed() and el.is_enabled():
                        return loc, el
                except StaleElementReferenceException:
                    continue
        if time.time() >= deadline:
            return None, None
        time.sleep(poll)


def click_first(driver, locators, what: str, timeout=15.0):
    loc, el = find_first(driver, locators, timeout=timeout)
    if el is None:
        dump(driver, f"no-{what.replace(' ', '-')}")
        raise RuntimeError(f"could not find {what}; tried {locators}")
    try:
        ActionChains(driver).move_to_element(el).click().perform()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", el)  # overlay / off-screen fallback
    log(f"clicked {what} ({loc[0]}={loc[1]!r})")


def needs_login(driver) -> bool:
    url = driver.current_url
    if "/ap/signin" in url or "/ax/claim" in url or "/ap/cvf" in url:
        return True
    return find_first(driver, EMAIL_FIELDS, timeout=1.0)[1] is not None


def chrome_major_version():
    """Detect the installed Chrome major version instead of hard-coding it."""
    candidates = [
        os.getenv("CHROME_BIN"),
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "/usr/bin/google-chrome",
        "/snap/bin/chromium",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        exe = shutil.which(candidate) or (candidate if os.path.exists(candidate) else None)
        if not exe:
            continue
        try:
            out = subprocess.check_output(
                [exe, "--version"], stderr=subprocess.DEVNULL, timeout=15
            ).decode()
        except Exception:  # noqa: BLE001
            continue
        match = re.search(r"(\d+)\.\d+\.\d+\.\d+", out)
        if match:
            log(f"found {out.strip()} at {exe}")
            return int(match.group(1))
    log("could not detect Chrome; letting undetected-chromedriver pick the driver")
    return None


def build_driver(headless: bool, major):
    options = uc.ChromeOptions()
    # Return as soon as the DOM is ready instead of waiting for every image and
    # ad on Amazon's pages to finish loading. The app is a single-page React
    # bundle that renders after load anyway, and every wait in this script polls
    # for elements, so there is nothing to gain from waiting for full load.
    options.page_load_strategy = "eager"
    if headless:
        # undetected-chromedriver's own headless flag leaves "HeadlessChrome"
        # in the user agent, which is a giveaway; override it with the version
        # we actually detected. For a fully convincing session run under
        # xvfb-run and pass --no-headless.
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")  # small /dev/shm on VPSes
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if headless and major:
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        )

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    kwargs = dict(
        options=options,
        user_data_dir=str(PROFILE_DIR),
        log_path=str(DEBUG_DIR / "uc.log"),
    )
    if major:
        kwargs["version_main"] = major
    driver = uc.Chrome(**kwargs)
    driver.set_page_load_timeout(60)
    log(
        "browser "
        f"{driver.capabilities.get('browserVersion')} / driver "
        f"{driver.capabilities.get('chrome', {}).get('chromedriverVersion', '').split()[0]}"
    )
    return driver


# ------------------------------------------------------------------- login --


def sign_in(driver, password: str, otp_secret: str) -> None:
    loc, email_el = find_first(driver, EMAIL_FIELDS, timeout=LOGIN_TIMEOUT)
    if email_el is None:
        dump(driver, "no-email-field")
        raise RuntimeError(
            "sign-in page never showed an email field - if this page is a "
            "CAPTCHA or a passkey prompt, the dumped HTML will show it"
        )
    log(f"email field ({loc[0]}={loc[1]!r}); submitting {AMZ_LOGIN}")

    # Some Amazon layouts put email and password on one page.
    same_page_pwd = find_first(driver, PASSWORD_FIELDS, timeout=1.0)[1]
    email_el.send_keys(AMZ_LOGIN)
    if same_page_pwd is not None:
        same_page_pwd.send_keys(password)
        click_first(driver, PASSWORD_SUBMITS, "password submit")
    else:
        click_first(driver, EMAIL_SUBMITS, "email submit")
        loc, pwd_el = find_first(driver, PASSWORD_FIELDS, timeout=20)
        if pwd_el is None:
            dump(driver, "no-password-field")
            raise RuntimeError(
                "no password field appeared. Amazon is likely asking for a "
                "passkey, a device approval or a CAPTCHA instead - check the "
                "dumped HTML"
            )
        log(f"password field ({loc[0]}={loc[1]!r})")
        pwd_el.send_keys(password)
        click_first(driver, PASSWORD_SUBMITS, "password submit")

    loc, otp_el = find_first(driver, OTP_FIELDS, timeout=20)
    if otp_el is None:
        log("no OTP prompt this time")
        return

    # Generated here, immediately before it is used. The old script called
    # totp.now() at import time, so a slow Chrome start could burn the 30s TOTP
    # window and the code would be rejected as expired.
    token = pyotp.TOTP(otp_secret).now()
    log(f"OTP field ({loc[0]}={loc[1]!r}); submitting a freshly generated code")
    otp_el.send_keys(token)
    click_first(driver, OTP_SUBMITS, "OTP submit")


# -------------------------------------------------------------- list + sheets


def first_present(driver, locators):
    """Return the first locator that matches anything, or None."""
    for loc in locators:
        try:
            if driver.find_elements(*loc):
                return loc
        except WebDriverException:
            continue
    return None


def read_items(driver, timeout=25.0, settle=EMPTY_SETTLE):
    """Return (items, matched_locator); ([], None) means the list is empty.

    An empty list is normal, not an error, so this returns quietly once it is
    confident. Confidence comes from any of: the page saying so, or the list
    container being rendered with no rows in it.
    """
    deadline = time.time() + timeout
    container_since = None

    while time.time() < deadline:
        for loc in ITEM_TITLES:
            try:
                elements = [e for e in driver.find_elements(*loc) if e.text.strip()]
            except WebDriverException:
                continue
            if elements:
                items = []
                for el in elements:
                    text = el.text.strip()
                    if text and text not in items:
                        items.append(text)
                log(f"read {len(items)} item(s) using {loc[0]}={loc[1]!r}")
                return items, loc

        try:
            body = driver.find_element(By.TAG_NAME, "body").text.lower()
        except WebDriverException:
            body = ""
        if any(marker in body for marker in EMPTY_MARKERS):
            log("the page says the list is empty")
            return [], None

        # The shell renders before its data, so don't trust an immediate
        # absence: give the rows a moment once the container is on screen.
        if first_present(driver, ITEM_CONTAINERS):
            if container_since is None:
                container_since = time.time()
            elif time.time() - container_since >= settle:
                log(f"list container rendered with no rows in {settle:.0f}s - list is empty")
                return [], None
        else:
            container_since = None
        time.sleep(0.5)

    # Ran out of time with nothing found. Treat it as empty, but say so loudly
    # and leave a dump: this is also exactly what markup drift looks like, and
    # the dump is how you tell the difference.
    dump(driver, "no-list-items")
    log(
        f"WARNING: no items found after {timeout:.0f}s. Treated as an empty list, "
        "but if items were on the list, check the dump above"
    )
    return [], None


def brief(exc: Exception) -> str:
    """Selenium messages embed a whole driver stack trace; only the first line
    is useful in the log."""
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


def item_count(driver) -> int:
    """Rows currently rendered in the list (0 once it is empty)."""
    for loc in ITEM_TITLES:
        try:
            return len([e for e in driver.find_elements(*loc) if e.text.strip()])
        except WebDriverException:
            continue
    return 0


def click_delete_once(driver) -> str:
    """Click one delete control. Returns 'clicked', 'gone' or 'retry'.

    A stale element reference here is expected rather than fatal: the list
    animates rows out, so the node we resolved gets replaced between finding
    and clicking. Re-find and retry instead of giving up.
    """
    _, button = find_first(driver, DELETE_BUTTONS, timeout=3)
    if button is None:
        return "gone"
    try:
        ActionChains(driver).move_to_element(button).click().perform()
        return "clicked"
    except WebDriverException as exc:
        log(f"click retry ({brief(exc)})")

    # Re-resolve the control and fall back to a plain JS click; the handle we
    # held is dead once the row has been re-rendered.
    _, fresh = find_first(driver, DELETE_BUTTONS, timeout=2)
    if fresh is None:
        return "gone"
    try:
        driver.execute_script("arguments[0].click();", fresh)
        return "clicked"
    except WebDriverException as exc:
        log(f"js click retry ({brief(exc)})")
        return "retry"


def delete_items(driver, expected: int) -> int:
    """Delete every row. Returns how many items were left on the list.

    Bounded by a deadline and a stall counter rather than `while True`, and it
    verifies the item count actually falls instead of assuming each click
    worked.
    """
    budget = max(45, expected * 20)
    deadline = time.time() + budget
    stalled = 0
    log(f"deleting {expected} item(s) from the Alexa list")

    while time.time() < deadline:
        remaining = item_count(driver)
        if remaining == 0:
            log("list is empty; all items deleted")
            return 0

        outcome = click_delete_once(driver)

        if outcome == "gone":
            # No control left to click: either the list is finished emptying or
            # a dialog/overlay is in the way.
            time.sleep(1.0)
            if item_count(driver) == 0:
                log("list is empty; all items deleted")
                return 0
            stalled += 1
            if stalled >= 3:
                log(f"no delete control found but {remaining} item(s) remain")
                return remaining
            continue

        if outcome == "retry":
            stalled += 1
            if stalled >= 5:
                log(f"gave up clicking delete controls; {remaining} item(s) remain")
                return remaining
            time.sleep(0.5)
            continue

        # A click landed - wait for the row count to actually drop.
        stalled = 0
        waited = 0.0
        while waited < 10 and item_count(driver) >= remaining:
            time.sleep(0.5)
            waited += 0.5

    left = item_count(driver)
    log(f"delete loop hit its {budget}s budget with {left} item(s) left")
    return left


def push_to_sheet(items: list[str]) -> int:
    import gspread
    from google.oauth2.service_account import Credentials
    from gspread_dataframe import set_with_dataframe

    if not CREDENTIALS_PATH.exists():
        raise SystemExit(f"credentials not found: {CREDENTIALS_PATH}")

    creds = Credentials.from_service_account_file(str(CREDENTIALS_PATH), scopes=SCOPES)
    sheet = gspread.authorize(creds).open(SHEET_NAME).worksheet(WORKSHEET_NAME)
    start_row = len(sheet.col_values(1)) + 1
    frame = pd.DataFrame(items, columns=["Items"])
    set_with_dataframe(
        sheet, frame, start_row, 1, include_column_header=False, include_index=False
    )
    log(f"wrote {len(items)} row(s) to '{SHEET_NAME}'!{WORKSHEET_NAME} at row {start_row}")
    return start_row


# -------------------------------------------------------------------- main --


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-headless", action="store_true", help="show the browser (use under xvfb-run)")
    parser.add_argument("--force-login", action="store_true", help="ignore the saved session")
    parser.add_argument("--dry-run", action="store_true", help="read only: no Sheets write, no deletes")
    parser.add_argument("--dump-html", action="store_true", help="save the list page HTML even on success")
    args = parser.parse_args()

    password = get_secret("amazon", "AMAZON_PASSWORD")
    otp_secret = get_secret("aws_otp_secret", "AMAZON_OTP_SECRET")

    headless = not args.no_headless
    major = chrome_major_version()
    driver = build_driver(headless, major)
    try:
        driver.get(LIST_URL)

        if args.force_login:
            log("--force-login: discarding the saved session")
            driver.delete_all_cookies()
            driver.get(LIST_URL)

        if needs_login(driver):
            log("signed out - going through the login flow")
            # Seed cookies on the homepage first, as the sign-in handshake
            # expects a session. Only worth paying for on a run that logs in.
            driver.get(BASE_URL)
            time.sleep(1)
            driver.get(LIST_URL)
            sign_in(driver, password, otp_secret)
            time.sleep(2)
            if needs_login(driver):
                dump(driver, "still-signed-out")
                raise RuntimeError(
                    "still signed out after login - check the dumped HTML for a "
                    "CAPTCHA, passkey prompt or rejected one-time code"
                )
            if "alexaShoppingList" not in driver.current_url:
                driver.get(LIST_URL)
        else:
            log("existing session still valid; skipping login")

        items, _ = read_items(driver)
        if args.dump_html:
            dump(driver, "list-page")
        if not items:
            log("shopping list is empty - nothing to do")
            return 0

        print(json.dumps(items, indent=2))

        if args.dry_run:
            log("dry run: not writing to Sheets and not deleting")
            return 0

        # Data first, deletion second. The old order meant a stuck delete loop
        # could hang before anything reached the spreadsheet.
        push_to_sheet(items)
        if DELETE_AFTER_DOWNLOAD:
            left = delete_items(driver, len(items))
            if left:
                # Not fatal: the items are safely in the spreadsheet already.
                log(f"WARNING: {left} item(s) are still on the Alexa list")
        else:
            log("DELETE_AFTER_DOWNLOAD is off; leaving the list untouched")
        return 0
    finally:
        try:
            driver.quit()
        except Exception as exc:  # noqa: BLE001
            log(f"driver.quit() complained: {exc}")


if __name__ == "__main__":
    sys.exit(main())
