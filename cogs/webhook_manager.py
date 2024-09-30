import discord
from discord.ext import commands
import asyncio
from utils.webhook_utils import parse_webhook_url

class WebhookManager(commands.Cog):
    def __init__(self, bot, webhook_urls):
        super().__init__()  # Initialize the superclass
        self.bot = bot
        self.webhook_urls = webhook_urls
        self.webhook_objects = {}
        self.lock = asyncio.Lock()

    @commands.Cog.listener()
    async def on_ready(self):
        await self.initialize_webhooks()
        print('WebhookManager Cog is ready.')

    async def initialize_webhooks(self):
        """
        Initializes webhook objects from the webhook_urls configuration.
        """
        for name, url in self.webhook_urls.items():
            try:
                webhook_id, webhook_token = parse_webhook_url(url)
                webhook = await self.bot.fetch_webhook(webhook_id)
                self.webhook_objects[name] = webhook
                print(f"Initialized webhook '{name}': {webhook.url}")
            except Exception as e:
                print(f"Error initializing webhook '{name}': {e}")

    async def get_webhook(self, name):
        """
        Retrieves a webhook by name.

        Args:
            name (str): The name of the webhook.

        Returns:
            discord.Webhook: The webhook object.
        """
        async with self.lock:
            return self.webhook_objects.get(name)

    async def move_webhook(self, name, channel):
        """
        Moves the specified webhook to a different channel.

        Args:
            name (str): The name of the webhook.
            channel (discord.TextChannel): The target channel.

        Returns:
            discord.Webhook: The updated webhook object.
        """
        async with self.lock:
            webhook = self.webhook_objects.get(name)
            if not webhook:
                print(f"Webhook '{name}' not found.")
                return None
            try:
                await webhook.edit(channel=channel)
                print(f"Webhook '{name}' moved to channel '{channel.name}' (ID: {channel.id}).")
                return webhook
            except Exception as e:
                print(f"Error moving webhook '{name}': {e}")
                return None

    async def send_via_webhook(self, name, content, username, avatar_url):
        """
        Sends a message via the specified webhook.

        Args:
            name (str): The name of the webhook.
            content (str): The message content.
            username (str): The username to display.
            avatar_url (str): The avatar URL to display.

        Returns:
            discord.Message: The sent webhook message object.
        """
        webhook = await self.get_webhook(name)
        if not webhook:
            print(f"Webhook '{name}' not found.")
            return None
        try:
            sent_message = await webhook.send(
                content=content,
                username=username,
                avatar_url=avatar_url,
                wait=True  # Wait for the message to be sent to get the message object
            )
            print(f"Message sent via webhook '{name}'.")
            return sent_message
        except Exception as e:
            print(f"Error sending message via webhook '{name}': {e}")
            return None

    async def edit_via_webhook(self, name, message_id, new_content):
        """
        Edits a specific message sent via the specified webhook.

        Args:
            name (str): The name of the webhook.
            message_id (int): The ID of the message to edit.
            new_content (str): The new content for the message.

        Returns:
            discord.Message: The edited webhook message object.
        """
        webhook = await self.get_webhook(name)
        if not webhook:
            print(f"Webhook '{name}' not found.")
            return None
        try:
            edited_message = await webhook.edit_message(message_id, content=new_content)
            print(f"Message ID {message_id} edited via webhook '{name}'.")
            return edited_message
        except Exception as e:
            print(f"Error editing message via webhook '{name}': {e}")
            return None

# Asynchronous setup function for the Cog
async def setup(bot):
    await bot.add_cog(WebhookManager(bot, bot.config.WEBHOOK_URLS))
