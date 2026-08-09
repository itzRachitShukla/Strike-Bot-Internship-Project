import asyncio
import re
from datetime import datetime, timedelta, timezone
import discord

from sheets_manager import sheets_manager
from pinned_dashboard import update_pinned_dashboard

IST = timezone(timedelta(hours=5, minutes=30))

def is_query_channel(channel) -> bool:
    """Returns True if the channel name ends with -query or -queries."""
    if not hasattr(channel, "name") or not channel.name:
        return False
    name = channel.name.lower()
    return name.endswith("-query") or name.endswith("-queries")


def is_video_message(message: discord.Message) -> bool:
    """Returns True if message contains a playable video attachment or video link."""
    video_extensions = ('.mp4', '.mov', '.webm', '.m4v', '.mkv', '.avi', '.wmv')
    
    # Check attachments
    for att in message.attachments:
        if att.filename.lower().endswith(video_extensions) or (att.content_type and att.content_type.startswith('video/')):
            return True
            
    # Check links inside text
    url_pattern = re.compile(r'https?://\S+')
    urls = url_pattern.findall(message.content)
    for url in urls:
        clean_url = url.lower().split('?')[0]
        if clean_url.endswith(video_extensions) or 'youtube.com' in clean_url or 'youtu.be' in clean_url or 'streamable.com' in clean_url:
            return True
            
    return False


def get_current_window_bounds(now_ist: datetime):
    """
    Calculates the 24h window bounds starting and ending at 1:00 AM IST.
    Window runs from 1:00 AM IST to 1:00 AM IST of the following day.
    """
    if now_ist.hour < 1:
        window_start = (now_ist - timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0)
        window_end = now_ist.replace(hour=1, minute=0, second=0, microsecond=0)
    else:
        window_start = now_ist.replace(hour=1, minute=0, second=0, microsecond=0)
        window_end = (now_ist + timedelta(days=1)).replace(hour=1, minute=0, second=0, microsecond=0)
        
    return window_start, window_end


class StrikeTracker:
    def __init__(self):
        self.channel_states = {}
        self.bot = None

    def set_bot(self, bot):
        self.bot = bot

    def _get_effective_now(self, channel_id: str) -> datetime:
        state = self.channel_states.get(channel_id, {})
        offset = state.get("time_offset_hours", 0.0)
        return datetime.now(IST) + timedelta(hours=offset)

    async def initialize_channel(self, channel):
        """Loads state from Google Sheets or initializes default state for a query channel."""
        channel_id = str(channel.id)
        if channel_id in self.channel_states:
            st = self.channel_states[channel_id]
            await update_pinned_dashboard(
                channel, st["worker_name"], st.get("worker_user_id"), st["active_strikes"], st["strike_dates"], st["last_video_dt"]
            )
            return

        # Fetch records from Google Sheets
        records = await sheets_manager.get_all_staff_records()
        record = None
        for r in records:
            if str(r.get("Channel ID", "")) == channel_id:
                record = r
                break

        if record:
            worker_name = record.get("Worker Username", channel.name.replace("-query", "").replace("-queries", "").capitalize())
            worker_user_id = str(record.get("Worker User ID", "")) or None
            active_strikes = int(record.get("Active Strikes", 0) or 0)
            
            strike_dates = []
            for s_key in ["Strike 1 Date", "Strike 2 Date", "Strike 3 Date"]:
                val = record.get(s_key, "")
                if val:
                    strike_dates.append(str(val))
                    
            last_vid_str = record.get("Last Video Date", "")
            last_video_dt = None
            if last_vid_str:
                try:
                    dt_naive = datetime.strptime(last_vid_str, "%Y-%m-%d %H:%M:%S IST")
                    last_video_dt = dt_naive.replace(tzinfo=IST)
                except ValueError:
                    try:
                        dt_naive = datetime.strptime(last_vid_str, "%Y-%m-%d %H:%M:%S UTC")
                        last_video_dt = dt_naive.replace(tzinfo=IST)
                    except ValueError:
                        pass
        else:
            worker_name = channel.name.replace("-query", "").replace("-queries", "").capitalize()
            worker_user_id = None
            active_strikes = 0
            strike_dates = []
            last_video_dt = None

        self.channel_states[channel_id] = {
            "worker_name": worker_name,
            "worker_user_id": worker_user_id,
            "active_strikes": active_strikes,
            "strike_dates": strike_dates,
            "last_video_dt": last_video_dt,
            "claim_dt": datetime.now(IST) if worker_user_id else None,
            "time_offset_hours": 0.0
        }

        await update_pinned_dashboard(
            channel, worker_name, worker_user_id, active_strikes, strike_dates, last_video_dt
        )

    async def claim_channel_worker(self, channel, worker_user: discord.User) -> bool:
        channel_id = str(channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(channel)

        state = self.channel_states[channel_id]
        state["worker_name"] = worker_user.name
        state["worker_user_id"] = str(worker_user.id)
        state["claim_dt"] = datetime.now(IST)

        channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
        s_dates = state["strike_dates"]
        s1 = s_dates[0] if len(s_dates) > 0 else ""
        s2 = s_dates[1] if len(s_dates) > 1 else ""
        s3 = s_dates[2] if len(s_dates) > 2 else ""

        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=worker_user.name,
            worker_user_id=str(worker_user.id),
            channel_link=channel_link,
            active_strikes=state["active_strikes"],
            s1_date=s1, s2_date=s2, s3_date=s3,
            last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S IST") if state["last_video_dt"] else ""
        )

        await update_pinned_dashboard(
            channel, worker_user.name, str(worker_user.id), state["active_strikes"], state["strike_dates"], state["last_video_dt"]
        )

        if self.bot:
            from logger_service import logger_service
            await logger_service.log_worker_add(self.bot, channel.guild.id, worker_user, channel.name, channel_link)

        return True

    async def unclaim_channel_worker(self, channel, admin_user: discord.User) -> bool:
        channel_id = str(channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(channel)

        state = self.channel_states[channel_id]
        if not state.get("worker_user_id"):
            return False

        prev_name = state["worker_name"]
        state["worker_name"] = channel.name.replace("-query", "").replace("-queries", "").capitalize()
        state["worker_user_id"] = None
        state["claim_dt"] = None

        channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
        s_dates = state["strike_dates"]
        s1 = s_dates[0] if len(s_dates) > 0 else ""
        s2 = s_dates[1] if len(s_dates) > 1 else ""
        s3 = s_dates[2] if len(s_dates) > 2 else ""

        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=state["worker_name"],
            worker_user_id="",
            channel_link=channel_link,
            active_strikes=state["active_strikes"],
            s1_date=s1, s2_date=s2, s3_date=s3,
            last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S IST") if state["last_video_dt"] else ""
        )

        await update_pinned_dashboard(
            channel, state["worker_name"], None, state["active_strikes"], state["strike_dates"], state["last_video_dt"]
        )

        if self.bot:
            from logger_service import logger_service
            await logger_service.log_worker_remove(self.bot, channel.guild.id, admin_user, prev_name, channel.name, channel_link)

        return True

    async def remove_strikes(self, channel, amount: int = 1, reason: str = "Admin manual removal", admin_user: discord.User = None) -> int:
        """Manually removes strikes from a channel worker (Admin action)."""
        channel_id = str(channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(channel)

        state = self.channel_states[channel_id]
        if state["active_strikes"] <= 0:
            return 0

        actual_removed = min(state["active_strikes"], amount)
        state["active_strikes"] -= actual_removed
        for _ in range(actual_removed):
            if state["strike_dates"]:
                state["strike_dates"].pop()

        channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
        s_dates = state["strike_dates"]
        s1 = s_dates[0] if len(s_dates) > 0 else ""
        s2 = s_dates[1] if len(s_dates) > 1 else ""
        s3 = s_dates[2] if len(s_dates) > 2 else ""

        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=state["worker_name"],
            worker_user_id=state.get("worker_user_id") or "",
            channel_link=channel_link,
            active_strikes=state["active_strikes"],
            s1_date=s1, s2_date=s2, s3_date=s3,
            last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S IST") if state["last_video_dt"] else ""
        )

        await update_pinned_dashboard(
            channel, state["worker_name"], state.get("worker_user_id"), state["active_strikes"], state["strike_dates"], state["last_video_dt"]
        )

        if self.bot:
            from logger_service import logger_service
            worker_tag = f"<@{state['worker_user_id']}>" if state.get("worker_user_id") else state["worker_name"]
            admin_str = f" by {admin_user.mention}" if admin_user else ""
            await logger_service.log_strike(
                self.bot, channel.guild.id, worker_tag, channel_link, "UNDONE_ADMIN", state["active_strikes"], f"Manually removed {actual_removed} strike(s){admin_str}. Reason: {reason}"
            )

        return actual_removed

    async def undo_last_strike(self, channel) -> bool:
        return (await self.remove_strikes(channel, amount=1, reason="Admin clicked Undo Last Strike button")) > 0

    async def handle_video_submission(self, message: discord.Message):
        channel_id = str(message.channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(message.channel)

        state = self.channel_states[channel_id]
        now = self._get_effective_now(channel_id)
        state["last_video_dt"] = now
        channel_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}"

        s_dates = state["strike_dates"]
        s1 = s_dates[0] if len(s_dates) > 0 else ""
        s2 = s_dates[1] if len(s_dates) > 1 else ""
        s3 = s_dates[2] if len(s_dates) > 2 else ""

        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=state["worker_name"],
            worker_user_id=state.get("worker_user_id") or "",
            channel_link=channel_link,
            active_strikes=state["active_strikes"],
            s1_date=s1, s2_date=s2, s3_date=s3,
            last_video_date=now.strftime("%Y-%m-%d %H:%M:%S IST")
        )

        await update_pinned_dashboard(
            message.channel, state["worker_name"], state.get("worker_user_id"), state["active_strikes"], state["strike_dates"], now
        )

    async def audit_channel(self, channel, bot: discord.Client | None):
        channel_id = str(channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(channel)

        state = self.channel_states[channel_id]
        now = self._get_effective_now(channel_id)
        last_vid = state["last_video_dt"]
        window_start, window_end = get_current_window_bounds(now)

        # Check 1 AM - 1 AM IST window deadline breach for claimed channel
        if state.get("worker_user_id"):
            has_video_in_window = (last_vid is not None and last_vid >= window_start)
            if not has_video_in_window:
                state["active_strikes"] += 1
                now_str = now.strftime("%Y-%m-%d %H:%M:%S IST")
                state["strike_dates"].append(now_str)
                state["last_video_dt"] = now  # Advance timer so next window is tracked

                s_dates = state["strike_dates"]
                s1 = s_dates[0] if len(s_dates) > 0 else ""
                s2 = s_dates[1] if len(s_dates) > 1 else ""
                s3 = s_dates[2] if len(s_dates) > 2 else ""

                channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
                await sheets_manager.update_staff_record(
                    channel_id=channel_id,
                    worker_name=state["worker_name"],
                    worker_user_id=state.get("worker_user_id") or "",
                    channel_link=channel_link,
                    active_strikes=state["active_strikes"],
                    s1_date=s1, s2_date=s2, s3_date=s3,
                    last_video_date=now.strftime("%Y-%m-%d %H:%M:%S IST")
                )

                await update_pinned_dashboard(
                    channel, state["worker_name"], state.get("worker_user_id"), state["active_strikes"], state["strike_dates"], now
                )

                worker_tag = f"<@{state['worker_user_id']}>" if state.get("worker_user_id") else f"**{state['worker_name']}**"
                try:
                    await channel.send(
                        f"**STRIKE ISSUED!** {worker_tag} has received **Strike #{state['active_strikes']}** "
                        f"for missing the 1:00 AM IST screen recording deadline."
                    )
                except Exception as e:
                    print(f"Error sending strike alert in {channel.name}: {e}")

                from logger_service import logger_service
                await logger_service.log_strike(
                    bot, channel.guild.id, worker_tag, channel_link, "ISSUED", state["active_strikes"], "1:00 AM IST screen recording deadline breached."
                )

        # Check 7-Day Clean Streak Auto-Revocation
        if state["active_strikes"] > 0 and state["strike_dates"]:
            latest_strike_str = state["strike_dates"][-1]
            try:
                dt_naive = datetime.strptime(latest_strike_str, "%Y-%m-%d %H:%M:%S IST")
                latest_strike_dt = dt_naive.replace(tzinfo=IST)
            except ValueError:
                try:
                    dt_naive = datetime.strptime(latest_strike_str, "%Y-%m-%d %H:%M:%S UTC")
                    latest_strike_dt = dt_naive.replace(tzinfo=IST)
                except ValueError:
                    latest_strike_dt = None

            if latest_strike_dt:
                days_since_strike = (now - latest_strike_dt).total_seconds() / 86400.0
                if days_since_strike >= 7.0:
                    print(f"Clean streak reached for {channel.name}. Revoking {state['active_strikes']} active strike(s)!")
                    prev_strikes = state["active_strikes"]
                    state["active_strikes"] = 0
                    state["strike_dates"] = []

                    channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
                    await sheets_manager.update_staff_record(
                        channel_id=channel_id,
                        worker_name=state["worker_name"],
                        worker_user_id=state.get("worker_user_id") or "",
                        channel_link=channel_link,
                        active_strikes=0,
                        s1_date="", s2_date="", s3_date="",
                        last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S IST") if state["last_video_dt"] else ""
                    )

                    await update_pinned_dashboard(
                        channel, state["worker_name"], state.get("worker_user_id"), 0, [], state["last_video_dt"]
                    )

                    worker_tag = f"<@{state['worker_user_id']}>" if state.get("worker_user_id") else f"**{state['worker_name']}**"
                    try:
                        await channel.send(
                            f"**CLEAN STREAK REWARD!** {worker_tag} completed 7 consecutive days without a new strike. "
                            f"All active strikes have been automatically revoked!"
                        )
                    except Exception:
                        pass

                    from logger_service import logger_service
                    await logger_service.log_strike(
                        bot, channel.guild.id, worker_tag, channel_link, "REVOKED_7DAY", 0, f"Revoked {prev_strikes} strike(s) after 7 clean days."
                    )

    async def audit_all_query_channels(self, bot: discord.Client):
        self.bot = bot
        for guild in bot.guilds:
            for ch in guild.text_channels:
                if is_query_channel(ch):
                    try:
                        await self.audit_channel(ch, bot)
                    except Exception as e:
                        print(f"Error auditing channel {ch.name}: {e}")

    async def simulate_time_travel(self, channel, hours: float):
        channel_id = str(channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(channel)
        state = self.channel_states[channel_id]
        state["time_offset_hours"] += hours

strike_tracker = StrikeTracker()
