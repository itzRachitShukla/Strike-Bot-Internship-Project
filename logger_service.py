import os
import json
import asyncio
import discord

CONFIG_FILE = "log_channels.json"

class LoggerService:
    def __init__(self, config_file=CONFIG_FILE):
        self.config_file = config_file
        self.log_channels = {
            "claim": None,            # Channel ID for /claim logs
            "strike": None,           # Channel ID for strike logs
            "strike_3": None,         # Channel ID for 3-strike alert logs
            "worker_add": None,       # Channel ID for worker claim channel logs
            "worker_remove": None,    # Channel ID for worker unclaim logs
            "worksheet": None         # Channel ID for Google Sheets change logs
        }
        self.ping_targets = []        # List of role or user IDs/mentions to ping for 3-strike alerts
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        if k == "ping_targets":
                            self.ping_targets = v
                        elif k in self.log_channels:
                            self.log_channels[k] = v
            except Exception as e:
                print(f"Error loading log_channels.json: {e}")

    def save_config(self):
        try:
            with open(self.config_file, "w") as f:
                save_data = dict(self.log_channels)
                save_data["ping_targets"] = self.ping_targets
                json.dump(save_data, f, indent=2)
        except Exception as e:
            print(f"Error saving log_channels.json: {e}")

    def set_log_channel(self, category: str, channel_id: int | None):
        if category in self.log_channels:
            self.log_channels[category] = channel_id
            self.save_config()
            return True
        return False

    def set_ping_targets(self, targets: list[str]):
        self.ping_targets = targets
        self.save_config()
        return True

    async def _send_v2_log(self, bot: discord.Client | None, guild_id: int | None, category: str, container_items: list, accent_color: discord.Color | None = None):
        if not bot:
            return
        channel_id = self.log_channels.get(category)
        if not channel_id and category == "strike_3":
            # Fallback to standard strike channel if strike_3 is not explicitly bound
            channel_id = self.log_channels.get("strike")
        if not channel_id:
            return
            
        channel = bot.get_channel(int(channel_id))
        if not channel and guild_id:
            guild = bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(int(channel_id))
                
        if not channel:
            return

        try:
            layout_view = discord.ui.LayoutView()
            container = discord.ui.Container(accent_color=accent_color) if accent_color else discord.ui.Container()
            for item in container_items:
                container.add_item(item)
            layout_view.add_item(container)

            await channel.send(view=layout_view)
        except Exception as e:
            print(f"Error sending {category} V2 log: {e}")

    # --- 1. Claim Log ---
    async def log_claim(self, bot: discord.Client, guild_id: int, ig_link: str, referrer_name: str, channel_link: str, claimed_at: str, platform: str = "Instagram"):
        items = [
            discord.ui.TextDisplay(f"## Audit Log: Influencer Claimed ({platform})"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"- **__Platform:__** `{platform}`\n"
                f"- **__Link / Handle:__** {ig_link}\n"
                f"- **__Claimed By:__** {referrer_name}"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"- **__Query Channel:__** {channel_link}\n"
                f"- **__Timestamp:__** `{claimed_at}`"
            )
        ]
        await self._send_v2_log(bot, guild_id, "claim", items, accent_color=discord.Color.blue())

    # --- 2. Strike Log ---
    async def log_strike(
        self,
        bot: discord.Client,
        guild_id: int,
        worker_tag: str,
        channel_link: str,
        event_type: str,
        active_strikes: int,
        details: str,
        reason: str | None = None,
        admin_user: discord.User | None = None
    ):
        type_titles = {
            "ISSUED": "Audit Log: Strike Issued",
            "ADDED_ADMIN": "Audit Log: Admin Strike Added",
            "REVOKED_7DAY": "Audit Log: 7-Day Clean Streak Strike Revocation",
            "UNDONE_ADMIN": "Audit Log: Admin Strike Undone"
        }
        title = type_titles.get(event_type, "Audit Log: Strike Update")
        accent = discord.Color.red() if event_type in ("ISSUED", "ADDED_ADMIN") else discord.Color.green()
        
        worker_info = f"- **__Worker:__** {worker_tag}\n"
        if admin_user:
            worker_info += f"- **__Action By Admin:__** {admin_user.mention}\n"

        if reason:
            details_display = f"- **__Details:__** {details}\n- **__Reason:__** {reason}"
        elif ". Reason: " in details:
            parts = details.split(". Reason: ", 1)
            details_display = f"- **__Details:__** {parts[0]}\n- **__Reason:__** {parts[1]}"
        elif "Reason: " in details:
            parts = details.split("Reason: ", 1)
            details_display = f"- **__Details:__** {parts[0].rstrip('. ')}\n- **__Reason:__** {parts[1]}"
        else:
            details_display = f"- **__Details:__** {details}"

        items = [
            discord.ui.TextDisplay(f"## {title}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"{worker_info}"
                f"- **__Query Channel:__** {channel_link}\n"
                f"- **__Active Strikes:__** `{active_strikes}/3`"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(details_display)
        ]
        await self._send_v2_log(bot, guild_id, "strike", items, accent_color=accent)

        # Also write event to Google Sheets "Strike Audit Log" worksheet tab
        try:
            from sheets_manager import sheets_manager
            admin_str = f"{admin_user.name} ({admin_user.id})" if admin_user else "System"
            ch_id = ""
            if "channels/" in channel_link:
                parts = channel_link.split("channels/")[-1].split("/")
                if len(parts) >= 2:
                    ch_id = parts[1]
                    
            asyncio.create_task(
                sheets_manager.log_strike_event(
                    event_type=event_type,
                    worker_name=worker_tag.replace("<@", "").replace(">", ""),
                    worker_user_id="",
                    channel_id=ch_id,
                    channel_link=channel_link,
                    active_strikes=active_strikes,
                    action_by_admin=admin_str,
                    reason=reason or "",
                    details=details
                )
            )
        except Exception as se:
            print(f"Warning logging strike event to Google Sheets: {se}")

    # --- 2b. 3-Strike Alert Log ---
    async def log_three_strike_alert(
        self,
        bot: discord.Client,
        guild_id: int,
        worker_tag: str,
        channel_link: str,
        active_strikes: int,
        details: str = "Worker has reached 3 active strikes and requires admin action.",
        reason: str | None = None,
        admin_user: discord.User | None = None
    ):
        """Sends a high-priority 3-strike alert log to the 3-strike log channel pinging configured roles/members."""
        pings = []
        for target in self.ping_targets:
            if target.startswith("<@") or target.startswith("<@&"):
                pings.append(target)
            elif str(target).isdigit():
                # Check if it's a role or user
                pings.append(f"<@&{target}>")
        ping_str = " ".join(pings) if pings else ""

        worker_info = f"- **__Worker:__** {worker_tag}\n"
        if admin_user:
            worker_info += f"- **__Action By Admin:__** {admin_user.mention}\n"

        if reason:
            details_display = f"- **__Details:__** {details}\n- **__Reason:__** {reason}"
        elif ". Reason: " in details:
            parts = details.split(". Reason: ", 1)
            details_display = f"- **__Details:__** {parts[0]}\n- **__Reason:__** {parts[1]}"
        elif "Reason: " in details:
            parts = details.split("Reason: ", 1)
            details_display = f"- **__Details:__** {parts[0].rstrip('. ')}\n- **__Reason:__** {parts[1]}"
        else:
            details_display = f"- **__Details:__** {details}"

        items = []
        if ping_str:
            items.append(discord.ui.TextDisplay(f"**Alert Pings:** {ping_str}"))
            items.append(discord.ui.Separator())

        items.extend([
            discord.ui.TextDisplay("## CRITICAL AUDIT ALERT: 3 STRIKES REACHED"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"{worker_info}"
                f"- **__Query Channel:__** {channel_link}\n"
                f"- **__Active Strikes:__** `{active_strikes}/3`"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"{details_display}\n- **__Action Required:__** Worker has reached maximum strikes. Please take appropriate administrative action (e.g. unclaim channel / ban).")
        ])
        await self._send_v2_log(bot, guild_id, "strike_3", items, accent_color=discord.Color.dark_red())

    # --- 3. Worker Add Log ---
    async def log_worker_add(self, bot: discord.Client, guild_id: int, worker_user: discord.User, channel_name: str, channel_link: str):
        items = [
            discord.ui.TextDisplay("## Audit Log: Worker Channel Claimed"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"- **__Worker:__** {worker_user.mention} (`{worker_user.name}`)\n"
                f"- **__Query Channel:__** {channel_link} (`#{channel_name}`)"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay("Worker has successfully bound their Discord account to this query channel.")
        ]
        await self._send_v2_log(bot, guild_id, "worker_add", items, accent_color=discord.Color.green())

    # --- 4. Worker Remove Log ---
    async def log_worker_remove(self, bot: discord.Client, guild_id: int, admin_user: discord.User, previous_worker_name: str, channel_name: str, channel_link: str):
        items = [
            discord.ui.TextDisplay("## Audit Log: Worker Channel Unclaimed"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"- **__Unclaimed By Admin:__** {admin_user.mention} (`{admin_user.name}`)\n"
                f"- **__Previous Worker:__** `{previous_worker_name}`"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"- **__Query Channel:__** {channel_link} (`#{channel_name}`)")
        ]
        await self._send_v2_log(bot, guild_id, "worker_remove", items, accent_color=discord.Color.orange())

    # --- 5. Worksheet Changes Log ---
    async def log_worksheet_change(
        self,
        bot: discord.Client,
        guild_id: int | None,
        worksheet_name: str,
        action_summary: str,
        details: str,
        channel_id: str | int | None = None,
        channel_link: str | None = None
    ):
        import time
        from config import SPREADSHEET_ID
        
        sheet_url = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
        now_unix = int(time.time())
        timestamp_str = f"<t:{now_unix}:F> (<t:{now_unix}:R>)"

        link_button = discord.ui.Button(
            label="Open Google Sheet",
            url=sheet_url,
            style=discord.ButtonStyle.link
        )

        channel_display = ""
        if channel_id and str(channel_id).isdigit():
            channel_display = f"\n- **__Query Channel:__** <#{channel_id}>"
        elif channel_link:
            channel_display = f"\n- **__Query Channel:__** {channel_link}"

        items = [
            discord.ui.Section(
                discord.ui.TextDisplay(f"## Google Sheets Audit Log: `{worksheet_name}`"),
                accessory=link_button
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"- **__Target Worksheet Tab:__** `{worksheet_name}`{channel_display}\n"
                f"- **__Action Summary:__** {action_summary}\n"
                f"- **__Change Details:__** {details}"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"- **__Google Spreadsheet Link:__** [Open Google Sheet]({sheet_url})\n"
                f"- **__Timestamp:__** {timestamp_str}"
            )
        ]
        await self._send_v2_log(bot, guild_id, "worksheet", items, accent_color=discord.Color.green())

logger_service = LoggerService()
