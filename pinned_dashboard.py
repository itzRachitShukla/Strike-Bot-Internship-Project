import discord
from datetime import datetime, timedelta
from sheets_manager import sheets_manager

# Helper for safe defer
async def safe_defer(interaction: discord.Interaction, ephemeral: bool = True):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
    except Exception:
        pass

# --- Interactive DM Input Modal ---
class DMLogModal(discord.ui.Modal):
    def __init__(self, day_num: int):
        super().__init__(title=f"Log DMs Sent - Day {day_num}")
        self.day_num = day_num
        self.dm_input = discord.ui.TextInput(
            label=f"Total DMs sent on Day {day_num}:",
            placeholder="e.g. 50",
            min_length=1,
            max_length=5,
            required=True
        )
        self.add_item(self.dm_input)

    async def on_submit(self, interaction: discord.Interaction):
        await safe_defer(interaction, ephemeral=True)
        channel = interaction.channel
        channel_id = str(channel.id)
        
        from strike_tracker import strike_tracker
        state = strike_tracker.channel_states.get(channel_id, {})
        worker_user_id = state.get("worker_user_id")

        if not worker_user_id:
            await interaction.followup.send("Cannot submit DMs: This channel is currently unclaimed.", ephemeral=True)
            return

        is_admin = interaction.user.guild_permissions.administrator
        is_claimed_worker = (worker_user_id and str(interaction.user.id) == str(worker_user_id))
        if not (is_admin or is_claimed_worker):
            await interaction.followup.send(
                f"Unauthorized: Only the claimed worker (<@{worker_user_id}>) or an Administrator can log DMs for this channel.",
                ephemeral=True
            )
            return

        try:
            count = int(self.dm_input.value.strip())
            if count < 0:
                raise ValueError()
        except ValueError:
            await interaction.followup.send("Please enter a valid non-negative number.", ephemeral=True)
            return

        channel_link = f"https://discord.com/channels/{interaction.guild_id}/{channel.id}"
        worker_name = state.get("worker_name") or channel.name.replace("-query", "").replace("-queries", "").capitalize()

        day_values, total_dms = await sheets_manager.update_dm_record(
            channel_id, worker_name, channel_link, self.day_num, count
        )

        await interaction.followup.send(
            f"Recorded! Day {self.day_num} DMs set to {count}. (7-Day Total: {total_dms} DMs)",
            ephemeral=True
        )

        if channel_id in strike_tracker.channel_states:
            st = strike_tracker.channel_states[channel_id]
            await update_pinned_dashboard(
                channel, st["worker_name"], st.get("worker_user_id"), st["active_strikes"], st["strike_dates"], st["last_video_dt"], day_values, total_dms
            )


# --- Dropdown Menu Component (Strictly Text-Only) ---
class DashboardSelect(discord.ui.Select):
    def __init__(self, current_day: int = 1, disabled: bool = False):
        self.current_day = current_day
        placeholder_text = "Claim channel to enable DM logging..." if disabled else f"Select an action (Log Day {current_day} DMs / View Stats)..."
        options = [
            discord.SelectOption(
                label=f"Log Day {current_day} DMs (Today)" if not disabled else "Log DMs (Claim Channel First)",
                description=f"Submit DM count for active Day {current_day}" if not disabled else "Claim this channel to log DMs",
                value=f"day_{current_day}"
            ),
            discord.SelectOption(
                label="View 7-Day Performance Log",
                description="Show complete 7-day DM and strike stats",
                value="view_stats"
            ),
        ]
        super().__init__(
            placeholder=placeholder_text,
            min_values=1,
            max_values=1,
            options=options,
            disabled=disabled,
            custom_id="v2_dashboard_select_menu"
        )

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        channel_id = str(interaction.channel_id)
        from strike_tracker import strike_tracker
        state = strike_tracker.channel_states.get(channel_id, {})
        worker_user_id = state.get("worker_user_id")

        if val.startswith("day_"):
            if not worker_user_id:
                await safe_defer(interaction, ephemeral=True)
                await interaction.followup.send(" This channel is unclaimed! Click 'Claim Channel' above before logging DMs.", ephemeral=True)
                return

            is_admin = interaction.user.guild_permissions.administrator
            is_claimed_worker = (worker_user_id and str(interaction.user.id) == str(worker_user_id))
            if not (is_admin or is_claimed_worker):
                await safe_defer(interaction, ephemeral=True)
                await interaction.followup.send(
                    f" Unauthorized: Only the claimed worker (<@{worker_user_id}>) or a Server Administrator can log DMs for this channel.",
                    ephemeral=True
                )
                return

            day_num = int(val.split("_")[1])
            await interaction.response.send_modal(DMLogModal(day_num))
        elif val == "view_stats":
            await send_v2_stats_response(interaction)


# --- Text-Only Control Buttons ---

class ClaimChannelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Claim Channel",
            style=discord.ButtonStyle.success,
            custom_id="v2_claim_channel_btn"
        )

    async def callback(self, interaction: discord.Interaction):
        await safe_defer(interaction, ephemeral=True)
        from strike_tracker import strike_tracker
        res = await strike_tracker.claim_channel_worker(interaction.channel, interaction.user)
        if res:
            await interaction.followup.send(f"Success! Channel claimed by {interaction.user.mention}.", ephemeral=True)
        else:
            await interaction.followup.send("Failed to claim channel.", ephemeral=True)


class UndoStrikeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Undo Last Strike",
            style=discord.ButtonStyle.danger,
            custom_id="v2_undo_strike_btn"
        )

    async def callback(self, interaction: discord.Interaction):
        await safe_defer(interaction, ephemeral=True)
        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("Only Administrators can undo strikes.", ephemeral=True)
            return

        from strike_tracker import strike_tracker
        res = await strike_tracker.undo_last_strike(interaction.channel, admin_user=interaction.user)
        if res:
            await interaction.followup.send("Success! The last strike has been undone.", ephemeral=True)
        else:
            await interaction.followup.send("No active strikes to undo for this channel.", ephemeral=True)


class ViewStatsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="View Stats",
            style=discord.ButtonStyle.success,
            custom_id="v2_view_stats_btn"
        )

    async def callback(self, interaction: discord.Interaction):
        await send_v2_stats_response(interaction)


async def send_v2_stats_response(interaction: discord.Interaction):
    """Sends a rich Discord Components V2 performance and strike statistics summary layout."""
    await safe_defer(interaction, ephemeral=True)
    channel = interaction.channel
    channel_id = str(channel.id)
    
    from strike_tracker import strike_tracker
    state = strike_tracker.channel_states.get(channel_id, {})
    worker_user_id = state.get("worker_user_id")
    active_strikes = state.get("active_strikes", 0)
    strike_dates = state.get("strike_dates", [])
    last_video_dt = state.get("last_video_dt")

    worker_display = f"<@{worker_user_id}>" if (worker_user_id and str(worker_user_id).isdigit()) else "Not Claimed"

    # Fetch DM records
    record = await sheets_manager.get_dm_record(channel_id)
    day_values = [int(record.get(f"Day {d} DMs", 0) or 0) for d in range(1, 8)]
    total_dms = sum(day_values)

    current_day = get_current_day_num(channel_id, channel)

    # 1. 7-Day DM Breakdown
    dm_lines = []
    for d in range(1, 8):
        val = day_values[d - 1]
        tag = " (Today)" if d == current_day else ""
        dm_lines.append(f"- Day {d}: {val} DMs{tag}")
    dm_breakdown_str = "\n".join(dm_lines)

    # 2. Strike History Details
    if strike_dates and active_strikes > 0:
        history_text = "\n".join([f"- Strike {i}: Received `{s_date}`" for i, s_date in enumerate(strike_dates[:active_strikes], start=1)])
    else:
        history_text = "No active strikes on record."

    if last_video_dt:
        last_video_str = f"<t:{int(last_video_dt.timestamp())}:F> (<t:{int(last_video_dt.timestamp())}:R>)"
    else:
        last_video_str = "No video submitted yet"

    # Components V2 Layout View with Gold Accent
    stats_layout = discord.ui.LayoutView()
    container = discord.ui.Container(accent_color=discord.Color.gold())

    # Header
    container.add_item(discord.ui.TextDisplay(f"## Performance & Strike Summary: {channel.name}"))
    container.add_item(discord.ui.Separator())

    # Worker & Active Strike Overview
    container.add_item(discord.ui.TextDisplay(
        f"- **__Worker:__** {worker_display}\n"
        f"- **__Active Strikes:__** {active_strikes}/3\n"
        f"- **__Last Screen Recording:__** {last_video_str}"
    ))
    container.add_item(discord.ui.Separator())

    # Strike History Details
    container.add_item(discord.ui.TextDisplay(
        f"- **__Strike History Details:__**\n{history_text}"
    ))
    container.add_item(discord.ui.Separator())

    # 7-Day DM Performance Stats
    container.add_item(discord.ui.TextDisplay(
        f"- **__7-Day DM Stats (Total: {total_dms} DMs):__**\n{dm_breakdown_str}"
    ))

    stats_layout.add_item(container)

    await interaction.followup.send(view=stats_layout, ephemeral=True)


class UnclaimChannelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Unclaim Channel",
            style=discord.ButtonStyle.secondary,
            custom_id="v2_unclaim_channel_btn"
        )

    async def callback(self, interaction: discord.Interaction):
        await safe_defer(interaction, ephemeral=True)
        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("Only Administrators can unclaim channels.", ephemeral=True)
            return

        from strike_tracker import strike_tracker
        res = await strike_tracker.unclaim_channel_worker(interaction.channel, interaction.user)
        if res:
            await interaction.followup.send("Success! Channel unclaimed.", ephemeral=True)
        else:
            await interaction.followup.send("Channel is already unclaimed.", ephemeral=True)


# --- Helper for Current Active Day Calculation ---
def get_current_day_num(channel_id: str, channel: discord.TextChannel = None) -> int:
    from strike_tracker import strike_tracker
    state = strike_tracker.channel_states.get(channel_id, {})
    claim_dt = state.get("claim_dt")
    if not claim_dt and channel and hasattr(channel, "created_at"):
        claim_dt = channel.created_at
    if not claim_dt:
        return 1

    now = strike_tracker._get_effective_now(channel_id)
    if hasattr(claim_dt, "tzinfo") and claim_dt.tzinfo is not None:
        claim_dt = claim_dt.replace(tzinfo=None)
    if hasattr(now, "tzinfo") and now.tzinfo is not None:
        now = now.replace(tzinfo=None)

    total_seconds = (now - claim_dt).total_seconds()
    if total_seconds < 0:
        total_seconds = 0
    days_elapsed = int(total_seconds // 86400)
    return (days_elapsed % 7) + 1


# --- Discord Components V2 Persistent LayoutView ---
class DashboardLayoutView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)


def build_dashboard_v2_layout(
    channel: discord.TextChannel,
    worker_name: str,
    worker_user_id: str | None,
    active_strikes: int,
    strike_dates: list,
    last_video_dt: datetime | None,
    day_values: list = None,
    total_dms: int = 0
) -> discord.ui.LayoutView:
    """
    Constructs a clean Components V2 LayoutView container structure 
    complying strictly with Discord API IS_COMPONENTS_V2 specifications.
    Field-specific control buttons are bound via Section accessories.
    """
    channel_id = str(channel.id)
    current_day = get_current_day_num(channel_id, channel)

    # 1. Worker Display (Inline Horizontal) & Right-Side Action Button (Claim / Unclaim)
    if worker_user_id and str(worker_user_id).isdigit():
        worker_text = f"- **__Worker:__** <@{worker_user_id}>"
        worker_accessory = UnclaimChannelButton()
    else:
        worker_text = "- **__Worker:__** Not Claimed"
        worker_accessory = ClaimChannelButton()

    # 2. Recording & 1 AM IST Window Deadline Timestamps
    from strike_tracker import strike_tracker, get_current_window_bounds
    now_ist = strike_tracker._get_effective_now(channel_id)
    window_start, window_end = get_current_window_bounds(now_ist)
    deadline_str = f"<t:{int(window_end.timestamp())}:F> (<t:{int(window_end.timestamp())}:R>)"

    if last_video_dt:
        last_video_str = f"<t:{int(last_video_dt.timestamp())}:F> (<t:{int(last_video_dt.timestamp())}:R>)"
        if last_video_dt >= window_start:
            video_status = f"Submitted for active window! Next video due before 1:00 AM IST ({deadline_str})"
        else:
            video_status = f"Pending video submission! Due before 1:00 AM IST ({deadline_str})"
    else:
        last_video_str = "No video submitted yet"
        video_status = f"Pending initial video submission! Due before 1:00 AM IST ({deadline_str})"

    # 3. 7-Day DM Performance in Column Format (Highlighting Current Active Day)
    if day_values:
        dm_column_lines = []
        for i, count in enumerate(day_values, start=1):
            tag = " (Today)" if i == current_day else ""
            dm_column_lines.append(f"- Day {i}: {count} DMs{tag}")
        dm_column_str = "\n".join(dm_column_lines)
    else:
        dm_column_lines = [f"- Day {i}: 0 DMs{' (Today)' if i == current_day else ''}" for i in range(1, 8)]
        dm_column_str = "\n".join(dm_column_lines)

    # 4. Strike History Text with Native Hyphen Bullets (No Extra Whitespace)
    if strike_dates and active_strikes > 0:
        history_text = "\n".join([f"- Strike {i}: Received `{s_date}`" for i, s_date in enumerate(strike_dates[:active_strikes], start=1)])
        history_text += "\n*Rule: Active strikes automatically revoke after 7 consecutive days clean.*"
    else:
        history_text = "No active strikes on record."

    # Components V2 Container Layout Assembly with Blue Accent Color
    layout_view = DashboardLayoutView()
    container = discord.ui.Container(accent_color=discord.Color.blue())
    
    # Header Block with View Stats Button on the Right Side
    container.add_item(discord.ui.Section(
        discord.ui.TextDisplay("## Query Channel Status & Strike Dashboard"),
        accessory=ViewStatsButton()
    ))
    container.add_item(discord.ui.Separator())
    
    # Top Section: Worker Status (Horizontal / Inline) + Claim/Unclaim Button on the Right Side
    container.add_item(discord.ui.Section(
        discord.ui.TextDisplay(worker_text),
        accessory=worker_accessory
    ))
    container.add_item(discord.ui.Separator())
    
    # Screen Recording & 24h Deadline Info
    container.add_item(discord.ui.TextDisplay(
        f"- **__Last Screen Recording:__**\n{last_video_str}\n"
        f"- **__24h Deadline Status:__**\n{video_status}"
    ))
    container.add_item(discord.ui.Separator())
    
    # Active Strikes Section + Undo Last Strike Button on the Right Side
    container.add_item(discord.ui.Section(
        discord.ui.TextDisplay(f"- **__Active Strikes:__**\n{active_strikes}/3"),
        accessory=UndoStrikeButton()
    ))
    container.add_item(discord.ui.Separator())

    # Strike History Field Section (Clean Single Newline, No Extra Whitespace)
    container.add_item(discord.ui.TextDisplay(
        f"- **__Strike History:__**\n{history_text}"
    ))
    container.add_item(discord.ui.Separator())

    # 7-Day DM Performance Section (Column Format)
    container.add_item(discord.ui.TextDisplay(
        f"- **__7-Day DMs Sent (Total: {total_dms} DMs):__**\n{dm_column_str}"
    ))
    container.add_item(discord.ui.Separator())
    
    # Actions & Logging Section (Restricted to Active Current Day & Claimed Channel)
    container.add_item(discord.ui.TextDisplay("- **__Actions & Logging:__**"))
    
    is_unclaimed = (worker_user_id is None or not str(worker_user_id).isdigit())
    select_action_row = discord.ui.ActionRow()
    select_action_row.add_item(DashboardSelect(current_day=current_day, disabled=is_unclaimed))
    container.add_item(select_action_row)
    
    layout_view.add_item(container)

    return layout_view


async def update_pinned_dashboard(
    channel: discord.TextChannel,
    worker_name: str,
    worker_user_id: str | None,
    active_strikes: int,
    strike_dates: list,
    last_video_dt: datetime | None,
    day_values: list = None,
    total_dms: int = 0,
    force_new: bool = False
):
    """Finds or creates a pinned message in the query channel, editing in place unless force_new=True."""
    channel_id = str(channel.id)
    if day_values is None:
        rec = await sheets_manager.get_dm_record(channel_id)
        day_values = [int(rec.get(f"Day {d} DMs", 0) or 0) for d in range(1, 8)]
        total_dms = sum(day_values)

    layout_view = build_dashboard_v2_layout(channel, worker_name, worker_user_id, active_strikes, strike_dates, last_video_dt, day_values, total_dms)
    
    from strike_tracker import strike_tracker
    state = strike_tracker.channel_states.get(channel_id, {})
    cached_msg_id = state.get("dashboard_msg_id")

    dashboard_msg = None

    if not force_new:
        # 1. Try fetching directly via cached message ID first
        if cached_msg_id:
            try:
                dashboard_msg = await channel.fetch_message(int(cached_msg_id))
            except Exception:
                dashboard_msg = None

        # 2. Fallback: Search pinned messages using component tree matching
        if not dashboard_msg:
            try:
                pinned_messages = await channel.pins()
            except Exception as e:
                print(f"Error fetching pinned messages in {channel.name}: {e}")
                pinned_messages = []

            dashboard_custom_ids = {
                "v2_claim_channel_btn", "v2_unclaim_channel_btn", "v2_undo_strike_btn", "v2_view_stats_btn", "v2_dashboard_select_menu"
            }

            def is_bot_dashboard_message(msg):
                if not msg.author or not hasattr(msg.author, "id"):
                    return False
                guild = getattr(msg, "guild", None) or getattr(getattr(msg, "channel", None), "guild", None)
                if guild and getattr(guild, "me", None) and getattr(guild.me, "id", None):
                    if msg.author.id != guild.me.id:
                        return False

                items_to_check = []
                if hasattr(msg, "view") and msg.view and hasattr(msg.view, "children"):
                    items_to_check.extend(msg.view.children)
                if hasattr(msg, "components") and msg.components:
                    items_to_check.extend(msg.components)

                if not items_to_check:
                    return True

                def check_items(items):
                    for item in items:
                        if getattr(item, "custom_id", None) in dashboard_custom_ids:
                            return True
                        children = getattr(item, "children", None) or getattr(item, "components", None)
                        if children and check_items(children):
                            return True
                    return False

                return check_items(items_to_check)

            for msg in pinned_messages:
                if is_bot_dashboard_message(msg):
                    dashboard_msg = msg
                    break

        # 3. Edit existing dashboard message in place
        if dashboard_msg:
            try:
                if channel_id in strike_tracker.channel_states:
                    strike_tracker.channel_states[channel_id]["dashboard_msg_id"] = dashboard_msg.id
                await dashboard_msg.edit(embed=None, view=layout_view)
                return dashboard_msg
            except discord.HTTPException as e:
                print(f"Edit failed in {channel.name}, creating new dashboard: {e}")

    # 4. Create & pin new dashboard message at bottom of channel
    try:
        new_msg = await channel.send(view=layout_view)
        try:
            await new_msg.pin()
        except Exception as pe:
            print(f"Warning pinning message in {channel.name}: {pe}")
        if channel_id in strike_tracker.channel_states:
            strike_tracker.channel_states[channel_id]["dashboard_msg_id"] = new_msg.id
        return new_msg
    except Exception as e:
        print(f"Error creating/pinning dashboard message in {channel.name}: {e}")
        return None


async def resend_channel_dashboard(channel: discord.TextChannel):
    """Deletes any existing pinned dashboard in channel and posts + pins a fresh new panel at the bottom."""
    channel_id = str(channel.id)
    from strike_tracker import strike_tracker
    if channel_id not in strike_tracker.channel_states:
        await strike_tracker.initialize_channel(channel)

    state = strike_tracker.channel_states[channel_id]
    cached_msg_id = state.get("dashboard_msg_id")

    # Clean up all existing bot dashboard messages in channel pins
    try:
        pinned_messages = await channel.pins()
        dashboard_custom_ids = {
            "v2_claim_channel_btn", "v2_unclaim_channel_btn", "v2_undo_strike_btn", "v2_view_stats_btn", "v2_dashboard_select_menu"
        }
        for msg in pinned_messages:
            if msg.author and hasattr(msg.author, "id"):
                guild = getattr(msg, "guild", None) or getattr(channel, "guild", None)
                if guild and getattr(guild, "me", None) and msg.author.id == guild.me.id:
                    try:
                        await msg.unpin()
                    except Exception:
                        pass
                    try:
                        await msg.delete()
                    except Exception:
                        pass
    except Exception as e:
        print(f"Error cleaning up old dashboard pins in {channel.name}: {e}")

    state["dashboard_msg_id"] = None
    return await update_pinned_dashboard(
        channel,
        state["worker_name"],
        state.get("worker_user_id"),
        state["active_strikes"],
        state["strike_dates"],
        state["last_video_dt"],
        force_new=True
    )
