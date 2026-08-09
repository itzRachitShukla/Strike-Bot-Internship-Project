import asyncio
import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_SERVICE_ACCOUNT_FILE, SPREADSHEET_ID

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def clear_google_sheets_database():
    print(f"Connecting to Google Spreadsheet ID: {SPREADSHEET_ID}...")
    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    sheets_to_clear = [
        ("Influencers", ["Instagram Link", "Claimed By", "Claimed At", "Channel Link"]),
        ("Staff Strikes", ["Worker Username", "Worker User ID", "Channel ID", "Channel Link", "Active Strikes", "Strike 1 Date", "Strike 2 Date", "Strike 3 Date", "Last Video Date"]),
        ("Daily DM Logs", ["Worker Username", "Channel ID", "Channel Link", "Day 1 DMs", "Day 2 DMs", "Day 3 DMs", "Day 4 DMs", "Day 5 DMs", "Day 6 DMs", "Day 7 DMs", "Total DMs"])
    ]

    for title, headers in sheets_to_clear:
        try:
            ws = sh.worksheet(title)
            print(f"Clearing worksheet '{title}'...")
            ws.clear()
            ws.append_row(headers)
            print(f" Reset '{title}' headers successfully.")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=title, rows=100, cols=len(headers))
            ws.append_row(headers)
            print(f" Created and set headers for '{title}'.")
        except Exception as e:
            print(f"Error clearing '{title}': {e}")

    print(" Google Sheets Database successfully cleared!")

if __name__ == "__main__":
    clear_google_sheets_database()
