import os
import json
import time
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

# Load environment variables from .env file
load_dotenv()

# Environment variables
secret =  kr.get_password("shoplist", "aws_otp_secret") # <AMAZON_SECRET>
amz_login = <AMAZON_ACCOUNT_EMAIL>
amz_password = kr.get_password("shoplist", "amazon") # <AMAZON_ACCOUNT_EMAIL>

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
log_level = False
delete_after_download = True
found_pwd = False
list_items = False

# Generate OTP using pyotp
totp = pyotp.TOTP(secret)
token = totp.now()

# Undetected Chromedriver configuration
options = uc.ChromeOptions()
options.add_argument('--headless')  # Runs silently in the background
options.add_argument('--no-sandbox')
options.add_argument('--disable-gpu')

# Initialize the stealth driver
driver = uc.Chrome(options=options, version_main=148)
driver.set_page_load_timeout(60)

# Start the automation process
try:
    # 1. Hit the base domain briefly to seed essential cookies
    driver.get('https://www.amazon.co.uk')
    time.sleep(2)

    # 2. Open the working sign-in URL
    driver.get(amz_signin_url)

    try:
        # Wait for the initial email element to arrive
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.NAME, 'email')))
    except Exception as e:
        # If it times out, capture the viewport state so you can see why it failed
        driver.save_screenshot('error_page.png')
        print("Timeout reached while waiting for 'ap_email'. Screenshot saved to 'error_page.png'")
        raise e

    # Determine login screen layout (Single-stage vs Two-stage authentication)
    try:
        if driver.find_element(By.ID, 'ap_password'):
            found_pwd = True
    except Exception:
        found_pwd = False

    if found_pwd:
        driver.find_element(By.ID, 'ap_email').send_keys(amz_login)
        driver.find_element(By.ID, 'ap_password').send_keys(amz_password)
        driver.find_element(By.ID, 'signInSubmit').click()
    else:
        time.sleep(1)
        driver.find_element(By.ID, 'ap_email').send_keys(amz_login)
        driver.find_element(By.ID, 'continue').submit()
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, 'ap_password')))
        driver.find_element(By.ID, 'ap_password').send_keys(amz_password)
        driver.find_element(By.ID, 'signInSubmit').click()

    # Handle OTP Challenges
    try:
        # Reduce wait here slightly; if OTP field isn't present after 10s, assume it's bypassed
        if WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, 'auth-mfa-otpcode'))):
            driver.find_element(By.ID, 'auth-mfa-otpcode').send_keys(token)
            driver.find_element(By.ID, 'auth-signin-button').click()
    except Exception:
        # No OTP container detected, proceeding to list page
        pass

    # Ensure we are fully landed on the Alexa Shopping List page
    if driver.current_url != amz_shoppinglist_url:
        driver.get(amz_shoppinglist_url)

    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".virtual-list .item-title")))
        list_items = True
    except Exception:
        list_items = False

    if list_items:
        # Extract the shopping list items
        item_titles = [item.text.strip() for item in driver.find_elements(By.CSS_SELECTOR, ".virtual-list .item-title")]

        # Format the items as JSON
        json_formatted_items = json.dumps(item_titles, indent=2)

        # Delete items after download (if required)
        if delete_after_download and item_titles:
            while True:
                time.sleep(0.5)
                delete_buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Delete')]")

                if not delete_buttons:
                    break

                for button in delete_buttons:
                    try:
                        WebDriverWait(driver, 10).until(EC.element_to_be_clickable(button))
                        ActionChains(driver).move_to_element(button).click().perform()
                        break
                    except Exception:
                        break

        # Debug: Display the JSON formatted list
        if log_level:
            print(json_formatted_items)

        # Add contents from shopping list to Google Sheets
        credentials_path = os.environ['HOME'] + '/scripts/gdrive.json'
        sheet_name = 'Shopping List'
        main_worksheet = 'Shopping List'
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        credentials = ServiceAccountCredentials.from_json_keyfile_name(credentials_path, scope)
        client = gspread.authorize(credentials)
        df = pd.DataFrame(item_titles, columns=["Items"])
        sheet = client.open(sheet_name).worksheet(main_worksheet)
        set_with_dataframe(sheet, df, len(sheet.col_values(1)) + 1, 1, include_column_header=False)

finally:
    driver.quit()
