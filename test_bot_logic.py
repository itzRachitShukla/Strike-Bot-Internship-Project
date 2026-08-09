import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta

from strike_tracker import strike_tracker, is_query_channel, is_video_message, IST
from pinned_dashboard import build_dashboard_v2_layout, DashboardLayoutView
from logger_service import logger_service
from main import parse_time_string_to_hours

class MockAuthor:
    def __init__(self, name="TestUser", user_id=999):
        self.name = name
        self.display_name = name
        self.id = user_id
        self.bot = False

    def __eq__(self, other):
        return isinstance(other, MockAuthor) and self.id == other.id

class MockGuild:
    def __init__(self):
        self.id = 12345
        self.me = MockAuthor("BotUser", 99999)

class MockChannel:
    def __init__(self, channel_id=101, name="test-query"):
        self.id = channel_id
        self.name = name
        self.mention = f"<#{channel_id}>"
        self.guild = MockGuild()
        self.sent_messages = []
        self.pins_list = []

    async def send(self, content=None, embed=None, view=None):
        msg = MockMessage(self, content, embed, view)
        self.sent_messages.append(msg)
        return msg

    async def pins(self):
        return self.pins_list


class MockAttachment:
    def __init__(self, filename="proof.mp4", content_type="video/mp4"):
        self.filename = filename
        self.content_type = content_type


class MockMessage:
    def __init__(self, channel, content="", embed=None, view=None, author=None, attachments=None, msg_id=999111):
        self.id = msg_id
        self.channel = channel
        self.content = content
        self.embeds = [embed] if embed else []
        self.components = []
        self.view = view
        self.author = author or MockAuthor()
        self.attachments = attachments or []
        self.pinned = False

    async def pin(self):
        self.pinned = True
        self.channel.pins_list.append(self)

    async def edit(self, content=None, embed=None, view=None):
        if content is not None:
            self.content = content
        if embed is not None:
            self.embeds = [embed]
        if view is not None:
            self.view = view


class TestBotLogic(unittest.IsolatedAsyncioTestCase):

    def test_query_channel_detection(self):
        self.assertTrue(is_query_channel(MockChannel(name="rachit-query")))
        self.assertTrue(is_query_channel(MockChannel(name="beast-queries")))
        self.assertFalse(is_query_channel(MockChannel(name="general")))
        self.assertFalse(is_query_channel(MockChannel(name="queries-chat")))

    def test_video_message_detection(self):
        msg_video = MockMessage(MockChannel(), attachments=[MockAttachment("screen_record.mp4")])
        self.assertTrue(is_video_message(msg_video))

        msg_link = MockMessage(MockChannel(), content="Here is my screen record https://youtube.com/watch?v=12345")
        self.assertTrue(is_video_message(msg_link))

        msg_text = MockMessage(MockChannel(), content="No video attached here")
        self.assertFalse(is_video_message(msg_text))

    def test_time_parser(self):
        self.assertEqual(parse_time_string_to_hours("12h"), 12.0)
        self.assertEqual(parse_time_string_to_hours("7d"), 168.0)
        self.assertAlmostEqual(parse_time_string_to_hours("1m"), 1/60.0, places=4)
        self.assertAlmostEqual(parse_time_string_to_hours("50s"), 50/3600.0, places=4)
        with self.assertRaises(ValueError):
            parse_time_string_to_hours("invalid_format")

    def test_v2_dashboard_layout_construction(self):
        ch = MockChannel()
        # Test Claimed State
        view_claimed = build_dashboard_v2_layout(ch, "TestWorker", "123456", 1, ["2026-08-01 10:00:00 IST"], datetime.now(IST), [10]*7, 70)
        self.assertIsInstance(view_claimed, DashboardLayoutView)
        self.assertEqual(len(view_claimed.children), 1)

        # Test Unclaimed State
        view_unclaimed = build_dashboard_v2_layout(ch, "TestWorker", None, 0, [], None, [0]*7, 0)
        self.assertIsInstance(view_unclaimed, DashboardLayoutView)
        self.assertEqual(len(view_unclaimed.children), 1)

    async def test_logger_service_config(self):
        logger_service.set_log_channel("claim", 999111)
        self.assertEqual(logger_service.log_channels["claim"], 999111)

    @patch("sheets_manager.sheets_manager.get_all_staff_records", new_callable=AsyncMock)
    @patch("sheets_manager.sheets_manager.update_staff_record", new_callable=AsyncMock)
    @patch("sheets_manager.sheets_manager.get_dm_record", new_callable=AsyncMock)
    async def test_strike_issuance_and_revocation_cycle(self, mock_dm_rec, mock_update_staff, mock_get_staff):
        mock_get_staff.return_value = []
        mock_dm_rec.return_value = {}
        
        ch = MockChannel(channel_id=888, name="test-query")
        channel_id = str(ch.id)
        
        # Initialize
        await strike_tracker.initialize_channel(ch)
        self.assertIn(channel_id, strike_tracker.channel_states)
        
        state = strike_tracker.channel_states[channel_id]
        state["worker_user_id"] = "123456"  # Claim channel to enable auditing
        
        # Simulate missing video past deadline
        state["last_video_dt"] = datetime.now(IST) - timedelta(days=2)
        
        # Fast forward time to trigger deadline
        await strike_tracker.simulate_time_travel(ch, 48.0)

        # Run audit (should trigger strike #1)
        await strike_tracker.audit_channel(ch, bot=None)
        self.assertEqual(state["active_strikes"], 1)
        
        # Fast forward 8 days with daily video submitted for current window
        now_eff = strike_tracker._get_effective_now(channel_id)
        state["strike_dates"][-1] = (now_eff - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S IST")
        state["last_video_dt"] = now_eff
        
        # Run audit (should revoke strike #1 after 7 clean days)
        await strike_tracker.audit_channel(ch, bot=None)
        self.assertEqual(state["active_strikes"], 0)


if __name__ == "__main__":
    unittest.main()
