import os
import json
import time
import argparse
import pyotp
import gspread
from gspread_dataframe import set_with_dataframe
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import base64
import keyring as kr
from dotenv import load_dotenv
import undetected_chromedriver as uc  # Changed from standard selenium webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

RUN_STARTED = time.time()

# ---------------------------------------------------------------- diagnostics
# Two levels of output:
#   log()   - always printed: results, warnings and failures. This is what a
#             normal cron log should contain.
#   debug() - printed only with --debug: the step-by-step trace of the run.
# Turn the trace on with `--debug` on the command line, or set DEBUG = True here.
DEBUG = False


def log(message):
    """Always printed: results, warnings and failures."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def debug(message):
    """Printed only when the debug trace is on: the step-by-step detail."""
    if DEBUG:
        print(f"[{time.strftime('%H:%M:%S')}] (+{time.time() - RUN_STARTED:6.1f}s) {message}", flush=True)


def where(d):
    """url + title, without letting a wedged tab hide the original error."""
    try:
        return f"url={d.current_url} title={d.title!r}"
    except Exception as e:
        return f"url/title unreadable ({type(e).__name__}: {e})"


parser = argparse.ArgumentParser(description="Read the Alexa shopping list and add it to Google Sheets.")
parser.add_argument('--debug', action='store_true',
                    help='print the step-by-step trace (use when something goes wrong)')
DEBUG = parser.parse_args().debug

if DEBUG:
    log("debug trace is ON")

# Load environment variables from .env file
load_dotenv()
debug("start: loading credentials from keyring")

# Environment variables
secret = kr.get_password("shoplist", "aws_otp_secret")
amz_login = <AMAZON_ACCOUNT_EMAIL>
amz_password = kr.get_password("shoplist", "amazon")
debug(f"credentials: login={amz_login} otp_secret={'present' if secret else 'MISSING'} password={'present' if amz_password else 'MISSING'}")

# Using your updated, working login URL with the correct return_to parameter
amz_signin_url = (
    'https://www.amazon.co.uk/ap/signin?openid.pape.max_auth_age=3600'
    '&openid.return_to=https%3A%2F%2Fwww.amazon.co.uk%2Falexaquantum%2Fsp%2FalexaShoppingList%3Fref_%3Dlist_d_wl_ys_list_1'
    '&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select'
    '&openid.assoc_handle=gbflex&openid.mode=checkid_setup&language=en_GB'
    '&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select'
    '&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0'
)

amz_shoppinglist_url = 'https://www.amazon.co.uk/alexaquantum/sp/alexaShoppingList?ref_=list_d_wl_ys_list_1'
delete_after_download = True
found_pwd = False
list_items = False

# Generate OTP using pyotp
debug("generating OTP")
totp = pyotp.TOTP(secret)
token = totp.now()
debug(f"OTP generated at {time.strftime('%H:%M:%S')}")

# Undetected Chromedriver configuration
options = uc.ChromeOptions()
options.add_argument('--headless')  # Runs silently in the background
options.add_argument('--no-sandbox')
options.add_argument('--disable-gpu')

# Initialize the stealth driver
debug("creating the driver with uc.Chrome(version_main=156) - a stall here is the driver download/patch")
driver = uc.Chrome(options=options, version_main=156)
try:
    debug(f"driver created: browser {driver.capabilities.get('browserVersion', 'unknown')}")
except Exception as e:
    debug(f"driver created (version unreadable: {e})")
driver.set_page_load_timeout(60)
debug("page load timeout set to 60s")

# Start the automation process
try:
    # 1. Hit the base domain briefly to seed essential cookies
    debug("STEP 1: loading amazon.co.uk to seed cookies")
    driver.get('https://www.amazon.co.uk')
    debug(f"STEP 1 done: {where(driver)}")
    time.sleep(2)

    # 2. Open the working sign-in URL
    debug("STEP 2: loading the sign-in URL")
    driver.get(amz_signin_url)
    debug(f"STEP 2 done: {where(driver)}")

    try:
        # Wait for the initial email element to arrive
        debug("STEP 3: waiting up to 60s for the email field (By.NAME, 'email')")
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.NAME, 'email')))
        debug("STEP 3 done: email field is present")
    except Exception as e:
        # If it times out, capture the viewport state so you can see why it failed
        log(f"FAILED at login: no email field after 60s ({type(e).__name__}: {str(e).splitlines()[0]})")
        log(f"  at failure: {where(driver)}")
        try:
            driver.save_screenshot('error_page.png')
            log(f"  screenshot: {os.path.abspath('error_page.png')}")
        except Exception as shot_error:
            log(f"  screenshot also failed: {shot_error}")
        raise e

    # Determine login screen layout (Single-stage vs Two-stage authentication)
    debug("STEP 4: detecting the login layout")
    try:
        if driver.find_element(By.ID, 'ap_password'):
            found_pwd = True
    except Exception:
        found_pwd = False
    debug(f"STEP 4 done: single-stage login (password on the email page) = {found_pwd}")

    if found_pwd:
        debug("  filling email and password on one page")
        driver.find_element(By.ID, 'ap_email_login').send_keys(amz_login)
        driver.find_element(By.ID, 'ap_password').send_keys(amz_password)
        driver.find_element(By.ID, 'signInSubmit').click()
        debug("  clicked signInSubmit")
    else:
        debug("  two-stage login: submitting the email first")
        time.sleep(1)
        driver.find_element(By.ID, 'ap_email_login').send_keys(amz_login)
        driver.find_element(By.ID, 'continue').submit()
        debug("  submitted 'continue', waiting up to 60s for the password field")
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, 'ap_password')))
        debug("  password field is present")
        driver.find_element(By.ID, 'ap_password').send_keys(amz_password)
        driver.find_element(By.ID, 'signInSubmit').click()
        debug("  clicked signInSubmit")

    # Handle OTP Challenges
    debug("STEP 5: checking for an OTP challenge (up to 10s)")
    try:
        # Reduce wait here slightly; if OTP field isn't present after 10s, assume it's bypassed.
        # The OTP box is now id='ap_otp_code'; the previous id stays as a fallback.
        otp = WebDriverWait(driver, 10).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "#ap_otp_code, #auth-mfa-otpcode")))
        debug(f"  OTP field present (id={otp.get_attribute('id')}), submitting the generated code")
        otp.send_keys(token)
        try:
            driver.find_element(By.ID, 'auth-signin-button').click()
            debug("  clicked auth-signin-button")
        except Exception as e:
            debug(f"  auth-signin-button not found ({type(e).__name__}); submitting the OTP form instead")
            otp.submit()
            debug("  submitted the OTP form")
    except Exception as e:
        # No OTP container detected, proceeding to list page
        debug(f"STEP 5 done: no OTP challenge ({type(e).__name__}), continuing")

    # Ensure we are fully landed on the Alexa Shopping List page
    debug(f"STEP 6: landing on the list page (currently {where(driver)})")
    if driver.current_url != amz_shoppinglist_url:
        debug("  not on the list page yet; loading it")
        driver.get(amz_shoppinglist_url)
        debug(f"  list page loaded: {where(driver)}")

    debug("STEP 7: waiting up to 20s for list items (css '.virtual-list .item-title')")
    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".virtual-list .item-title")))
        list_items = True
    except Exception as e:
        list_items = False
        debug(f"STEP 7: no list items appeared within 20s ({type(e).__name__})")
        debug(f"  {where(driver)}")
    debug(f"STEP 7 done: list_items={list_items}")

    if list_items:
        # Extract the shopping list items
        debug("STEP 8: reading the items")
        item_titles = [item.text.strip() for item in driver.find_elements(By.CSS_SELECTOR, ".virtual-list .item-title")]
        debug(f"STEP 8 done: read {len(item_titles)} item(s)")
        log(f"read {len(item_titles)} item(s): {item_titles}")

        # Format the items as JSON
        json_formatted_items = json.dumps(item_titles, indent=2)

        # Delete items after download (if required)
        if delete_after_download and item_titles:
            debug(f"STEP 9: deleting {len(item_titles)} item(s) from the Alexa list")
            deleted = 0
            pass_number = 0
            while True:
                pass_number += 1
                time.sleep(0.5)
                delete_buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Delete')]")
                debug(f"  pass {pass_number}: {len(delete_buttons)} delete button(s) found")

                if not delete_buttons:
                    debug("  no delete buttons left")
                    break

                for button in delete_buttons:
                    try:
                        WebDriverWait(driver, 10).until(EC.element_to_be_clickable(button))
                        ActionChains(driver).move_to_element(button).click().perform()
                        deleted += 1
                        debug("  clicked one delete button")
                        break
                    except Exception as e:
                        log(f"delete click failed ({type(e).__name__}: {str(e).splitlines()[0]}); leaving the rest in place")
                        break
            if deleted != len(item_titles):
                log(f"deleted {deleted} of {len(item_titles)} item(s) from the Alexa list")
            else:
                log(f"deleted {deleted} item(s) from the Alexa list")

        # Debug: Display the JSON formatted list
        debug(json_formatted_items)

        # Add contents from shopping list to Google Sheets
        debug("STEP 10: writing to Google Sheets")
        credentials_path = os.environ['HOME'] + '/scripts/gdrive.json'
        sheet_name = 'Shopping List'
        main_worksheet = 'Shopping List'
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        debug(f"  credentials file: {credentials_path} (exists={os.path.exists(credentials_path)})")
        credentials = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
        client = gspread.authorize(credentials)
        debug("  authorised with Google")
        df = pd.DataFrame(item_titles, columns=["Items"])
        sheet = client.open(sheet_name).worksheet(main_worksheet)
        start_row = len(sheet.col_values(1)) + 1
        debug(f"  opened '{sheet_name}'!'{main_worksheet}', {len(df)} row(s) starting at row {start_row}")
        set_with_dataframe(sheet, df, start_row, 1, include_column_header=False)
        log(f"wrote {len(df)} row(s) to '{sheet_name}' at row {start_row}")
    else:
        # One line, but carrying the page title: an error page or a sign-in
        # redirect is visible here even with the debug trace switched off.
        try:
            title = driver.title
        except Exception as e:
            title = f"unreadable ({type(e).__name__})"
        log(f"no items on the list - nothing to do (page title={title!r})")
finally:
    debug("quitting the driver")
    driver.quit()
    debug("run finished")
