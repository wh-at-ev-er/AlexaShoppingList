# AlexaShoppingList
Scrape Alexa Shopping List and send it to Google Sheets

**Google Sheets Credentials** 
Log into the [Google Developers Console](https://console.developers.google.com/) with the Google account whose spreadsheets you want to access. Create (or select) a project and enable the Drive API and Sheets API (under Google Apps APIs).

Go to the Credentials for your project and create New credentials > OAuth client ID > of type Other. In the list of your OAuth 2.0 client IDs click Download JSON for the Client ID you just created. Save the file as client_secrets.json in your home directory (user directory).

**How to get your OTP App Secret from Amazon <YOUR_OTP_APP_SECRET>:**
If you don't have 2-step verification enable:
1. Login to Amazon https://www.amazon.co.uk/
2. Go to Your Account => Login & Security and click on "Turn On" under 2-step verification
3. Select the Authentication App
4. Click on "Can't scan the barcode" and save the Key (13 sets of 4 characters each)
5. Remove the spaces of the Key (you will have something like this "ASDMASDFMSKDMKSFMKLASDDADABB6JNRNF7WFEHQW23H238R7843")

**If you already have 2-step verification enabled via Authenticator App:**
1. Login to Amazon https://www.amazon.co.uk/
2. Go to Your Account => Login & Security and click on "Manage" under 2-step verification
3. Under Authenticator App, click on Add New App
4. Click on "Can't scan the barcode" and save the Key (13 sets of 4 characters each)
5. Remove the spaces of the Key (you will have something like this "ASDMASDFMSKDMKSFMKLASDDADABB6JNRNF7WFEHQW23H238R7843")

**If you already have 2-step verification enabled via SMS please change to Authenticator app and follow the above steps.**

Amazon Password & Secret Key are stored using [Python Keyring](https://pypi.org/project/keyring/)

Amazon Sign-In & Shopping Cart URL should be updated to that of the country you use. 

Google Chrome & matching ChromeDriver need to be present
