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
            "worker_add": None,       # Channel ID for worker claim channel logs
            "worker_remove": None,    # Channel ID for worker unclaim logs
            "worksheet": None         # Channel ID for Google Sheets change logs
        }
        self.load_config()

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    data = json.load(f)
                    self.log_channels.update(data)
            except Exception as e:
                print(f"Error loading log_channels.json: {e}")

    def save_config(self):
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.log_channels, f, indent=2)
        except Exception as e:
            print(f"Error saving log_channels.json: {e}")

    def set_log_channel(self, category: str, channel_id: int | None):
        if category in self.log_channels:
            self.log_channels[category] = channel_id
            self.save_config()
            return True
        return False

    async def _send_v2_log(self, bot: discord.Client | None, guild_id: int | None, category: str, container_items: list, accent_color: discord.Color | None = None):
        if not bot:
            return
        channel_id = self.log_channels.get(category)
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
    async def log_claim(self, bot: discord.Client, guild_id: int, ig_link: str, referrer_name: str, channel_link: str, claimed_at: str):
        items = [
            discord.ui.TextDisplay("## Audit Log: Influencer Claimed"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"- **__Instagram Link:__** {ig_link}\n"
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
    async def log_strike(self, bot: discord.Client, guild_id: int, worker_tag: str, channel_link: str, event_type: str, active_strikes: int, details: str):
        type_titles = {
            "ISSUED": "Audit Log: Strike Issued",
            "REVOKED_7DAY": "Audit Log: 7-Day Clean Streak Strike Revocation",
            "UNDONE_ADMIN": "Audit Log: Admin Strike Undone"
        }
        title = type_titles.get(event_type, "Audit Log: Strike Update")
        accent = discord.Color.red() if event_type == "ISSUED" else discord.Color.green()
        items = [
            discord.ui.TextDisplay(f"## {title}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"- **__Worker:__** {worker_tag}\n"
                f"- **__Query Channel:__** {channel_link}\n"
                f"- **__Active Strikes:__** `{active_strikes}/3`"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"- **__Details:__** {details}")
        ]
        await self._send_v2_log(bot, guild_id, "strike", items, accent_color=accent)

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
