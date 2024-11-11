import os
import discord
from discord.ext import commands
import asyncio
import logging

from config import Config
from cogs.webhook_manager import WebhookManager
from cogs.message_handler import MessageHandler

# Configure logging
logging.basicConfig(level=logging.INFO)

def main():
    # Initialize bot with intents
    intents = discord.Intents.default()
    intents.message_content = True  # Ensure the bot can read message content
    intents.members = True  # Enable members intent

    bot = commands.Bot(
        command_prefix='!', 
        intents=intents, 
        command_tree_cls=discord.app_commands.CommandTree,
        application_id=int(os.getenv('APPLICATION_ID'))  # Add application ID from environment
    )

    # Attach configuration to bot for access in Cogs
    bot.config = Config

    # Register Cogs using load_extension
    # This approach requires that each Cog has an async setup function
    async def load_cogs():
        await bot.load_extension('cogs.webhook_manager')
        await bot.load_extension('cogs.message_handler')

    async def run_bot():
        async with bot:
            await load_cogs()
            await bot.start(Config.BOT_TOKEN)

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("Bot is shutting down.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    main()
