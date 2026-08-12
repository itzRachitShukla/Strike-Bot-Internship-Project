import asyncio
import os
import re
import time
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_SERVICE_ACCOUNT_FILE, SPREADSHEET_ID

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def normalize_link(url: str) -> str:
    """Clean and normalize profile/post URL or handle for comparison."""
    if not url:
        return ""
    url = url.strip().split("?")[0].rstrip("/")
    if not url.startswith("http://") and not url.startswith("https://") and ("/" in url or "." in url):
        url = "https://" + url
    return url.lower()

normalize_ig_link = normalize_link

def format_datetime_custom(dt_val) -> str:
    """Formats a datetime object or date string as '26 Jun 2:30 pm'."""
    if not dt_val:
        return ""
    if isinstance(dt_val, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d %b %I:%M %p"):
            try:
                clean_str = dt_val.replace(" IST", "").replace(" UTC", "").strip()
                dt_val = datetime.strptime(clean_str, fmt)
                break
            except Exception:
                pass
        if isinstance(dt_val, str):
            return dt_val

    hour_12 = dt_val.strftime("%I").lstrip("0")
    if not hour_12:
        hour_12 = "12"
    return f"{dt_val.day} {dt_val.strftime('%b')} {hour_12}:{dt_val.strftime('%M')} {dt_val.strftime('%p').lower()}"

class SheetsManager:
    def __init__(self, json_file=GOOGLE_SERVICE_ACCOUNT_FILE, spreadsheet_id=SPREADSHEET_ID):
        self.json_file = json_file
        self.spreadsheet_id = spreadsheet_id
        self._gc = None
        self.bot = None
        self._headers_validated = set()

    def set_bot(self, bot):
        self.bot = bot

    def _get_client(self, force_refresh=False):
        if self._gc is None or force_refresh:
            if not os.path.exists(self.json_file):
                raise FileNotFoundError(f"Service account file '{self.json_file}' not found.")
            creds = Credentials.from_service_account_file(self.json_file, scopes=SCOPES)
            self._gc = gspread.authorize(creds)
        return self._gc

    def _execute_with_retry(self, func, *args, **kwargs):
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                print(f" Google Sheets API attempt {attempt}/{max_retries} warning: {e}")
                if attempt == max_retries:
                    raise e
                time.sleep(1.5 * attempt)
                try:
                    self._get_client(force_refresh=True)
                except Exception:
                    pass

    def _get_worksheet(self, title: str, default_headers: list):
        gc = self._get_client()
        sh = gc.open_by_key(self.spreadsheet_id)
        try:
            ws = sh.worksheet(title)
            if title not in self._headers_validated:
                current_headers = ws.row_values(1)
                if current_headers != default_headers:
                    end_col_a1 = gspread.utils.rowcol_to_a1(1, len(default_headers))
                    ws.update(f"A1:{end_col_a1}", [default_headers], value_input_option="USER_ENTERED")
                self._headers_validated.add(title)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=title, rows=100, cols=len(default_headers))
            ws.append_row(default_headers, value_input_option="USER_ENTERED")
            self._headers_validated.add(title)
        return ws

    def _safe_get_records(self, ws, expected_headers: list):
        """Safely gets all records from a worksheet, handling duplicate or empty header columns cleanly."""
        try:
            return ws.get_all_records(expected_headers=expected_headers)
        except Exception:
            values = ws.get_all_values()
            if not values or len(values) < 2:
                return []
            header_row = [str(h).strip() for h in values[0]]
            records = []
            for row in values[1:]:
                row_dict = {}
                for idx, h in enumerate(expected_headers):
                    if idx < len(row):
                        row_dict[h] = row[idx]
                    else:
                        row_dict[h] = ""
                records.append(row_dict)
            return records

    # --- Influencers Logic ---

    def _check_influencer_sync(self, link: str, platform: str = "Instagram"):
        clean_target = normalize_link(link)
        headers = ["Social Link", "Referred By", "Channel Link", "Timestamp", "GEO", "Platform"]
        ws = self._get_worksheet("Influencers", headers)
        records = self._safe_get_records(ws, headers)
        for row in records:
            existing_link = normalize_link(str(row.get("Social Link", "") or row.get("Link / Handle", "") or row.get("Instagram Link", "")))
            if existing_link == clean_target:
                return {
                    "platform": row.get("Platform") or platform,
                    "ig_link": row.get("Social Link") or row.get("Link / Handle") or row.get("Instagram Link"),
                    "claimed_by": row.get("Referred By") or row.get("Claimed By"),
                    "claimed_at": row.get("Timestamp") or row.get("Claimed At"),
                    "channel_link": row.get("Channel Link"),
                    "geo": row.get("GEO", "N/A")
                }
        return None

    async def check_influencer(self, link: str, platform: str = "Instagram"):
        return await asyncio.to_thread(self._execute_with_retry, self._check_influencer_sync, link, platform)

    def _register_influencer_sync(self, link: str, referrer: str, channel_link: str, platform: str = "Instagram", geo: str = "US"):
        clean_target = normalize_link(link)
        headers = ["Social Link", "Referred By", "Channel Link", "Timestamp", "GEO", "Platform"]
        ws = self._get_worksheet("Influencers", headers)
        now_str = format_datetime_custom(datetime.now(timezone.utc))
        ws.append_row([clean_target, referrer, channel_link, now_str, geo, platform])
        
        if self.bot:
            from logger_service import logger_service
            asyncio.run_coroutine_threadsafe(
                logger_service.log_worksheet_change(
                    self.bot, None, "Influencers", f"Registered {platform} {clean_target} ({geo})", f"Referred by {referrer}", channel_link=channel_link
                ),
                self.bot.loop
            )
            
        return {
            "platform": platform,
            "ig_link": clean_target,
            "claimed_by": referrer,
            "claimed_at": now_str,
            "channel_link": channel_link,
            "geo": geo
        }

    async def register_influencer(self, link: str, referrer: str, channel_link: str, platform: str = "Instagram", geo: str = "US"):
        return await asyncio.to_thread(self._execute_with_retry, self._register_influencer_sync, link, referrer, channel_link, platform, geo)

    # --- Staff Strikes & Audit Log Logic ---

    def _get_all_staff_records_sync(self):
        headers = [
            "Worker Username", "Worker User ID", "Channel ID", "Channel Link",
            "Status", "Active Strikes", "Strike 1 Date", "Strike 1 Reason", "Strike 2 Date",
            "Strike 2 Reason", "Strike 3 Date", "Strike 3 Reason", "Last Video Date", "Last Action By Admin"
        ]
        ws = self._get_worksheet("Staff Strikes", headers)
        return self._safe_get_records(ws, headers)

    async def get_all_staff_records(self):
        return await asyncio.to_thread(self._execute_with_retry, self._get_all_staff_records_sync)

    def _update_staff_record_sync(
        self,
        channel_id: str,
        worker_name: str,
        worker_user_id: str,
        channel_link: str,
        active_strikes: int,
        s1_date: str,
        s2_date: str,
        s3_date: str,
        last_video_date: str,
        s1_reason: str = "",
        s2_reason: str = "",
        s3_reason: str = "",
        last_admin: str = "",
        status: str = None
    ):
        headers = [
            "Worker Username", "Worker User ID", "Channel ID", "Channel Link",
            "Status", "Active Strikes", "Strike 1 Date", "Strike 1 Reason", "Strike 2 Date",
            "Strike 2 Reason", "Strike 3 Date", "Strike 3 Reason", "Last Video Date", "Last Action By Admin"
        ]
        ws = self._get_worksheet("Staff Strikes", headers)
        records = self._safe_get_records(ws, headers)
        
        row_index = None
        for i, row in enumerate(records, start=2):
            if str(row.get("Channel ID", "")) == str(channel_id):
                row_index = i
                break
        
        worker_status = status or ("Active" if (worker_user_id and str(worker_user_id).isdigit()) else "Inactive")

        row_data = [
            worker_name,
            str(worker_user_id or ""),
            str(channel_id),
            channel_link,
            worker_status,
            active_strikes,
            s1_date or "",
            s1_reason or "",
            s2_date or "",
            s2_reason or "",
            s3_date or "",
            s3_reason or "",
            last_video_date or "",
            last_admin or ""
        ]
        
        if row_index:
            range_name = f"A{row_index}:N{row_index}"
            ws.update(range_name, [row_data], value_input_option="USER_ENTERED")
        else:
            ws.append_row(row_data, value_input_option="USER_ENTERED")

        if self.bot:
            from logger_service import logger_service
            asyncio.run_coroutine_threadsafe(
                logger_service.log_worksheet_change(
                    self.bot, None, "Staff Strikes", f"Updated record for {worker_name}", f"Status: {worker_status}, Active Strikes: {active_strikes}", channel_id=channel_id, channel_link=channel_link
                ),
                self.bot.loop
            )

    async def update_staff_record(
        self,
        channel_id: str,
        worker_name: str,
        worker_user_id: str,
        channel_link: str,
        active_strikes: int,
        s1_date: str,
        s2_date: str,
        s3_date: str,
        last_video_date: str,
        s1_reason: str = "",
        s2_reason: str = "",
        s3_reason: str = "",
        last_admin: str = "",
        status: str = None
    ):
        return await asyncio.to_thread(
            self._execute_with_retry,
            self._update_staff_record_sync,
            channel_id, worker_name, worker_user_id, channel_link, active_strikes, s1_date, s2_date, s3_date, last_video_date, s1_reason, s2_reason, s3_reason, last_admin, status
        )

    # --- Strike Audit Log Logic ---

    def _log_strike_event_sync(
        self,
        event_type: str,
        worker_name: str,
        worker_user_id: str,
        channel_id: str,
        channel_link: str,
        active_strikes: int,
        action_by_admin: str,
        reason: str,
        details: str
    ):
        headers = [
            "Timestamp", "Event Type", "Worker Username", "Worker User ID",
            "Channel ID", "Channel Link", "Active Strikes", "Action By Admin", "Reason", "Details"
        ]
        ws = self._get_worksheet("Strike Audit Log", headers)
        now_str = format_datetime_custom(datetime.now(timezone.utc))
        ws.append_row([
            now_str,
            event_type,
            worker_name,
            str(worker_user_id or ""),
            str(channel_id),
            channel_link,
            active_strikes,
            action_by_admin,
            reason,
            details
        ], value_input_option="USER_ENTERED")

    async def log_strike_event(
        self,
        event_type: str,
        worker_name: str,
        worker_user_id: str,
        channel_id: str,
        channel_link: str,
        active_strikes: int,
        action_by_admin: str,
        reason: str,
        details: str
    ):
        return await asyncio.to_thread(
            self._execute_with_retry,
            self._log_strike_event_sync,
            event_type, worker_name, worker_user_id, channel_id, channel_link, active_strikes, action_by_admin, reason, details
        )

    # --- Daily DM Logs Logic ---

    def _get_dm_record_sync(self, channel_id: str):
        headers = ["Worker Username", "Channel ID", "Channel Link", "Day 1 DMs", "Day 2 DMs", "Day 3 DMs", "Day 4 DMs", "Day 5 DMs", "Day 6 DMs", "Day 7 DMs", "Total DMs"]
        ws = self._get_worksheet("Daily DM Logs", headers)
        records = self._safe_get_records(ws, headers)
        for row in records:
            if str(row.get("Channel ID", "")) == str(channel_id):
                return row
        return {}

    async def get_dm_record(self, channel_id: str):
        return await asyncio.to_thread(self._execute_with_retry, self._get_dm_record_sync, channel_id)

    def _update_dm_record_sync(self, channel_id: str, worker_name: str, channel_link: str, day_num: int, dm_count: int):
        headers = ["Worker Username", "Channel ID", "Channel Link", "Day 1 DMs", "Day 2 DMs", "Day 3 DMs", "Day 4 DMs", "Day 5 DMs", "Day 6 DMs", "Day 7 DMs", "Total DMs"]
        ws = self._get_worksheet("Daily DM Logs", headers)
        records = self._safe_get_records(ws, headers)
        
        row_index = None
        current_row = {}
        for i, row in enumerate(records, start=2):
            if str(row.get("Channel ID", "")) == str(channel_id):
                row_index = i
                current_row = row
                break

        day_values = []
        for d in range(1, 8):
            key = f"Day {d} DMs"
            if d == day_num:
                day_values.append(dm_count)
            else:
                val = current_row.get(key, 0)
                try:
                    day_values.append(int(val) if val != "" else 0)
                except ValueError:
                    day_values.append(0)
                    
        total_dms = sum(day_values)
        row_data = [worker_name, str(channel_id), channel_link] + day_values + [total_dms]
        
        if row_index:
            range_name = f"A{row_index}:K{row_index}"
            ws.update(range_name, [row_data], value_input_option="USER_ENTERED")
        else:
            ws.append_row(row_data, value_input_option="USER_ENTERED")

        if self.bot:
            from logger_service import logger_service
            asyncio.run_coroutine_threadsafe(
                logger_service.log_worksheet_change(
                    self.bot, None, "Daily DM Logs", f"Updated Day {day_num} DMs for {worker_name}", f"Day {day_num}: {dm_count} DMs (Total: {total_dms})", channel_id=channel_id, channel_link=channel_link
                ),
                self.bot.loop
            )

        return day_values, total_dms

    async def update_dm_record(self, channel_id: str, worker_name: str, channel_link: str, day_num: int, dm_count: int):
        return await asyncio.to_thread(self._execute_with_retry, self._update_dm_record_sync, channel_id, worker_name, channel_link, day_num, dm_count)

# Global sheets manager instance
sheets_manager = SheetsManager()
