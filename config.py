import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")

# Testing & Timing Threshold Configs (Defaults: 24h video deadline, 7 days revocation streak, 15 min audit)
# For fast live testing, change these in .env (e.g. STRIKE_DEADLINE_HOURS=0.05 for 3 minutes)
STRIKE_DEADLINE_HOURS = float(os.getenv("STRIKE_DEADLINE_HOURS", "24.0"))
REVOCATION_STREAK_DAYS = float(os.getenv("REVOCATION_STREAK_DAYS", "7.0"))
AUDIT_INTERVAL_MINUTES = float(os.getenv("AUDIT_INTERVAL_MINUTES", "15.0"))
