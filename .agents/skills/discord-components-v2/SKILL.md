---
name: discord-components-v2
description: Patterns and rules for building Discord Components V2 UI layouts (LayoutView, Container, Section, TextDisplay, Separator) in discord.py with text-only button constraints.
---

# Discord Components V2 & UI Layout Patterns

When building or updating Discord UIs in `discord.py` (v2.6+):

## 1. Components V2 Architecture
- **Base View**: Use `discord.ui.LayoutView` instead of traditional `discord.ui.View`.
- **Layout Elements**:
  - `discord.ui.Container`: Card wrapper for grouping components.
  - `discord.ui.TextDisplay`: Main text display elements supporting markdown and dynamic timestamps (`<t:unix:R>`).
  - `discord.ui.Separator`: Visual divider line between sections.
  - `discord.ui.Section`: Grouped text and control accessories.

```python
import discord

class V2DashboardLayout(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay("## Header Title"))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay("Body Section"))
        container.add_item(discord.ui.Separator())
        
        # Add Controls
        container.add_item(discord.ui.Button(label="Claim Channel", style=discord.ButtonStyle.primary))
        self.add_item(container)
```

## 2. Formatting & Control Rules
- **Text-Only Buttons**: Unless icons are explicitly requested by the user, keep all button labels (`label="Action Text"`) and dropdown select options strictly text-only without emojis.
- **User Tagging**: Always tag users directly using `<@user_id>` for notifications instead of channel names (`<#channel_id>` or `<@channel_name>`).
- **Admin Permission Checks**: Restrict destructive buttons (like `Undo Last Strike` or `Unclaim`) using `interaction.user.guild_permissions.administrator`.
