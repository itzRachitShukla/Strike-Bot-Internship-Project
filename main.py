import asyncio
import os
import re
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DISCORD_TOKEN, GOOGLE_SERVICE_ACCOUNT_FILE, SPREADSHEET_ID, STRIKE_DEADLINE_HOURS, REVOCATION_STREAK_DAYS, AUDIT_INTERVAL_MINUTES
from sheets_manager import sheets_manager, normalize_link, normalize_ig_link
from strike_tracker import strike_tracker, is_query_channel, is_video_message
from pinned_dashboard import update_pinned_dashboard, DashboardLayoutView
from logger_service import logger_service

# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Helper function to parse time strings like 12h, 7d, 12d, 1m, 50s into hours
def parse_time_string_to_hours(time_str: str) -> float:
    time_str = time_str.strip().lower()
    match = re.match(r'^(\d+(?:\.\d+)?)\s*([a-z]+)?$', time_str)
    if not match:
        raise ValueError(f"Invalid time format: '{time_str}'. Use formats like 12h, 7d, 1m, 50s.")
    
    val = float(match.group(1))
    unit = match.group(2) or "h"

    if unit in ("s", "sec", "second", "seconds"):
        return val / 3600.0
    elif unit in ("m", "min", "minute", "minutes"):
        return val / 60.0
    elif unit in ("h", "hr", "hour", "hours"):
        return val
    elif unit in ("d", "day", "days"):
        return val * 24.0
    else:
        raise ValueError(f"Unknown time unit '{unit}'. Supported units: s, m, h, d.")


# Helper function to safely send interaction responses even if interaction expired or was deferred
async def safe_respond(interaction: discord.Interaction, content: str = None, embed: discord.Embed = None, ephemeral: bool = False):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
    except discord.NotFound:
        if interaction.channel:
            await interaction.channel.send(content=content, embed=embed)
    except Exception as e:
        print(f"Error sending safe_respond: {e}")

# ----------------------------------------------------
# SETUP LOGS UI & SLASH COMMAND (/setup-logs)
# ----------------------------------------------------

def build_setup_logs_embed(active_category: str = "claim") -> discord.Embed:
    cat_names = {
        "claim": "1. Claim Logs",
        "strike": "2. Strike Event Logs",
        "strike_3": "3. 3-Strike Alert Logs",
        "worker_add": "4. Worker Add Logs",
        "worker_remove": "5. Worker Remove Logs",
        "worksheet": "6. Worksheet Changes Logs"
    }
    embed = discord.Embed(
        title="Audit Log Channels Configuration",
        description=(
            "Click **Auto-Setup Audit Log Channels** to create channels automatically,\n"
            "or pick a category below to configure manually:\n\n"
            f"**Currently Editing:** `{cat_names.get(active_category, active_category)}`"
        ),
        color=discord.Color.blue()
    )
    embed.add_field(name="1. Claim Logs", value=f"<#{logger_service.log_channels.get('claim')}>" if logger_service.log_channels.get('claim') else "*Not Configured*", inline=False)
    embed.add_field(name="2. Strike Event Logs", value=f"<#{logger_service.log_channels.get('strike')}>" if logger_service.log_channels.get('strike') else "*Not Configured*", inline=False)
    embed.add_field(name="3. 3-Strike Alert Logs", value=f"<#{logger_service.log_channels.get('strike_3')}>" if logger_service.log_channels.get('strike_3') else "*Not Configured*", inline=False)
    embed.add_field(name="4. Worker Add Logs", value=f"<#{logger_service.log_channels.get('worker_add')}>" if logger_service.log_channels.get('worker_add') else "*Not Configured*", inline=False)
    embed.add_field(name="5. Worker Remove Logs", value=f"<#{logger_service.log_channels.get('worker_remove')}>" if logger_service.log_channels.get('worker_remove') else "*Not Configured*", inline=False)
    embed.add_field(name="6. Worksheet Changes Logs", value=f"<#{logger_service.log_channels.get('worksheet')}>" if logger_service.log_channels.get('worksheet') else "*Not Configured*", inline=False)
    
    ping_display = ", ".join(logger_service.ping_targets) if logger_service.ping_targets else "*None Configured*"
    embed.add_field(name="7. Configured 3-Strike Alert Pings", value=ping_display, inline=False)
    
    embed.set_footer(text="Admin Log Setup • Changes persist automatically across restarts")
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, selected_category: str = "claim"):
        self.selected_category = selected_category
        options = [
            discord.SelectOption(label="1. Influencer Claim Logs", value="claim", default=(selected_category == "claim")),
            discord.SelectOption(label="2. Strike Event Logs", value="strike", default=(selected_category == "strike")),
            discord.SelectOption(label="3. 3-Strike Alert Logs", value="strike_3", default=(selected_category == "strike_3")),
            discord.SelectOption(label="4. Worker Add Logs", value="worker_add", default=(selected_category == "worker_add")),
            discord.SelectOption(label="5. Worker Remove Logs", value="worker_remove", default=(selected_category == "worker_remove")),
            discord.SelectOption(label="6. Worksheet Changes Logs", value="worksheet", default=(selected_category == "worksheet")),
        ]
        super().__init__(
            placeholder="Select a log category to configure manually...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="setup_logs_cat_select"
        )

    async def callback(self, interaction: discord.Interaction):
        cat = self.values[0]
        embed = build_setup_logs_embed(active_category=cat)
        await interaction.response.edit_message(embed=embed, view=SetupLogsView(active_category=cat))


class LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, category: str = "claim"):
        self.category = category
        super().__init__(
            placeholder=f"Select channel for {category.replace('_', ' ').title()}...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            custom_id="setup_logs_ch_select"
        )

    async def callback(self, interaction: discord.Interaction):
        selected_ch = self.values[0]
        logger_service.set_log_channel(self.category, selected_ch.id)
        
        embed = build_setup_logs_embed(active_category=self.category)
        await interaction.response.edit_message(embed=embed, view=SetupLogsView(active_category=self.category))
        await interaction.followup.send(f"Configured **{self.category.replace('_', ' ').title()}** log channel to {selected_ch.mention}.", ephemeral=True)


class PingTargetSelect(discord.ui.MentionableSelect):
    def __init__(self):
        super().__init__(
            placeholder="Select roles or members to ping on 3-strike alerts...",
            min_values=0,
            max_values=10,
            custom_id="setup_logs_ping_select"
        )

    async def callback(self, interaction: discord.Interaction):
        target_mentions = [val.mention for val in self.values]
        logger_service.set_ping_targets(target_mentions)
        mentions_str = ", ".join(target_mentions) if target_mentions else "*None (Pings cleared)*"
        embed = build_setup_logs_embed()
        await interaction.response.edit_message(embed=embed, view=SetupLogsView())
        await interaction.followup.send(f"Configured 3-strike alert ping targets to: {mentions_str}", ephemeral=True)


class AutoSetupLogsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Auto-Setup Audit Log Channels",
            style=discord.ButtonStyle.success,
            custom_id="setup_logs_auto_btn"
        )

    async def callback(self, interaction: discord.Interaction):
        await safe_respond(interaction, "Scanning guild for audit log category and channels...", ephemeral=True)
        guild = interaction.guild
        if not guild:
            return

        # 1. Search for any existing audit log category in the guild (case-insensitive)
        category = None
        for cat in guild.categories:
            c_name = cat.name.lower()
            if "audit" in c_name and "log" in c_name:
                category = cat
                break

        # If no audit category exists at all, create "AUDIT LOGS"
        if not category:
            try:
                category = await guild.create_category("AUDIT LOGS")
            except Exception as e:
                await interaction.followup.send(f"Error creating category: {e}", ephemeral=True)
                return

        # 2. Define flexible channel keywords for matching pre-existing channels
        channel_spec = {
            "claim": {
                "default_name": "influencer-claim-logs",
                "keywords": ["influencer-claim", "claim-log"]
            },
            "strike": {
                "default_name": "staff-strike-logs",
                "keywords": ["staff-strike", "strike-event", "strike-log"]
            },
            "strike_3": {
                "default_name": "three-strike-alert-logs",
                "keywords": ["three-strike", "3-strike", "strike-3", "strike-alert"]
            },
            "worker_add": {
                "default_name": "worker-add-logs",
                "keywords": ["worker-add"]
            },
            "worker_remove": {
                "default_name": "worker-remove-logs",
                "keywords": ["worker-remove", "worker-unclaim"]
            },
            "worksheet": {
                "default_name": "worksheet-changes-logs",
                "keywords": ["worksheet-change", "worksheet-log", "sheet-change"]
            }
        }

        created_text = []
        newly_created_count = 0
        existing_count = 0

        for cat_key, spec in channel_spec.items():
            default_ch_name = spec["default_name"]
            keywords = spec["keywords"]
            existing_channel_id = logger_service.log_channels.get(cat_key)
            ch = None

            # Check if previously bound channel ID still exists in guild
            if existing_channel_id:
                try:
                    ch = guild.get_channel(int(existing_channel_id))
                except Exception:
                    ch = None

            # Search within category text channels first
            if not ch and category:
                for text_ch in category.text_channels:
                    ch_name_lower = text_ch.name.lower()
                    if any(kw in ch_name_lower for kw in keywords):
                        ch = text_ch
                        break

            # Search across all text channels in the entire guild
            if not ch:
                for text_ch in guild.text_channels:
                    ch_name_lower = text_ch.name.lower()
                    if any(kw in ch_name_lower for kw in keywords):
                        ch = text_ch
                        break

            # Only if channel is still nowhere to be found, create a new text channel
            if not ch:
                try:
                    ch = await category.create_text_channel(default_ch_name)
                    newly_created_count += 1
                    status_tag = "(Newly Created)"
                except Exception as e:
                    print(f"Error creating text channel #{default_ch_name}: {e}")
                    continue
            else:
                existing_count += 1
                status_tag = "(Already Exists)"

            logger_service.set_log_channel(cat_key, ch.id)
            created_text.append(f"• **{cat_key.replace('_', ' ').title()}**: {ch.mention} `{status_tag}`")

        embed = build_setup_logs_embed(active_category="strike_3")
        await interaction.message.edit(embed=embed, view=SetupLogsView(active_category="strike_3"))
        
        category_display_name = category.name if category else "AUDIT LOGS"
        summary_msg = (
            f"**Auto-Setup Complete!** Audit category `{category_display_name}` scan finished:\n"
            f"• **Created:** `{newly_created_count}` missing channel(s)\n"
            f"• **Preserved & Bound:** `{existing_count}` existing channel(s)\n\n"
            + "\n".join(created_text)
            + "\n\n**Action Required for 3-Strike Alert Pings:**\nUse the dropdown menu below (`Select roles or members to ping on 3-strike alerts...`) to choose which roles or members should be notified when a worker hits 3 strikes!"
        )
        await interaction.followup.send(summary_msg, ephemeral=False)


class SetupLogsView(discord.ui.View):
    def __init__(self, active_category: str = "claim"):
        super().__init__(timeout=300)
        self.add_item(AutoSetupLogsButton())
        self.add_item(CategorySelect(selected_category=active_category))
        self.add_item(LogChannelSelect(category=active_category))
        self.add_item(PingTargetSelect())


@bot.tree.command(name="setup-logs", description="Configure channels and ping targets for audit logging (Admin only).")
@app_commands.checks.has_permissions(administrator=True)
async def setup_logs_slash(interaction: discord.Interaction):
    embed = build_setup_logs_embed(active_category="claim")
    await interaction.response.send_message(embed=embed, view=SetupLogsView(active_category="claim"), ephemeral=False)


# ----------------------------------------------------
# STRIKE MANAGEMENT SLASH & PREFIX COMMANDS
# ----------------------------------------------------

@bot.tree.command(name="add_strike", description="Manually issue strike(s) to a query channel worker (Admin only).")
@app_commands.describe(
    channel="The query channel to issue strike(s) to (defaults to current channel)",
    amount="Number of strikes to issue (default 1)",
    reason="Reason for issuing the strike(s)"
)
@app_commands.checks.has_permissions(administrator=True)
async def add_strike_slash(interaction: discord.Interaction, channel: discord.TextChannel = None, amount: int = 1, reason: str = "Admin manual strike issuance"):
    target_channel = channel or interaction.channel
    if not is_query_channel(target_channel):
        await safe_respond(interaction, content="`/add_strike` must target a query channel (`#-query` or `#-queries`).", ephemeral=True)
        return

    added = await strike_tracker.add_strikes(target_channel, amount=amount, reason=reason, admin_user=interaction.user)
    await safe_respond(interaction, content=f"Successfully issued {added} strike(s) to {target_channel.mention}. Reason: {reason}", ephemeral=True)


@bot.command(name="add_strike")
@commands.has_permissions(administrator=True)
async def add_strike_prefix(ctx: commands.Context, amount: int = 1, *, reason: str = "Admin manual strike issuance"):
    if not is_query_channel(ctx.channel):
        await ctx.send("This command can only be used inside a query channel.")
        return

    added = await strike_tracker.add_strikes(ctx.channel, amount=amount, reason=reason, admin_user=ctx.author)
    await ctx.send(f"Successfully issued {added} strike(s) to {ctx.channel.mention}. Reason: {reason}")


@bot.tree.command(name="remove_strike", description="Manually remove strikes from a query channel worker (Admin only).")
@app_commands.describe(
    channel="The query channel to remove strike(s) from (defaults to current channel)",
    amount="Number of strikes to remove (default 1)",
    reason="Reason for removing the strike(s)"
)
@app_commands.checks.has_permissions(administrator=True)
async def remove_strike_slash(interaction: discord.Interaction, channel: discord.TextChannel = None, amount: int = 1, reason: str = "Admin manual removal"):
    target_channel = channel or interaction.channel
    if not is_query_channel(target_channel):
        await safe_respond(interaction, content="`/remove_strike` must target a query channel (`#-query` or `#-queries`).", ephemeral=True)
        return

    removed = await strike_tracker.remove_strikes(target_channel, amount=amount, reason=reason, admin_user=interaction.user)
    if removed > 0:
        await safe_respond(interaction, content=f"Successfully removed {removed} strike(s) from {target_channel.mention}. Reason: {reason}", ephemeral=True)
    else:
        await safe_respond(interaction, content=f"No active strikes to remove in {target_channel.mention}.", ephemeral=True)


@bot.command(name="remove_strike")
@commands.has_permissions(administrator=True)
async def remove_strike_prefix(ctx: commands.Context, amount: int = 1, *, reason: str = "Admin manual removal"):
    if not is_query_channel(ctx.channel):
        await ctx.send("This command can only be used inside a query channel.")
        return

    removed = await strike_tracker.remove_strikes(ctx.channel, amount=amount, reason=reason, admin_user=ctx.author)
    if removed > 0:
        await ctx.send(f"Successfully removed {removed} strike(s) from {ctx.channel.mention}. Reason: {reason}")
    else:
        await ctx.send(f"No active strikes to remove in {ctx.channel.mention}.")


# ----------------------------------------------------
# RESEND DASHBOARD SLASH & PREFIX COMMANDS
# ----------------------------------------------------

from pinned_dashboard import resend_channel_dashboard

async def _handle_resend_dashboard(interaction: discord.Interaction, channel: discord.TextChannel = None, all_channels: bool = False):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    guild = interaction.guild
    if not guild:
        await safe_respond(interaction, content="This command must be used within a server.", ephemeral=True)
        return

    if all_channels:
        count = 0
        for ch in guild.text_channels:
            if is_query_channel(ch):
                await resend_channel_dashboard(ch)
                count += 1
        await safe_respond(interaction, content=f"Successfully resent dashboard panels in `{count}` query channel(s).", ephemeral=True)
        return

    target_channel = channel or interaction.channel
    if not is_query_channel(target_channel):
        await safe_respond(interaction, content="This command must target a query channel (`#-query` or `#-queries`).", ephemeral=True)
        return

    await resend_channel_dashboard(target_channel)
    await safe_respond(interaction, content=f"Successfully resent and re-pinned dashboard panel in {target_channel.mention}.", ephemeral=True)


@bot.tree.command(name="resend_dashboard", description="Resend and re-pin fresh dashboard panel(s) in query channel(s) (Admin only).")
@app_commands.describe(
    channel="Specific query channel to resend dashboard panel in (defaults to current channel)",
    all_channels="Set to True to resend dashboard panels in ALL query channels"
)
@app_commands.checks.has_permissions(administrator=True)
async def resend_dashboard_slash(interaction: discord.Interaction, channel: discord.TextChannel = None, all_channels: bool = False):
    await _handle_resend_dashboard(interaction, channel=channel, all_channels=all_channels)


@bot.tree.command(name="resend_panels", description="Resend and re-pin fresh dashboard panel(s) in query channel(s) (Admin only).")
@app_commands.describe(
    channel="Specific query channel to resend dashboard panel in (defaults to current channel)",
    all_channels="Set to True to resend dashboard panels in ALL query channels"
)
@app_commands.checks.has_permissions(administrator=True)
async def resend_panels_slash(interaction: discord.Interaction, channel: discord.TextChannel = None, all_channels: bool = False):
    await _handle_resend_dashboard(interaction, channel=channel, all_channels=all_channels)


@bot.command(name="resend_dashboard", aliases=["resend_panels", "resend_dashboard_panels"])
@commands.has_permissions(administrator=True)
async def resend_dashboard_prefix(ctx: commands.Context, channel: discord.TextChannel = None):
    target_channel = channel or ctx.channel
    if not is_query_channel(target_channel):
        await ctx.send("This command must target a query channel (`#-query` or `#-queries`).")
        return

    await resend_channel_dashboard(target_channel)
    await ctx.send(f"Successfully resent and re-pinned dashboard panel in {target_channel.mention}.")


# ----------------------------------------------------
# SPEED TIME SLASH COMMAND (/speed_time)
# ----------------------------------------------------

active_speed_tasks = {}

@bot.tree.command(name="speed_time", description="Simulate/accelerate bot time for query channels (e.g. 60 real seconds = 12h bot time).")
@app_commands.describe(
    real_time_seconds="Real time interval in seconds (e.g. 60 for continuous acceleration, or 0 for instant jump)",
    bot_time="Amount of bot time to advance (e.g. 12h, 7d, 12d, 1m, 50s)"
)
@app_commands.checks.has_permissions(administrator=True)
async def speed_time_slash(interaction: discord.Interaction, real_time_seconds: float, bot_time: str):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=False)
    except Exception:
        pass

    if not is_query_channel(interaction.channel):
        await safe_respond(interaction, content="`/speed_time` can only be used inside query channels (`#-query` or `#-queries`).", ephemeral=True)
        return

    try:
        hours_to_add = parse_time_string_to_hours(bot_time)
    except ValueError as err:
        await safe_respond(interaction, content=f"{str(err)}", ephemeral=True)
        return

    channel = interaction.channel
    channel_id = str(channel.id)

    if real_time_seconds <= 0:
        await strike_tracker.simulate_time_travel(channel, hours_to_add)
        await strike_tracker.audit_channel(channel, bot)
        await safe_respond(
            interaction,
            content=f"**Instant Time Jump!** Fast-forwarded bot time by **{bot_time}** ({hours_to_add:.2f} hours) for {channel.mention}!"
        )
        return

    if channel_id in active_speed_tasks and not active_speed_tasks[channel_id].done():
        active_speed_tasks[channel_id].cancel()

    async def speed_loop():
        try:
            while True:
                await asyncio.sleep(real_time_seconds)
                print(f"Accelerating time for #{channel.name}: +{hours_to_add:.2f}h every {real_time_seconds}s...")
                await strike_tracker.simulate_time_travel(channel, hours_to_add)
                await strike_tracker.audit_channel(channel, bot)
        except asyncio.CancelledError:
            print(f"Speed time loop cancelled for #{channel.name}.")
        except Exception as e:
            print(f"Error in speed_loop for #{channel.name}: {e}")

    task = asyncio.create_task(speed_loop())
    active_speed_tasks[channel_id] = task

    embed = discord.Embed(
        title="Time Acceleration Activated!",
        description=(
            f"Accelerating bot clock for {channel.mention}:\n"
            f"• **Real Time Interval:** Every `{real_time_seconds}` second(s)\n"
            f"• **Bot Time Advance:** `+{bot_time}` (`{hours_to_add:.2f}` hours per cycle)"
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text="Use /stop_speed_time to return to standard time.")
    await safe_respond(interaction, embed=embed)


@bot.tree.command(name="stop_speed_time", description="Stop time acceleration for the current query channel.")
@app_commands.checks.has_permissions(administrator=True)
async def stop_speed_time_slash(interaction: discord.Interaction):
    if not is_query_channel(interaction.channel):
        await safe_respond(interaction, content="`/stop_speed_time` can only be used inside query channels.", ephemeral=True)
        return

    channel_id = str(interaction.channel_id)
    if channel_id in active_speed_tasks and not active_speed_tasks[channel_id].done():
        active_speed_tasks[channel_id].cancel()
        del active_speed_tasks[channel_id]
        await safe_respond(interaction, content="**Time acceleration stopped.** Returned to real-time clock.")
    else:
        await safe_respond(interaction, content="No active time acceleration loop running for this channel.", ephemeral=True)


# ----------------------------------------------------
# SLASH COMMANDS (/claim & /check)
# ----------------------------------------------------

@bot.tree.command(name="claim", description="Claim an influencer profile/link and register them in the database.")
@app_commands.describe(
    link="The profile link or handle of the influencer",
    platform="Choose platform: Telegram, WhatsApp, Instagram, or Discord"
)
@app_commands.choices(platform=[
    app_commands.Choice(name="Telegram", value="Telegram"),
    app_commands.Choice(name="WhatsApp", value="WhatsApp"),
    app_commands.Choice(name="Instagram", value="Instagram"),
    app_commands.Choice(name="Discord", value="Discord"),
])
async def claim_slash(
    interaction: discord.Interaction,
    link: str,
    platform: app_commands.Choice[str]
):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=False)
    except Exception:
        pass
    
    platform_str = platform.value if isinstance(platform, app_commands.Choice) else (platform or "Instagram")
    clean_url = normalize_link(link)
    if not clean_url:
        await safe_respond(interaction, content=" **Invalid Link/Handle!** Please provide a valid URL or handle.", ephemeral=True)
        return

    referrer_name = f"{interaction.user.name} ({interaction.user.display_name})"
    channel_link = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}"

    try:
        existing = await sheets_manager.check_influencer(clean_url, platform=platform_str)
        if existing:
            embed = discord.Embed(
                title="Influencer Already Claimed!",
                description=f"The **[{existing.get('platform', platform_str)}]** link/handle `{clean_url}` has already been registered in the database.",
                color=discord.Color.gold()
            )
            embed.add_field(name="Platform", value=existing.get("platform", platform_str), inline=True)
            embed.add_field(name="Link / Handle", value=clean_url, inline=False)
            embed.add_field(name="Claimed By", value=existing.get("claimed_by", "Unknown"), inline=True)
            embed.add_field(name="Date Claimed", value=existing.get("claimed_at", "Unknown"), inline=True)
            if existing.get("channel_link"):
                embed.add_field(name="Channel Link", value=existing.get("channel_link"), inline=False)
            
            await safe_respond(interaction, embed=embed)
            return

        res = await sheets_manager.register_influencer(clean_url, referrer_name, channel_link, platform=platform_str)
        
        embed = discord.Embed(
            title="Influencer Successfully Claimed!",
            description=f"Successfully registered **[{platform_str}]** `{clean_url}` under **{referrer_name}**.",
            color=discord.Color.green()
        )
        embed.add_field(name="Platform", value=platform_str, inline=True)
        embed.add_field(name="Link / Handle", value=clean_url, inline=False)
        embed.add_field(name="Registered To", value=referrer_name, inline=True)
        embed.add_field(name="Claim Date", value=res.get("claimed_at"), inline=True)
        embed.set_footer(text="Google Sheets Database Updated")
        
        await safe_respond(interaction, embed=embed)

        await logger_service.log_claim(
            bot, interaction.guild_id, clean_url, referrer_name, channel_link, res.get("claimed_at"), platform=platform_str
        )

    except Exception as e:
        print(f"Error executing /claim: {e}")
        await safe_respond(interaction, content=f"Error accessing database: {str(e)}", ephemeral=True)


@bot.tree.command(name="check", description="Check if an influencer has already been claimed in the database.")
@app_commands.describe(
    link="The profile link or handle to check in the database",
    platform="Choose platform: Telegram, WhatsApp, Instagram, or Discord"
)
@app_commands.choices(platform=[
    app_commands.Choice(name="Telegram", value="Telegram"),
    app_commands.Choice(name="WhatsApp", value="WhatsApp"),
    app_commands.Choice(name="Instagram", value="Instagram"),
    app_commands.Choice(name="Discord", value="Discord"),
])
async def check_slash(
    interaction: discord.Interaction,
    link: str,
    platform: app_commands.Choice[str] = None
):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=False)
    except Exception:
        pass
    
    platform_str = platform.value if isinstance(platform, app_commands.Choice) else (platform or "Instagram")
    clean_url = normalize_link(link)
    if not clean_url:
        await safe_respond(interaction, content="Invalid Link/Handle!", ephemeral=True)
        return

    try:
        existing = await sheets_manager.check_influencer(clean_url, platform=platform_str)
        if existing:
            embed = discord.Embed(
                title="Influencer Already Claimed!",
                description=f"The **[{existing.get('platform', platform_str)}]** link/handle `{clean_url}` is already claimed in the database.",
                color=discord.Color.gold()
            )
            embed.add_field(name="Platform", value=existing.get("platform", platform_str), inline=True)
            embed.add_field(name="Link / Handle", value=clean_url, inline=False)
            embed.add_field(name="Claimed By", value=existing.get("claimed_by", "Unknown"), inline=True)
            embed.add_field(name="Date Claimed", value=existing.get("claimed_at", "Unknown"), inline=True)
            if existing.get("channel_link"):
                embed.add_field(name="Channel Link", value=existing.get("channel_link"), inline=False)
            await safe_respond(interaction, embed=embed)
        else:
            embed = discord.Embed(
                title="Influencer Unclaimed!",
                description=f"No record found for **[{platform_str}]** `{clean_url}`. Available to claim using `/claim`!",
                color=discord.Color.green()
            )
            embed.add_field(name="Platform", value=platform_str, inline=True)
            embed.add_field(name="Link / Handle", value=clean_url, inline=False)
            embed.set_footer(text="Database Check Complete • Available to Claim")
            await safe_respond(interaction, embed=embed)
    except Exception as e:
        print(f"Error executing /check: {e}")
        await safe_respond(interaction, content=f"Error checking database: {str(e)}", ephemeral=True)


# PREFIX COMMANDS (!claim, !check, !sync)
@bot.command(name="sync")
@commands.has_permissions(administrator=True)
async def sync_prefix(ctx: commands.Context):
    """Instantly syncs slash commands to the current server."""
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    cmd_list = [cmd.name for cmd in synced]
    await ctx.send(f" **Instantly synced {len(synced)} slash command(s) to this server!**\nCommands available: `{', '.join(cmd_list)}`")


@bot.command(name="claim")
async def claim_prefix(ctx: commands.Context, link: str = None, platform: str = "Instagram"):
    if not link:
        await ctx.send("Usage: `!claim <link_or_handle> [Telegram|WhatsApp|Instagram|Discord]`")
        return
    clean_url = normalize_link(link)
    platform_cap = platform.capitalize()
    if platform_cap not in ["Telegram", "Whatsapp", "Instagram", "Discord"]:
        platform_cap = "Instagram"
    if platform_cap == "Whatsapp":
        platform_cap = "WhatsApp"

    referrer_name = f"{ctx.author.name} ({ctx.author.display_name})"
    channel_link = f"https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}"

    try:
        existing = await sheets_manager.check_influencer(clean_url, platform=platform_cap)
        if existing:
            embed = discord.Embed(
                title="Influencer Already Claimed!",
                description=f"The **[{existing.get('platform', platform_cap)}]** link/handle `{clean_url}` is already registered.",
                color=discord.Color.gold()
            )
            embed.add_field(name="Platform", value=existing.get("platform", platform_cap), inline=True)
            embed.add_field(name="Claimed By", value=existing.get("claimed_by", "Unknown"), inline=True)
            embed.add_field(name="Date Claimed", value=existing.get("claimed_at", "Unknown"), inline=True)
            await ctx.send(embed=embed)
            return

        res = await sheets_manager.register_influencer(clean_url, referrer_name, channel_link, platform=platform_cap)
        embed = discord.Embed(
            title="Influencer Successfully Claimed!",
            description=f"Successfully registered **[{platform_cap}]** `{clean_url}` under **{referrer_name}**.",
            color=discord.Color.green()
        )
        embed.add_field(name="Platform", value=platform_cap, inline=True)
        embed.add_field(name="Claim Date", value=res.get("claimed_at"), inline=True)
        await ctx.send(embed=embed)

        await logger_service.log_claim(
            bot, ctx.guild.id, clean_url, referrer_name, channel_link, res.get("claimed_at"), platform=platform_cap
        )

    except Exception as e:
        await ctx.send(f"Error accessing database: {str(e)}")


@bot.command(name="check")
async def check_prefix(ctx: commands.Context, ig_link: str = None):
    if not ig_link:
        await ctx.send("Usage: `!check <instagram_link>`")
        return
    clean_url = normalize_ig_link(ig_link)
    if not clean_url or "instagram.com" not in clean_url:
        await ctx.send("Invalid Instagram Link!")
        return
    try:
        existing = await sheets_manager.check_influencer(clean_url)
        if existing:
            embed = discord.Embed(
                title="Influencer Already Claimed!",
                description=f"The Instagram link `{clean_url}` is already claimed by **{existing.get('claimed_by')}** on `{existing.get('claimed_at')}`.",
                color=discord.Color.gold()
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="Influencer Unclaimed!",
                description=f"`{clean_url}` is **available to claim** using `!claim` or `/claim`!",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error checking database: {str(e)}")

class HelpCategorySelect(discord.ui.Select):
    def __init__(self, current_category: str = "worker"):
        options = [
            discord.SelectOption(label="1. Worker Commands & Daily Logging", value="worker", default=(current_category == "worker")),
            discord.SelectOption(label="2. Influencer Claims & Checks", value="influencer", default=(current_category == "influencer")),
            discord.SelectOption(label="3. Admin Strike Management", value="strike_admin", default=(current_category == "strike_admin")),
            discord.SelectOption(label="4. Audit Logs Setup & Testing", value="setup_testing", default=(current_category == "setup_testing")),
        ]
        super().__init__(
            placeholder="Select a help topic to view commands...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="v2_help_cat_select"
        )

    async def callback(self, interaction: discord.Interaction):
        cat = self.values[0]
        v2_help_view = build_help_v2_layout(category=cat)
        await interaction.response.edit_message(view=v2_help_view)


def build_help_v2_layout(category: str = "worker") -> discord.ui.LayoutView:
    """Constructs a clean Discord Components V2 help guide card with zero emojis and normal hyphens."""
    layout_view = discord.ui.LayoutView()
    container = discord.ui.Container(accent_color=discord.Color.blue())

    container.add_item(discord.ui.TextDisplay("## Bot Command Guide & Documentation"))
    container.add_item(discord.ui.Separator())

    if category == "worker":
        container.add_item(discord.ui.TextDisplay(
            "- **__Worker Commands & Daily Logging:__**\n"
            "- **Log DMs (Today):** Use the dropdown menu on the pinned query channel dashboard to submit daily DM counts.\n"
            "- **Screen Recording Proof:** Post an `.mp4` / `.mov` file or video link in your query channel before 1:00 AM IST daily.\n"
            "- **View Stats:** Click the green **View Stats** button on your dashboard to see your 7-day performance log and strike history."
        ))
    elif category == "influencer":
        container.add_item(discord.ui.TextDisplay(
            "- **__Influencer Registration Commands:__**\n"
            "- **/claim <ig_link>:** Claims an influencer's Instagram URL and registers it in the Google Spreadsheet database.\n"
            "- **/check <ig_link>:** Checks if an Instagram URL has already been claimed by another staff member.\n"
            "- **Prefix Commands:** `!claim <link>` and `!check <link>` are also supported."
        ))
    elif category == "strike_admin":
        container.add_item(discord.ui.TextDisplay(
            "- **__Admin Strike Management:__**\n"
            "- **/add_strike [channel] [amount] [reason]:** Manually issue active strike(s) to a query channel worker.\n"
            "- **/remove_strike [channel] [amount] [reason]:** Manually remove active strike(s) from a query channel worker.\n"
            "- **Undo Last Strike Button:** Click the red **Undo Last Strike** button on the query channel dashboard to revoke the latest strike.\n"
            "- **Unclaim Channel Button:** Admins can unclaim a channel worker via the top dashboard button.\n"
            "- **Prefix Commands:** `!add_strike <amount> <reason>` and `!remove_strike <amount> <reason>` are also supported."
        ))
    elif category == "setup_testing":
        container.add_item(discord.ui.TextDisplay(
            "- **__Audit Logs Setup & Testing:__**\n"
            "- **/setup-logs:** Auto-create and bind the 5 audit log channels (`#influencer-claim-logs`, `#staff-strike-logs`, `#worker-add-logs`, `#worker-remove-logs`, `#worksheet-changes-logs`).\n"
            "- **/speed_time <seconds> <time>:** Advance bot clock to test 24h deadline strikes (e.g. `/speed_time 0 12h`).\n"
            "- **/stop_speed_time:** Stop clock acceleration and return to real time.\n"
            "- **/sync:** Instantly sync all slash commands to the current server."
        ))

    container.add_item(discord.ui.Separator())

    select_row = discord.ui.ActionRow()
    select_row.add_item(HelpCategorySelect(current_category=category))
    container.add_item(select_row)

    layout_view.add_item(container)
    return layout_view


@bot.tree.command(name="help", description="View interactive bot documentation and command guide.")
async def help_slash(interaction: discord.Interaction):
    v2_help_view = build_help_v2_layout(category="worker")
    await interaction.response.send_message(view=v2_help_view, ephemeral=True)


@bot.command(name="help")
async def help_prefix(ctx: commands.Context):
    v2_help_view = build_help_v2_layout(category="worker")
    await ctx.send(view=v2_help_view)


# DEV TESTING COMMANDS
@bot.command(name="test_audit")
@commands.has_permissions(administrator=True)
async def test_audit(ctx: commands.Context):
    await ctx.send("Running manual strike & revocation audit on all query channels...")
    await strike_tracker.audit_all_query_channels(bot)
    await ctx.send("Manual audit completed!")

@bot.command(name="test_fastforward")
@commands.has_permissions(administrator=True)
async def test_fastforward(ctx: commands.Context, hours: float):
    if not is_query_channel(ctx.channel):
        await ctx.send("This command can only be used inside a query channel.")
        return
    await strike_tracker.simulate_time_travel(ctx.channel, hours)
    await ctx.send(f"Fast-forwarded time by {hours} hours for {ctx.channel.mention}! Running audit now...")
    await strike_tracker.audit_all_query_channels(bot)

@bot.command(name="test_clear_strikes")
@commands.has_permissions(administrator=True)
async def test_clear_strikes(ctx: commands.Context):
    if not is_query_channel(ctx.channel):
        await ctx.send("This command can only be used inside a query channel.")
        return
    channel_id = str(ctx.channel.id)
    if channel_id in strike_tracker.channel_states:
        state = strike_tracker.channel_states[channel_id]
        state["active_strikes"] = 0
        state["strike_dates"] = []
        state["s1_date"] = ""
        state["s2_date"] = ""
        state["s3_date"] = ""
        await sheets_manager.update_staff_record(
            channel_id=channel_id,
            worker_name=state["worker_name"],
            worker_user_id=state.get("worker_user_id") or "",
            channel_link=f"https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}",
            active_strikes=0,
            s1_date="", s2_date="", s3_date="",
            last_video_date=state["last_video_dt"].strftime("%Y-%m-%d %H:%M:%S UTC") if state["last_video_dt"] else ""
        )
        await update_pinned_dashboard(ctx.channel, state["worker_name"], state.get("worker_user_id"), 0, [], state["last_video_dt"])
        await ctx.send("Active strikes cleared!")


# ----------------------------------------------------
# BACKGROUND AUDIT TASK
# ----------------------------------------------------

@tasks.loop(minutes=AUDIT_INTERVAL_MINUTES)
async def periodic_strike_audit():
    try:
        await strike_tracker.audit_all_query_channels(bot)
    except Exception as e:
        print(f"Warning in periodic_strike_audit loop: {e}")

@periodic_strike_audit.before_loop
async def before_strike_audit():
    await bot.wait_until_ready()


# ----------------------------------------------------
# EVENTS & LISTENERS
# ----------------------------------------------------

@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    """Fired instantly when a new channel is created in the server."""
    if isinstance(channel, discord.TextChannel) and is_query_channel(channel):
        print(f" New query channel created: #{channel.name}. Instantly initializing V2 Dashboard!")
        await asyncio.sleep(0.5)
        try:
            from pinned_dashboard import resend_channel_dashboard
            await resend_channel_dashboard(channel)
        except Exception as e:
            print(f"Error initializing dashboard for new channel #{channel.name}: {e}")

@bot.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
    """Fired instantly when a channel is renamed or updated."""
    if isinstance(after, discord.TextChannel) and is_query_channel(after) and not is_query_channel(before):
        print(f" Channel renamed to query channel: #{after.name}. Instantly initializing V2 Dashboard!")
        await asyncio.sleep(0.5)
        try:
            from pinned_dashboard import resend_channel_dashboard
            await resend_channel_dashboard(after)
        except Exception as e:
            print(f"Error initializing dashboard for updated channel #{after.name}: {e}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
        
    if is_query_channel(message.channel) and is_video_message(message):
        print(f"Video detected in {message.channel.name} from {message.author.name}")
        await strike_tracker.handle_video_submission(message)
        try:
            await message.add_reaction("✅")
        except Exception:
            pass

    await bot.process_commands(message)


@bot.event
async def on_ready():
    print(f"==================================================")
    print(f"Bot is online as {bot.user} (ID: {bot.user.id})")
    print(f"==================================================")
    
    bot.add_view(DashboardLayoutView())
    sheets_manager.set_bot(bot)
    strike_tracker.set_bot(bot)

    # Initialize all existing query channels on startup
    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced_guild = await bot.tree.sync(guild=guild)
            print(f" Instant synced {len(synced_guild)} slash command(s) to guild '{guild.name}' ({guild.id})")
        except Exception as e:
            print(f"Guild sync warning for {guild.id}: {e}")

        for ch in guild.text_channels:
            if is_query_channel(ch):
                try:
                    await strike_tracker.initialize_channel(ch)
                except Exception as e:
                    print(f"Error initializing query channel #{ch.name} on startup: {e}")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global slash command(s): {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")
        
    if not periodic_strike_audit.is_running():
        periodic_strike_audit.start()
        print(f"Periodic strike audit task started (runs every {AUDIT_INTERVAL_MINUTES} mins).")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("WARNING: DISCORD_TOKEN is not set in .env file.")
    else:
        bot.run(DISCORD_TOKEN)
