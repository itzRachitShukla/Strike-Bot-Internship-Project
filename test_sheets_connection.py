import asyncio
import gspread
from sheets_manager import sheets_manager

async def test():
    print("Testing Google Sheets connection...")
    try:
        res = await sheets_manager.register_influencer(
            "https://instagram.com/test_influencer_profile",
            "TestUser#1234",
            "https://discord.com/channels/123/456"
        )
        print(" SUCCESS! Added row to Influencers worksheet:")
        print(res)
    except Exception as e:
        print(f" ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test())
