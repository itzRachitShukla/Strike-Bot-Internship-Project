import asyncio
import os
import re
import traceback
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DISCORD_TOKEN, GOOGLE_SERVICE_ACCOUNT_FILE, SPREADSHEET_ID, STRIKE_DEADLINE_HOURS, REVOCATION_STREAK_DAYS, AUDIT_INTERVAL_MINUTES
from sheets_manager import sheets_manager, normalize_link, normalize_ig_link
from strike_tracker import strike_tracker, is_query_channel, is_video_message
from pinned_dashboard import update_pinned_dashboard, DashboardLayoutView
from logger_service import logger_service, send_rich_permission_error_v2

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
async def safe_respond(interaction: discord.Interaction, content: str = None, embed: discord.Embed = None, view: discord.ui.View = None, ephemeral: bool = False):
    try:
        kwargs = {}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view
        kwargs["ephemeral"] = ephemeral

        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except discord.NotFound:
        if interaction.channel:
            if "ephemeral" in kwargs:
                del kwargs["ephemeral"]
            await interaction.channel.send(**kwargs)
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
        "worksheet": "6. Worksheet Changes Logs",
        "error": "7. Bot Error Logs"
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
    embed.add_field(name="7. Bot Error Logs", value=f"<#{logger_service.log_channels.get('error')}>" if logger_service.log_channels.get('error') else "*Not Configured*", inline=False)
    
    ping_display = ", ".join(logger_service.ping_targets) if logger_service.ping_targets else "*None Configured*"
    embed.add_field(name="8. Configured 3-Strike Alert Pings", value=ping_display, inline=False)
    
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
            discord.SelectOption(label="7. Bot Error Logs", value="error", default=(selected_category == "error")),
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

        if interaction.guild and isinstance(selected_ch, discord.TextChannel):
            admin_overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False, view_channel=False)
            }
            for role in interaction.guild.roles:
                if role.permissions.administrator and not role.is_default():
                    admin_overwrites[role] = discord.PermissionOverwrite(read_messages=True, view_channel=True)
            try:
                await selected_ch.edit(overwrites=admin_overwrites)
            except Exception as pe:
                print(f"Warning setting admin-only overwrites on #{selected_ch.name}: {pe}")
        
        embed = build_setup_logs_embed(active_category=self.category)
        await interaction.response.edit_message(embed=embed, view=SetupLogsView(active_category=self.category))
        await interaction.followup.send(f"Configured **{self.category.replace('_', ' ').title()}** log channel to {selected_ch.mention} (Admin-only view access set).", ephemeral=True)


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

        # Build Administrator-Only Permission Overwrites:
        # Deny @everyone, and grant explicit view permission to administrator roles
        admin_overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False, view_channel=False)
        }
        for role in guild.roles:
            if role.permissions.administrator and not role.is_default():
                admin_overwrites[role] = discord.PermissionOverwrite(read_messages=True, view_channel=True)

        # 1. Search for any existing audit log category in the guild (case-insensitive)
        category = None
        for cat in guild.categories:
            c_name = cat.name.lower()
            if "audit" in c_name and "log" in c_name:
                category = cat
                break

        # If no audit category exists at all, create "AUDIT LOGS" with admin-only overwrites
        if not category:
            try:
                category = await guild.create_category("AUDIT LOGS", overwrites=admin_overwrites)
            except Exception as e:
                await interaction.followup.send(f"Error creating category: {e}", ephemeral=True)
                return
        else:
            try:
                await category.edit(overwrites=admin_overwrites)
            except Exception as pe:
                print(f"Warning updating category overwrites: {pe}")

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
            },
            "error": {
                "default_name": "bot-error-logs",
                "keywords": ["bot-error", "error-log", "bot-errors", "exception-log"]
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
                    ch = await category.create_text_channel(default_ch_name, overwrites=admin_overwrites)
                    newly_created_count += 1
                    status_tag = "(Newly Created)"
                except Exception as e:
                    print(f"Error creating text channel #{default_ch_name}: {e}")
                    continue
            else:
                existing_count += 1
                status_tag = "(Already Exists)"
                try:
                    await ch.edit(overwrites=admin_overwrites)
                except Exception as pe:
                    print(f"Warning applying admin overwrites to #{ch.name}: {pe}")

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


@bot.tree.command(name="resend_panels", description="Resend and re-pin fresh dashboard panel(s) in query channel(s) (Admin only).")
@app_commands.describe(
    channel="Specific query channel to resend dashboard panel in (defaults to current channel)",
    all_channels="Set to True to resend dashboard panels in ALL query channels"
)
@app_commands.checks.has_permissions(administrator=True)
async def resend_panels_slash(interaction: discord.Interaction, channel: discord.TextChannel = None, all_channels: bool = False):
    await _handle_resend_dashboard(interaction, channel=channel, all_channels=all_channels)


@bot.command(name="resend_panels", aliases=["resend_dashboard"])
@commands.has_permissions(administrator=True)
async def resend_panels_prefix(ctx: commands.Context, channel: discord.TextChannel = None):
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
# COMPONENTS V2 LAYOUT VIEWS FOR CLAIMS & CHECKS
# ----------------------------------------------------

class ClaimSuccessView(discord.ui.LayoutView):
    def __init__(self, platform: str, clean_url: str, referrer_mention: str, claimed_at: str, geo: str = "US"):
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_color=discord.Color.green())
        container.add_item(discord.ui.TextDisplay("## Influencer Successfully Claimed!"))
        container.add_item(discord.ui.Separator())
        details_text = (
            f"Successfully registered **{platform}** `{clean_url}` under {referrer_mention}.\n\n"
            f"- **Social Link:** `{clean_url}`\n"
            f"- **Referred By:** {referrer_mention}\n"
            f"- **GEO:** **{geo}**\n"
            f"- **Platform:** **{platform}**\n"
            f"- **Claim Date:** `{claimed_at}`"
        )
        container.add_item(discord.ui.TextDisplay(details_text))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay("*Google Sheets Database Updated*"))
        self.add_item(container)


class AlreadyClaimedView(discord.ui.LayoutView):
    def __init__(self, platform: str, clean_url: str, claimed_by: str, claimed_at: str, channel_link: str = None, geo: str = None):
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_color=discord.Color.gold())
        container.add_item(discord.ui.TextDisplay("## Influencer Already Claimed!"))
        container.add_item(discord.ui.Separator())
        
        claimed_by_str = claimed_by if (claimed_by and claimed_by.startswith("<@")) else f"**{claimed_by}**"
        
        details_text = (
            f"The **{platform}** link/handle `{clean_url}` has already been registered in the database.\n\n"
            f"- **Social Link:** `{clean_url}`\n"
            f"- **Referred By:** {claimed_by_str}\n"
            f"- **Platform:** **{platform}**\n"
            f"- **Date Claimed:** `{claimed_at}`"
        )
        if geo:
            details_text += f"\n- **GEO:** **{geo}**"
        if channel_link:
            details_text += f"\n- **Channel Link:** {channel_link}"
        container.add_item(discord.ui.TextDisplay(details_text))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay("*Database Verification Complete*"))
        self.add_item(container)


class UnclaimedView(discord.ui.LayoutView):
    def __init__(self, platform: str, clean_url: str):
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_color=discord.Color.green())
        container.add_item(discord.ui.TextDisplay("## Influencer Unclaimed!"))
        container.add_item(discord.ui.Separator())
        platform_str = f" **{platform}**" if platform else ""
        details_text = (
            f"No record found for{platform_str} `{clean_url}`.\n\n"
            f"- **Status:** Available to Claim\n"
            f"- **Social Link:** `{clean_url}`"
        )
        if platform:
            details_text += f"\n- **Platform Checked:** **{platform}**"
        container.add_item(discord.ui.TextDisplay(details_text))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay("*Database Check Complete • Available to Claim via /claim*"))
        self.add_item(container)


# ----------------------------------------------------
# SLASH COMMANDS (/claim & /check)
# ----------------------------------------------------

@bot.tree.command(name="claim", description="Claim an influencer profile/link and register them in the database.")
@app_commands.describe(
    link="The profile link or handle of the influencer",
    gc="Choose platform: Telegram, WhatsApp, Instagram, or Discord",
    geo="Choose or type creator GEO/country (e.g. US, IN, UK, CA, AU, DE, FR, BR, JP)"
)
@app_commands.choices(
    gc=[
        app_commands.Choice(name="Telegram", value="Telegram"),
        app_commands.Choice(name="WhatsApp", value="WhatsApp"),
        app_commands.Choice(name="Instagram", value="Instagram"),
        app_commands.Choice(name="Discord", value="Discord"),
    ],
    geo=[
        app_commands.Choice(name="US (United States)", value="US"),
        app_commands.Choice(name="IN (India)", value="IN"),
        app_commands.Choice(name="UK (United Kingdom)", value="UK"),
        app_commands.Choice(name="CA (Canada)", value="CA"),
        app_commands.Choice(name="AU (Australia)", value="AU"),
        app_commands.Choice(name="DE (Germany)", value="DE"),
        app_commands.Choice(name="FR (France)", value="FR"),
        app_commands.Choice(name="BR (Brazil)", value="BR"),
        app_commands.Choice(name="JP (Japan)", value="JP"),
        app_commands.Choice(name="Other", value="Other"),
    ]
)
async def claim_slash(
    interaction: discord.Interaction,
    link: str,
    gc: app_commands.Choice[str],
    geo: str = "US"
):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=False)
    except Exception:
        pass
    
    platform_str = gc.value if isinstance(gc, app_commands.Choice) else (gc or "Instagram")
    geo_str = geo.value if isinstance(geo, app_commands.Choice) else (geo or "US")
    clean_url = normalize_link(link)
    if not clean_url:
        await safe_respond(interaction, content=" **Invalid Link/Handle!** Please provide a valid URL or handle.", ephemeral=True)
        return

    referrer_name = f"{interaction.user.name} ({interaction.user.display_name})"
    referrer_mention = interaction.user.mention
    channel_link = f"https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}"

    try:
        existing = await sheets_manager.check_influencer(clean_url, platform=platform_str)
        if existing:
            claimed_plat = existing.get("platform") or platform_str
            claimed_by = existing.get("claimed_by", "Unknown")
            claimed_at = existing.get("claimed_at", "Unknown")
            ch_link = existing.get("channel_link")
            c_geo = existing.get("geo")
            v2_view = AlreadyClaimedView(claimed_plat, clean_url, claimed_by, claimed_at, ch_link, geo=c_geo)
            await safe_respond(interaction, view=v2_view)
            return

        res = await sheets_manager.register_influencer(clean_url, referrer_name, channel_link, platform=platform_str, geo=geo_str)
        claimed_at = res.get("claimed_at") or ""
        v2_view = ClaimSuccessView(platform_str, clean_url, referrer_mention, claimed_at, geo=geo_str)
        await safe_respond(interaction, view=v2_view)

        await logger_service.log_claim(
            bot, interaction.guild_id, clean_url, referrer_name, channel_link, claimed_at, platform=platform_str
        )

    except Exception as e:
        print(f"Error executing /claim: {e}")
        await safe_respond(interaction, content=f"Error accessing database: {str(e)}", ephemeral=True)


@bot.tree.command(name="check", description="Check if an influencer has already been claimed in the database.")
@app_commands.describe(
    link="The profile link or handle to check in the database",
    gc="Choose platform: Telegram, WhatsApp, Instagram, or Discord"
)
@app_commands.choices(gc=[
    app_commands.Choice(name="Telegram", value="Telegram"),
    app_commands.Choice(name="WhatsApp", value="WhatsApp"),
    app_commands.Choice(name="Instagram", value="Instagram"),
    app_commands.Choice(name="Discord", value="Discord"),
])
async def check_slash(
    interaction: discord.Interaction,
    link: str,
    gc: app_commands.Choice[str] = None
):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=False)
    except Exception:
        pass
    
    platform_str = gc.value if isinstance(gc, app_commands.Choice) else (gc or None)
    clean_url = normalize_link(link)
    if not clean_url:
        await safe_respond(interaction, content="Invalid Link/Handle!", ephemeral=True)
        return

    try:
        existing = await sheets_manager.check_influencer(clean_url, platform=platform_str or "Instagram")
        if existing:
            claimed_plat = existing.get("platform") or platform_str or "Instagram"
            claimed_by = existing.get("claimed_by", "Unknown")
            claimed_at = existing.get("claimed_at", "Unknown")
            ch_link = existing.get("channel_link")
            c_geo = existing.get("geo")
            v2_view = AlreadyClaimedView(claimed_plat, clean_url, claimed_by, claimed_at, ch_link, geo=c_geo)
            await safe_respond(interaction, view=v2_view)
        else:
            v2_view = UnclaimedView(platform_str, clean_url)
            await safe_respond(interaction, view=v2_view)
    except Exception as e:
        print(f"Error executing /check: {e}")
        await safe_respond(interaction, content=f"Error checking database: {str(e)}", ephemeral=True)


# PREFIX COMMANDS (!claim, !check, !sync)
@bot.command(name="sync")
@commands.has_permissions(administrator=True)
async def sync_prefix(ctx: commands.Context):
    """Instantly purges global duplicate commands and syncs slash commands to current server."""
    global_cmds = list(bot.tree.get_commands())
    try:
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
    except Exception as e:
        print(f"Warning purging global commands: {e}")

    for cmd in global_cmds:
        bot.tree.add_command(cmd)

    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    cmd_list = [cmd.name for cmd in synced]
    await ctx.send(
        f" **Instantly purged global duplicates and synced {len(synced)} slash command(s) to this server!**\n"
        f"Commands available: `{', '.join(cmd_list)}`\n"
        f"*(Note: Restart your bot script and press **Ctrl+R** in Discord if your desktop app still caches old duplicates!)*"
    )


@bot.command(name="claim")
async def claim_prefix(ctx: commands.Context, link: str = None, gc: str = "Instagram", geo: str = "US"):
    if not link:
        await ctx.send("Usage: `!claim <link_or_handle> [Telegram|WhatsApp|Instagram|Discord] [GEO]`")
        return
    clean_url = normalize_link(link)
    platform_cap = gc.capitalize()
    if platform_cap not in ["Telegram", "Whatsapp", "Instagram", "Discord"]:
        platform_cap = "Instagram"
    if platform_cap == "Whatsapp":
        platform_cap = "WhatsApp"

    referrer_name = f"{ctx.author.name} ({ctx.author.display_name})"
    referrer_mention = ctx.author.mention
    channel_link = f"https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}"

    try:
        existing = await sheets_manager.check_influencer(clean_url, platform=platform_cap)
        if existing:
            claimed_plat = existing.get("platform") or platform_cap
            claimed_by = existing.get("claimed_by", "Unknown")
            claimed_at = existing.get("claimed_at", "Unknown")
            ch_link = existing.get("channel_link")
            c_geo = existing.get("geo")
            v2_view = AlreadyClaimedView(claimed_plat, clean_url, claimed_by, claimed_at, ch_link, geo=c_geo)
            await ctx.send(view=v2_view)
            return

        res = await sheets_manager.register_influencer(clean_url, referrer_name, channel_link, platform=platform_cap, geo=geo)
        claimed_at = res.get("claimed_at") or ""
        v2_view = ClaimSuccessView(platform_cap, clean_url, referrer_mention, claimed_at, geo=geo)
        await ctx.send(view=v2_view)

        await logger_service.log_claim(
            bot, ctx.guild.id, clean_url, referrer_name, channel_link, claimed_at, platform=platform_cap
        )

    except Exception as e:
        await ctx.send(f"Error accessing database: {str(e)}")


@bot.command(name="check")
async def check_prefix(ctx: commands.Context, link: str = None, gc: str = "Instagram"):
    if not link:
        await ctx.send("Usage: `!check <link_or_handle> [Telegram|WhatsApp|Instagram|Discord]`")
        return
    clean_url = normalize_link(link)
    if not clean_url:
        await ctx.send("Invalid Link or Handle!")
        return
    platform_cap = gc.capitalize()
    if platform_cap not in ["Telegram", "Whatsapp", "Instagram", "Discord"]:
        platform_cap = "Instagram"
    if platform_cap == "Whatsapp":
        platform_cap = "WhatsApp"

    try:
        existing = await sheets_manager.check_influencer(clean_url, platform=platform_cap)
        if existing:
            claimed_plat = existing.get("platform") or platform_cap
            claimed_by = existing.get("claimed_by", "Unknown")
            claimed_at = existing.get("claimed_at", "Unknown")
            ch_link = existing.get("channel_link")
            v2_view = AlreadyClaimedView(claimed_plat, clean_url, claimed_by, claimed_at, ch_link)
            await ctx.send(view=v2_view)
        else:
            v2_view = UnclaimedView(platform_cap, clean_url)
            await ctx.send(view=v2_view)
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
            await strike_tracker.initialize_channel(channel, send_dashboard=True)
        except Exception as e:
            print(f"Error initializing dashboard for new channel #{channel.name}: {e}")

@bot.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
    """Fired instantly when a channel is renamed or updated."""
    if isinstance(after, discord.TextChannel) and is_query_channel(after) and not is_query_channel(before):
        print(f" Channel renamed to query channel: #{after.name}. Instantly initializing V2 Dashboard!")
        await asyncio.sleep(0.5)
        try:
            await strike_tracker.initialize_channel(after, send_dashboard=True)
        except Exception as e:
            print(f"Error initializing dashboard for updated channel #{after.name}: {e}")

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    """Fired instantly when a channel is deleted in the server."""
    if isinstance(channel, discord.TextChannel) and is_query_channel(channel):
        print(f"Query channel deleted: #{channel.name} ({channel.id}). Marking status as Inactive...")
        try:
            await strike_tracker.mark_channel_inactive(str(channel.id), channel.name)
        except Exception as e:
            print(f"Error marking deleted channel #{channel.name} as inactive: {e}")

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

    # 1. Clear global commands on Discord API to prevent double/duplicate slash commands in UI
    global_cmds = list(bot.tree.get_commands())
    try:
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
    except Exception as e:
        print(f"Warning clearing stale global commands: {e}")

    # Restore local commands back to tree
    for cmd in global_cmds:
        bot.tree.add_command(cmd)

    # 2. Sync instant guild-specific slash commands for each connected guild
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

    if not periodic_strike_audit.is_running():
        periodic_strike_audit.start()
        print(f"Periodic strike audit task started (runs every {AUDIT_INTERVAL_MINUTES} mins).")


# ----------------------------------------------------
# GLOBAL BOT ERROR HANDLERS (LOGS TO #bot-error-logs)
# ----------------------------------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    cmd_name = interaction.command.name if interaction.command else "Unknown Slash Command"
    print(f"Slash command error in /{cmd_name}: {error}")
    
    ch_id = interaction.channel_id
    ctx_str = f"Slash Command: `/{cmd_name}` in <#{ch_id}>" if ch_id else f"Slash Command: `/{cmd_name}`"
    if interaction.user:
        ctx_str += f" by {interaction.user.mention}"

    await logger_service.log_error(bot, interaction.guild_id, type(error).__name__, tb_str, context_info=ctx_str)

    if isinstance(error, (app_commands.errors.MissingPermissions, app_commands.errors.BotMissingPermissions, discord.Forbidden)):
        await send_rich_permission_error_v2(interaction, interaction.user, f"/{cmd_name}")
    else:
        err_msg = f"An error occurred while executing `/{cmd_name}`: `{str(error)}`"
        await safe_respond(interaction, content=err_msg, ephemeral=True)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return

    tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    cmd_name = ctx.command.name if ctx.command else "Unknown Prefix Command"
    print(f"Prefix command error in !{cmd_name}: {error}")

    guild_id = ctx.guild.id if ctx.guild else None
    ctx_str = f"Prefix Command: `!{cmd_name}` in <#{ctx.channel.id}>"
    if ctx.author:
        ctx_str += f" by {ctx.author.mention}"

    await logger_service.log_error(bot, guild_id, type(error).__name__, tb_str, context_info=ctx_str)

    if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions, discord.Forbidden)):
        await send_rich_permission_error_v2(ctx, ctx.author, f"!{cmd_name}")
    else:
        await ctx.send(f"An error occurred: `{str(error)}`")


@bot.event
async def on_error(event, *args, **kwargs):
    tb_str = traceback.format_exc()
    print(f"Global event error in {event}: {tb_str}")
    guild_id = None
    if args and isinstance(args[0], discord.Interaction):
        guild_id = args[0].guild_id
    elif args and hasattr(args[0], "guild") and args[0].guild:
        guild_id = args[0].guild.id

    await logger_service.log_error(bot, guild_id, f"Event Error: {event}", tb_str, context_info=f"System Event Handler: `{event}`")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("WARNING: DISCORD_TOKEN is not set in .env file.")
    else:
        bot.run(DISCORD_TOKEN)
