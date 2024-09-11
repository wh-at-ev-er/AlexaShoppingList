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
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

# Load environment variables from .env file
load_dotenv()


# Environment variables
secret =  kr.get_password("shoplist", "aws_otp_secret") # <AMAZON_SECRET>
amz_login = <AMAZON_ACCOUNT_EMAIL>
amz_password = kr.get_password("shoplist", "amazon") # <AMAZON_ACCOUNT_EMAIL>
amz_signin_url = ('https://www.amazon.co.uk/ap/signin?openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww'
                  '.amazon.co.uk%2Fgp%2Fcss%2Fhomepage.html%2Fref%3Dnav_ya_signin&openid.identity=http%3A%2F%2Fspecs'
                  '.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=gbflex&openid.mode=checkid_setup'
                  '&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http'
                  '%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0')
amz_shoppinglist_url = 'https://www.amazon.co.uk/alexaquantum/sp/alexaShoppingList?ref_=list_d_wl_ys_list_1'
log_level = False
delete_after_download = True
found_pwd = False
list_items = False

# Generate OTP using pyotp
totp = pyotp.TOTP(secret)
token = totp.now()

service = Service(executable_path=r'/usr/bin/chromedriver')
options = webdriver.ChromeOptions()
options.add_argument("enable-automation")
options.add_argument("--no-sandbox")
options.add_argument("--disable-extensions")
options.add_argument("--dns-prefetch-disable")
options.add_argument("--disable-gpu")
options.add_argument('--headless')
driver = webdriver.Chrome(service=service, options=options)
driver.set_page_load_timeout(60)

try:
    element_exists = False
    while not element_exists:
        parts = amz_signin_url.split('/')
        result = '/'.join(parts[:3])
        driver.get(result)
        time.sleep(1.5)
        driver.get(amz_signin_url)
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, 'ap_email')))
        element_exists = True

    try:
        if driver.find_element(By.ID, 'ap_password'):
            found_pwd = True
    except Exception as e:
        found_pwd = False

    if found_pwd:
        driver.find_element(By.ID, 'ap_email').send_keys(amz_login)
        driver.find_element(By.ID, 'ap_password').send_keys(amz_password)
        driver.find_element(By.ID, 'signInSubmit').click()
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, 'ap_email')))
    else:
        time.sleep(1)
        driver.find_element(By.ID, 'ap_email').send_keys(amz_login)
        driver.find_element(By.ID, 'continue').submit() #click()
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, 'ap_password')))
        driver.find_element(By.ID, 'ap_password').send_keys(amz_password)
        driver.find_element(By.ID, 'signInSubmit').click()

    # Handle OTP (if required)
    if WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, 'auth-mfa-otpcode'))):
        driver.find_element(By.ID, 'auth-mfa-otpcode').send_keys(token)
        driver.find_element(By.ID, 'auth-signin-button').click()

    # Navigate to Alexa Shopping List page
    driver.get(amz_shoppinglist_url)

    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".virtual-list .item-title")))
        list_items = True
    except Exception as e:
        list_items = False

    if list_items:
        # Extract the shopping list items
        item_titles = [item.text.strip() for item in driver.find_elements(By.CSS_SELECTOR, ".virtual-list .item-title")]

        # Format the items as JSON
        json_formatted_items = json.dumps(item_titles, indent=2)

        # Delete items after download (if required)
        if delete_after_download and item_titles:
            while True:
                # Locate all delete buttons using XPath
                time.sleep(0.5)
                delete_buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Delete')]")

                if not delete_buttons:
                    # No more buttons found, break the loop
                    break

                for button in delete_buttons:
                    try:
                        WebDriverWait(driver, 10).until(EC.element_to_be_clickable(button))
                        ActionChains(driver).move_to_element(button).click().perform()
                        # After clicking, break out of the loop to re-fetch the buttons
                        break
                    except Exception as e:
                        # If the button becomes stale or another error occurs, re-fetch the buttons
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
