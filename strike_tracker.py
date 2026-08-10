import asyncio
import re
from datetime import datetime, timedelta
import discord

from sheets_manager import sheets_manager
from pinned_dashboard import update_pinned_dashboard

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


def get_current_window_bounds(dt: datetime | None = None) -> tuple[datetime, datetime]:
    """
    Calculates the 1:00 AM IST to 1:00 AM IST 24-hour deadline window for a given datetime.
    """
    if dt is None:
        dt = datetime.utcnow() + timedelta(hours=5.5)

    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)

    today_1am = dt.replace(hour=1, minute=0, second=0, microsecond=0)

    if dt >= today_1am:
        window_start = today_1am
        window_end = today_1am + timedelta(days=1)
    else:
        window_start = today_1am - timedelta(days=1)
        window_end = today_1am

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
        return (datetime.utcnow() + timedelta(hours=5.5)) + timedelta(hours=offset)

    def _get_worker_tag(self, channel, state: dict) -> str:
        worker_user_id = state.get("worker_user_id")
        if worker_user_id and str(worker_user_id).isdigit():
            return f"<@{worker_user_id}>"
        return f"**{state.get('worker_name', channel.name)}**"

    async def initialize_channel(self, channel):
        """Loads state from Google Sheets or initializes default state for a query channel."""
        channel_id = str(channel.id)
        if channel_id in self.channel_states:
            st = self.channel_states[channel_id]
            await update_pinned_dashboard(
                channel, st["worker_name"], st.get("worker_user_id"), st["active_strikes"], st["strike_dates"], st["last_video_dt"]
            )
            return

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
            strike_reasons = []
            for s_key in ["Strike 1 Date", "Strike 2 Date", "Strike 3 Date"]:
                val = record.get(s_key, "")
                if val:
                    strike_dates.append(str(val))

            for r_key in ["Strike 1 Reason", "Strike 2 Reason", "Strike 3 Reason"]:
                val = record.get(r_key, "")
                if val:
                    strike_reasons.append(str(val))

            last_vid_str = record.get("Last Video Date", "")
            last_video_dt = None
            if last_vid_str:
                try:
                    last_video_dt = datetime.strptime(last_vid_str, "%Y-%m-%d %H:%M:%S IST")
                except ValueError:
                    pass

            claim_str = record.get("Claim Date", "")
            claim_dt = None
            if claim_str:
                try:
                    claim_dt = datetime.strptime(claim_str, "%Y-%m-%d %H:%M:%S IST")
                except ValueError:
                    pass
        else:
            worker_name = channel.name.replace("-query", "").replace("-queries", "").capitalize()
            worker_user_id = None
            active_strikes = 0
            strike_dates = []
            strike_reasons = []
            last_video_dt = None
            claim_dt = None

        self.channel_states[channel_id] = {
            "worker_name": worker_name,
            "worker_user_id": worker_user_id,
            "active_strikes": active_strikes,
            "strike_dates": strike_dates,
            "strike_reasons": strike_reasons,
            "last_video_dt": last_video_dt,
            "claim_dt": claim_dt,
            "time_offset_hours": 0.0,
            "dashboard_msg_id": None
        }

        await update_pinned_dashboard(
            channel, worker_name, worker_user_id, active_strikes, strike_dates, last_video_dt
        )

    async def claim_channel_worker(self, channel, user: discord.User) -> bool:
        channel_id = str(channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(channel)

        state = self.channel_states[channel_id]
        now = self._get_effective_now(channel_id)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S IST")

        state["worker_name"] = user.name
        state["worker_user_id"] = str(user.id)
        state["claim_dt"] = now
        channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"

        s_dates = state["strike_dates"]
        s_reasons = state["strike_reasons"]
        s1_d = s_dates[0] if len(s_dates) > 0 else ""
        s2_d = s_dates[1] if len(s_dates) > 1 else ""
        s3_d = s_dates[2] if len(s_dates) > 2 else ""

        s1_r = s_reasons[0] if len(s_reasons) > 0 else ""
        s2_r = s_reasons[1] if len(s_reasons) > 1 else ""
        s3_r = s_reasons[2] if len(s_reasons) > 2 else ""

        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=user.name,
            worker_user_id=str(user.id),
            channel_link=channel_link,
            active_strikes=state["active_strikes"],
            s1_date=s1_d, s2_date=s2_d, s3_date=s3_d,
            last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S IST") if state["last_video_dt"] else "",
            s1_reason=s1_r, s2_reason=s2_r, s3_reason=s3_r,
            claim_date=now_str
        )

        await update_pinned_dashboard(
            channel, user.name, str(user.id), state["active_strikes"], state["strike_dates"], state["last_video_dt"]
        )

        if self.bot:
            from logger_service import logger_service
            await logger_service.log_worker_add(self.bot, channel.guild.id, user, channel.name, channel_link)

        return True

    async def unclaim_channel_worker(self, channel, admin_user: discord.User) -> bool:
        channel_id = str(channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(channel)

        state = self.channel_states[channel_id]
        prev_name = state["worker_name"]
        default_name = channel.name.replace("-query", "").replace("-queries", "").capitalize()
        state["worker_name"] = default_name
        state["worker_user_id"] = None
        state["claim_dt"] = None
        channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"

        s_dates = state["strike_dates"]
        s_reasons = state["strike_reasons"]
        s1_d = s_dates[0] if len(s_dates) > 0 else ""
        s2_d = s_dates[1] if len(s_dates) > 1 else ""
        s3_d = s_dates[2] if len(s_dates) > 2 else ""

        s1_r = s_reasons[0] if len(s_reasons) > 0 else ""
        s2_r = s_reasons[1] if len(s_reasons) > 1 else ""
        s3_r = s_reasons[2] if len(s_reasons) > 2 else ""

        admin_info = f"{admin_user.name} ({admin_user.id})" if admin_user else "System"

        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=default_name,
            worker_user_id="",
            channel_link=channel_link,
            active_strikes=state["active_strikes"],
            s1_date=s1_d, s2_date=s2_d, s3_date=s3_d,
            last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S IST") if state["last_video_dt"] else "",
            s1_reason=s1_r, s2_reason=s2_r, s3_reason=s3_r,
            claim_date="",
            last_admin=admin_info
        )

        await update_pinned_dashboard(
            channel, default_name, None, state["active_strikes"], state["strike_dates"], state["last_video_dt"]
        )

        if self.bot:
            from logger_service import logger_service
            await logger_service.log_worker_remove(self.bot, channel.guild.id, admin_user, prev_name, channel.name, channel_link)

        return True

    async def add_strikes(self, channel, amount: int = 1, reason: str = "Admin manual addition", admin_user: discord.User = None) -> int:
        """Manually adds strikes to a channel worker (Admin action)."""
        channel_id = str(channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(channel)

        state = self.channel_states[channel_id]
        now = self._get_effective_now(channel_id)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S IST")

        for _ in range(amount):
            state["active_strikes"] += 1
            state["strike_dates"].append(now_str)
            state["strike_reasons"].append(reason)

        channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
        s_dates = state["strike_dates"]
        s_reasons = state["strike_reasons"]
        s1_d = s_dates[0] if len(s_dates) > 0 else ""
        s2_d = s_dates[1] if len(s_dates) > 1 else ""
        s3_d = s_dates[2] if len(s_dates) > 2 else ""

        s1_r = s_reasons[0] if len(s_reasons) > 0 else ""
        s2_r = s_reasons[1] if len(s_reasons) > 1 else ""
        s3_r = s_reasons[2] if len(s_reasons) > 2 else ""

        admin_info = f"{admin_user.name} ({admin_user.id})" if admin_user else "System"

        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=state["worker_name"],
            worker_user_id=state.get("worker_user_id") or "",
            channel_link=channel_link,
            active_strikes=state["active_strikes"],
            s1_date=s1_d, s2_date=s2_d, s3_date=s3_d,
            last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S IST") if state["last_video_dt"] else "",
            s1_reason=s1_r, s2_reason=s2_r, s3_reason=s3_r,
            last_admin=admin_info
        )

        await update_pinned_dashboard(
            channel, state["worker_name"], state.get("worker_user_id"), state["active_strikes"], state["strike_dates"], state["last_video_dt"]
        )

        worker_tag = self._get_worker_tag(channel, state)
        admin_mention = f" by {admin_user.mention}" if admin_user else ""
        details_str = f"Manually issued {amount} strike(s){admin_mention}"
        
        # Send channel notification
        try:
            if state["active_strikes"] == 2:
                warning_msg = f"\n**WARNING!** {worker_tag} You are **1 strike away** from receiving your 3rd strike and being banned!"
            elif state["active_strikes"] >= 3:
                warning_msg = f"\n**CRITICAL ALERT!** {worker_tag} has reached **3/3 STRIKES**! Immediate administrative action required."
            else:
                warning_msg = ""

            await channel.send(
                f"**STRIKE ISSUED!** {worker_tag} has received **{amount} strike(s)**{admin_mention}. "
                f"Active Strikes: `{state['active_strikes']}/3`. Reason: {reason}{warning_msg}"
            )
        except Exception as e:
            print(f"Error sending strike alert in {channel.name}: {e}")

        if self.bot:
            from logger_service import logger_service
            await logger_service.log_strike(
                self.bot, channel.guild.id, worker_tag, channel_link, "ADDED_ADMIN", state["active_strikes"], details_str, reason=reason, admin_user=admin_user
            )
            if state["active_strikes"] >= 3:
                await logger_service.log_three_strike_alert(
                    self.bot, channel.guild.id, worker_tag, channel_link, state["active_strikes"], details_str, reason=reason, admin_user=admin_user
                )

        return amount

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
            if state["strike_reasons"]:
                state["strike_reasons"].pop()

        channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
        s_dates = state["strike_dates"]
        s_reasons = state["strike_reasons"]
        s1_d = s_dates[0] if len(s_dates) > 0 else ""
        s2_d = s_dates[1] if len(s_dates) > 1 else ""
        s3_d = s_dates[2] if len(s_dates) > 2 else ""

        s1_r = s_reasons[0] if len(s_reasons) > 0 else ""
        s2_r = s_reasons[1] if len(s_reasons) > 1 else ""
        s3_r = s_reasons[2] if len(s_reasons) > 2 else ""

        admin_info = f"{admin_user.name} ({admin_user.id})" if admin_user else "System"

        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=state["worker_name"],
            worker_user_id=state.get("worker_user_id") or "",
            channel_link=channel_link,
            active_strikes=state["active_strikes"],
            s1_date=s1_d, s2_date=s2_d, s3_date=s3_d,
            last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S IST") if state["last_video_dt"] else "",
            s1_reason=s1_r, s2_reason=s2_r, s3_reason=s3_r,
            last_admin=admin_info
        )

        await update_pinned_dashboard(
            channel, state["worker_name"], state.get("worker_user_id"), state["active_strikes"], state["strike_dates"], state["last_video_dt"]
        )

        worker_tag = self._get_worker_tag(channel, state)
        admin_str = f" by {admin_user.mention}" if admin_user else ""
        details_str = f"Manually removed {actual_removed} strike(s){admin_str}"

        if self.bot:
            from logger_service import logger_service
            await logger_service.log_strike(
                self.bot, channel.guild.id, worker_tag, channel_link, "UNDONE_ADMIN", state["active_strikes"], details_str, reason=reason, admin_user=admin_user
            )

        return actual_removed

    async def undo_last_strike(self, channel, admin_user: discord.User = None) -> bool:
        reason_str = f"{admin_user.mention} clicked Undo Last Strike button" if admin_user else "Undo Last Strike button clicked"
        return (await self.remove_strikes(channel, amount=1, reason=reason_str, admin_user=admin_user)) > 0

    async def handle_video_submission(self, message: discord.Message):
        channel_id = str(message.channel.id)
        if channel_id not in self.channel_states:
            await self.initialize_channel(message.channel)

        state = self.channel_states[channel_id]
        now = self._get_effective_now(channel_id)
        state["last_video_dt"] = now
        channel_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}"

        s_dates = state["strike_dates"]
        s_reasons = state["strike_reasons"]
        s1_d = s_dates[0] if len(s_dates) > 0 else ""
        s2_d = s_dates[1] if len(s_dates) > 1 else ""
        s3_d = s_dates[2] if len(s_dates) > 2 else ""

        s1_r = s_reasons[0] if len(s_reasons) > 0 else ""
        s2_r = s_reasons[1] if len(s_reasons) > 1 else ""
        s3_r = s_reasons[2] if len(s_reasons) > 2 else ""

        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=state["worker_name"],
            worker_user_id=state.get("worker_user_id") or "",
            channel_link=channel_link,
            active_strikes=state["active_strikes"],
            s1_date=s1_d, s2_date=s2_d, s3_date=s3_d,
            last_video_date=now.strftime("%Y-%m-%d %H:%M:%S IST"),
            s1_reason=s1_r, s2_reason=s2_r, s3_reason=s3_r
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
                reason_str = "Missing daily screen recording submission before 1:00 AM IST"
                state["strike_dates"].append(now_str)
                state["strike_reasons"].append(reason_str)
                state["last_video_dt"] = now

                s_dates = state["strike_dates"]
                s_reasons = state["strike_reasons"]
                s1_d = s_dates[0] if len(s_dates) > 0 else ""
                s2_d = s_dates[1] if len(s_dates) > 1 else ""
                s3_d = s_dates[2] if len(s_dates) > 2 else ""

                s1_r = s_reasons[0] if len(s_reasons) > 0 else ""
                s2_r = s_reasons[1] if len(s_reasons) > 1 else ""
                s3_r = s_reasons[2] if len(s_reasons) > 2 else ""

                channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
                await sheets_manager.update_staff_record(
                    channel_id=channel_id,
                    worker_name=state["worker_name"],
                    worker_user_id=state.get("worker_user_id") or "",
                    channel_link=channel_link,
                    active_strikes=state["active_strikes"],
                    s1_date=s1_d, s2_date=s2_d, s3_date=s3_d,
                    last_video_date=now.strftime("%Y-%m-%d %H:%M:%S IST"),
                    s1_reason=s1_r, s2_reason=s2_r, s3_reason=s3_r,
                    last_admin="System (Deadline)"
                )

                details_str = "1:00 AM IST screen recording deadline breached"

                await update_pinned_dashboard(
                    channel, state["worker_name"], state.get("worker_user_id"), state["active_strikes"], state["strike_dates"], now
                )

                worker_tag = self._get_worker_tag(channel, state)
                
                if state["active_strikes"] == 2:
                    warning_msg = f"\n**WARNING!** {worker_tag} You have received **2 strikes**! You are **1 strike away** from receiving your 3rd strike and being banned."
                elif state["active_strikes"] >= 3:
                    warning_msg = f"\n**CRITICAL ALERT!** {worker_tag} has reached **3/3 STRIKES**! Immediate administrative action required."
                else:
                    warning_msg = ""

                try:
                    await channel.send(
                        f"**STRIKE ISSUED!** {worker_tag} has received **Strike #{state['active_strikes']}** "
                        f"for missing the 1:00 AM IST screen recording deadline.{warning_msg}"
                    )
                except Exception as e:
                    print(f"Error sending strike alert in {channel.name}: {e}")

                if self.bot:
                    from logger_service import logger_service
                    await logger_service.log_strike(
                        bot, channel.guild.id, worker_tag, channel_link, "ISSUED", state["active_strikes"], details_str, reason=reason_str
                    )
                    if state["active_strikes"] >= 3:
                        await logger_service.log_three_strike_alert(
                            bot, channel.guild.id, worker_tag, channel_link, state["active_strikes"], details_str, reason=reason_str
                        )

        # Check 7-Day Clean Streak Auto-Revocation
        if state["active_strikes"] > 0 and state["strike_dates"]:
            latest_strike_str = state["strike_dates"][-1]
            try:
                latest_strike_dt = datetime.strptime(latest_strike_str, "%Y-%m-%d %H:%M:%S IST")
                days_since_strike = (now - latest_strike_dt).total_seconds() / 86400.0
                if days_since_strike >= 7.0:
                    print(f"Clean streak reached for {channel.name}. Revoking {state['active_strikes']} active strike(s)!")
                    prev_strikes = state["active_strikes"]
                    state["active_strikes"] = 0
                    state["strike_dates"] = []
                    state["strike_reasons"] = []

                    channel_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
                    await sheets_manager.update_staff_record(
                        channel_id=channel_id,
                        worker_name=state["worker_name"],
                        worker_user_id=state.get("worker_user_id") or "",
                        channel_link=channel_link,
                        active_strikes=0,
                        s1_date="", s2_date="", s3_date="",
                        last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S IST") if state["last_video_dt"] else "",
                        s1_reason="", s2_reason="", s3_reason="",
                        last_admin="System (7-Day Revocation)"
                    )

                    await update_pinned_dashboard(
                        channel, state["worker_name"], state.get("worker_user_id"), 0, [], state["last_video_dt"]
                    )

                    worker_tag = self._get_worker_tag(channel, state)
                    try:
                        await channel.send(
                            f"🎉 **CLEAN STREAK REWARD!** {worker_tag} completed 7 consecutive days without a new strike. "
                            f"All active strikes have been automatically revoked!"
                        )
                    except Exception:
                        pass

                    if self.bot:
                        from logger_service import logger_service
                        await logger_service.log_strike(
                            bot, channel.guild.id, worker_tag, channel_link, "REVOKED_7DAY", 0, f"Revoked {prev_strikes} strike(s) after 7 clean days."
                        )
            except Exception as e:
                print(f"Error checking clean streak in {channel.name}: {e}")

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
