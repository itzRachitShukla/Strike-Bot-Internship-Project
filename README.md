# 🤖 Strike-Bot Internship Project

An automated Marketing & Agency Management Discord Bot built with **Discord Components V2 (`discord.ui.LayoutView`)** and real-time **Google Sheets API** synchronization.

---

## 🌟 Key Features

- **Discord Components V2 Dashboard**: Interactive card dashboard pinned in query channels (`#-query`, `#-queries`) with real-time timestamps, text-only buttons, and DM logger menus.
- **Worker Channel Claiming**: Workers click `Claim Channel` to bind their Discord User ID (`<@user_id>`) to their assigned query channel.
- **Automated Strike Tracking**: Monitors screen recording video submissions. Issues strikes for deadline breaches and tags the worker user directly.
- **7-Day Clean Streak Auto-Revocation**: Automatically revokes active strikes when 7 consecutive days pass without a new strike.
- **Admin Undo Control**: Admins can undo strikes via the `Undo Last Strike` button.
- **Audit Logging System**: 5 dedicated audit log channels (`Influencer Claims`, `Strike Events`, `Worker Add`, `Worker Remove`, `Worksheet Changes`) configured via `/setup-logs` with a 1-click **Auto-Setup** button.
- **Time Acceleration (`/speed_time`)**: Fast-forward bot time for testing (e.g. `/speed_time real_time_seconds: 60 bot_time: 12h`).

---

## 📋 Prerequisites

- **Python 3.10** or higher
- A **Discord Account** with permission to create bots
- A **Google Cloud Console Account** (free) with Google Sheets API enabled

---

## 🚀 Beginner-Friendly Setup Guide

### Step 1: Clone the Repository & Install Dependencies

1. Open your terminal / command prompt in your project folder:
   ```bash
   git clone https://github.com/YOUR_USERNAME/Strike-Bot-Internship-Project.git
   cd Strike-Bot-Internship-Project
   ```
2. Create and activate a Python virtual environment:
   - **Windows**:
     ```powershell
     python -m venv venv
     .\venv\Scripts\activate
     ```
   - **Mac/Linux**:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```
3. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

---

### Step 2: Create a Discord Bot & Get Your Bot Token

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** at the top right and enter a name (e.g. `Agency Strike Bot`).
3. In the left sidebar, click **Bot**.
4. Click **Reset Token** (or **Copy Token**) to copy your Bot Token. Save this for Step 4!
5. Scroll down to **Privileged Gateway Intents** and enable **Message Content Intent**:
   - Turn **ON** `MESSAGE CONTENT INTENT` (Required for video detection and prefix commands).
   - Click **Save Changes**.
6. In the left sidebar, go to **OAuth2** -> **URL Generator**:
   - Select scope: `bot`, `applications.commands`.
   - Select bot permissions: `Administrator` (or `Send Messages`, `Manage Messages`, `Embed Links`, `Manage Channels`, `Read Message History`).
   - Copy the generated URL at the bottom, paste it into your browser, and invite the bot to your Discord Server!

---

### Step 3: Create Google Service Account Credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g. `Agency Discord Bot`).
3. In the search bar at the top, search for **Google Sheets API** and click **Enable**.
4. Search for **Google Drive API** and click **Enable**.
5. In the left menu, go to **APIs & Services** -> **Credentials**.
6. Click **+ CREATE CREDENTIALS** at the top -> select **Service Account**.
7. Enter a name (e.g. `sheets-bot`), click **Create and Continue**, then click **Done**.
8. Click on your newly created Service Account email in the list.
9. Go to the **KEYS** tab at the top -> click **ADD KEY** -> select **Create new key**.
10. Choose **JSON** and click **Create**. A `.json` file will automatically download to your computer.
11. **Rename** the downloaded file to `credentials.json` and move it directly into the root folder of this project!
12. Open `credentials.json` and copy the `"client_email"` address (e.g. `sheets-bot@your-project.iam.gserviceaccount.com`).
13. Open your Google Sheet in your browser -> click **Share** at the top right -> paste the service account email address, give it **Editor** permissions, and click **Send**!

---

### Step 4: Configure Your `.env` File

1. In the root directory of this project, create a file named `.env` (or copy `.env.example` to `.env`).
2. Fill in your credentials:
   ```env
   # Discord Bot Token from Step 2
   DISCORD_TOKEN=your_discord_bot_token_here

   # Google Service Account JSON filename
   GOOGLE_SERVICE_ACCOUNT_FILE=credentials.json

   # Google Spreadsheet ID (Found in your Google Sheet URL between /d/ and /edit)
   # Example URL: https://docs.google.com/spreadsheets/d/1ZLxwVXcXIqHoNkBPzGIzqJWqKmK2Gpsjbtzjwa2xenY/edit
   SPREADSHEET_ID=1ZLxwVXcXIqHoNkBPzGIzqJWqKmK2Gpsjbtzjwa2xenY

   # Configurable Thresholds
   STRIKE_DEADLINE_HOURS=24
   REVOCATION_STREAK_DAYS=7
   AUDIT_INTERVAL_MINUTES=15
   ```

---

### Step 5: Run the Bot!

Run the bot with Python:
```bash
python main.py
```

Upon logging in, you will see output like this:
```text
==================================================
Bot is online as Agency Strike Bot#1234
==================================================
⚡ Instant synced 5 slash command(s) to guild 'Your Server'
Synced 5 global slash command(s): ['setup-logs', 'speed_time', 'stop_speed_time', 'claim', 'check']
Periodic strike audit task started (runs every 15.0 mins).
```

---

## 🛠️ Discord Commands Cheatsheet

| Command | Type | Description |
| :--- | :--- | :--- |
| `/setup-logs` | Slash | Opens interactive audit log channels setup. Includes **Auto-Setup** button to automatically create categories and channels! |
| `/claim <ig_link>` | Slash | Registers and claims an influencer Instagram URL in Google Sheets. |
| `/check <ig_link>` | Slash | Checks if an Instagram URL is already claimed in the database. |
| `/speed_time <sec> <time>` | Slash | Fast-forwards/accelerates bot time for testing (e.g. `/speed_time real_time_seconds: 60 bot_time: 12h`). |
| `/stop_speed_time` | Slash | Stops active time acceleration loop for the query channel. |
| `!sync` | Prefix | Instantly syncs all slash commands to your server (0-second delay). |
| `!claim <ig_link>` | Prefix | Prefix command alternative for claiming influencers. |
| `!check <ig_link>` | Prefix | Prefix command alternative for checking influencers. |

---

## 📁 Database Reset Helper

To clear out test data rows in your Google Sheet while keeping sheet headers intact:
```bash
python clear_database.py
```

---

## 🔒 Security Notice

Never share or commit your `.env` file or `credentials.json` file. The included `.gitignore` protects these sensitive files automatically.
