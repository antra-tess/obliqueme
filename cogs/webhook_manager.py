import json
import os

import discord
from discord.ext import commands
import asyncio
from utils.webhook_utils import parse_webhook_url


class WebhookManager(commands.Cog):
    def __init__(self, bot, webhook_urls):
        super().__init__()  # Initialize the superclass
        self.bot = bot
        self.webhook_urls = webhook_urls
        self.webhook_objects = {}  # Format: {guild_id: {webhook_name: webhook}}
        self.lock = asyncio.Lock()
        self.initialized = False

    @commands.Cog.listener()
    async def on_ready(self):
        print('WebhookManager Cog is ready.')

    async def initialize_webhooks(self):
        """
        Initializes webhook objects from the webhook_urls configuration.
        """
        self.initialized = True
        for guild in self.bot.guilds:
            self.webhook_objects[guild.id] = {}
            guild_webhooks = await guild.webhooks()
            bot_webhooks = [webhook for webhook in guild_webhooks if webhook.user == self.bot.user]
            
            for webhook in bot_webhooks:
                self.webhook_objects[guild.id][webhook.name] = webhook
                print(f"Found existing webhook '{webhook.name}' in guild {guild.id}: {webhook.url}")

        # Initialize any remaining webhooks from the webhook_urls configuration
        for name, url in self.webhook_urls.items():
            try:
                webhook_id, webhook_token = parse_webhook_url(url)
                webhook = await self.bot.fetch_webhook(webhook_id)
                guild_id = webhook.guild_id
                if guild_id not in self.webhook_objects:
                    self.webhook_objects[guild_id] = {}
                self.webhook_objects[guild_id][name] = webhook
                print(f"Initialized webhook '{name}' in guild {guild_id}: {webhook.url}")
            except Exception as e:
                print(f"Error initializing webhook '{name}': {e}")

    async def get_webhook(self, guild_id, name):
        """
        Retrieves a webhook by guild ID and name.

        Args:
            guild_id (int): The ID of the guild.
            name (str): The name of the webhook.

        Returns:
            discord.Webhook: The webhook object.
        """
        async with self.lock:
            return self.webhook_objects.get(guild_id, {}).get(name)

    async def create_webhook(self, name, channel_id):
        """
        Creates a new webhook in the specified channel or returns an existing one.

        Args:
            name (str): The name of the webhook.
            channel_id (int): The ID of the target channel.

        Returns:
            discord.Webhook: The created or existing webhook object.
        """
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            print(f"Error: Channel with ID {channel_id} not found.")
            return None
        async with self.lock:
            try:
                # Check if the webhook already exists in our objects
                if name in self.webhook_objects:
                    print(f"Webhook '{name}' already exists in our objects.")
                    return self.webhook_objects[name]

                # Check if the webhook already exists in the channel
                existing_webhooks = await channel.webhooks()
                existing_webhook = discord.utils.get(existing_webhooks, name=name)

                if existing_webhook:
                    print(f"Webhook '{name}' already exists in channel '{channel.name}' (ID: {channel.id}).")
                    self.webhook_objects[name] = existing_webhook
                    self.webhook_urls[name] = existing_webhook.url
                    return existing_webhook

                # Create a new webhook if it doesn't exist
                webhook = await channel.create_webhook(name=name)
                self.webhook_objects[name] = webhook
                self.webhook_urls[name] = webhook.url

                print(f"Webhook '{name}' created in channel '{channel.name}' (ID: {channel.id}).")
                return webhook
            except Exception as e:
                print(f"Error managing webhook '{name}': {e}")
                return None

    async def move_webhook(self, guild_id, name, channel):
        """
        Moves the specified webhook to a different channel within the same guild.

        Args:
            guild_id (int): The ID of the guild.
            name (str): The name of the webhook.
            channel (discord.TextChannel): The target channel.

        Returns:
            discord.Webhook: The updated webhook object.
        """
        async with self.lock:
            webhook = self.webhook_objects.get(guild_id, {}).get(name)
            if not webhook:
                print(f"Webhook '{name}' not found in guild {guild_id}.")
                return None
            
            if channel.guild.id != guild_id:
                print(f"Cannot move webhook '{name}' to a different guild.")
                return None
                
            try:
                await webhook.edit(channel=channel)
                print(f"Webhook '{name}' moved to channel '{channel.name}' (ID: {channel.id}) in guild {guild_id}.")
                return webhook
            except Exception as e:
                print(f"Error moving webhook '{name}' in guild {guild_id}: {e}")
                return None

    async def send_via_webhook(self, name, content, username, avatar_url, guild_id, view=None):
        """
        Sends a message via the specified webhook.

        Args:
            name (str): The name of the webhook.
            content (str): The message content.
            username (str): The username to display.
            avatar_url (str): The avatar URL to display.
            guild_id (int): The ID of the guild.
            view (discord.ui.View, optional): The view containing components to add to the message.

        Returns:
            discord.Message: The sent webhook message object.
        """
        if self.initialized == False:
            await self.initialize_webhooks()

        webhook = await self.get_webhook(guild_id, name)
        if not webhook:
            print(f"Webhook '{name}' not found.")
            return None
        try:
            sent_message = await webhook.send(
                content=content,
                username=username,
                avatar_url=avatar_url,
                wait=True,  # Wait for the message to be sent to get the message object
                view=view
            )
            print(f"Message sent via webhook '{name}'.")
            return sent_message
        except Exception as e:
            print(f"Error sending message via webhook '{name}': {e}")
            return None

    async def edit_via_webhook(self, name, message_id, new_content, view=None):
        """
        Edits a specific message sent via the specified webhook.

        Args:
            name (str): The name of the webhook.
            message_id (int): The ID of the message to edit.
            new_content (str): The new content for the message.
            view (discord.ui.View, optional): The view containing components to add to the message.

        Returns:
            discord.Message: The edited webhook message object.
        """
        webhook = await self.get_webhook(name)
        if not webhook:
            print(f"Webhook '{name}' not found.")
            return None
        try:
            edited_message = await webhook.edit_message(message_id, content=new_content, view=view)
            print(f"Message ID {message_id} edited via webhook '{name}'.")
            return edited_message
        except Exception as e:
            print(f"Error editing message via webhook '{name}': {e}")
            return None

    async def delete_webhook_message(self, name, message_id):
        """
        Deletes a specific message sent via the specified webhook.

        Args:
            name (str): The name of the webhook.
            message_id (int): The ID of the message to delete.

        Returns:
            bool: True if the message was successfully deleted, False otherwise.
        """
        webhook = await self.get_webhook(name)
        if not webhook:
            print(f"Webhook '{name}' not found.")
            return False
        try:
            await webhook.delete_message(message_id)
            print(f"Message ID {message_id} deleted via webhook '{name}'.")
            return True
        except Exception as e:
            print(f"Error deleting message via webhook '{name}': {e}")
            return False


# Asynchronous setup function for the Cog
async def setup(bot):
    await bot.add_cog(WebhookManager(bot, bot.config.WEBHOOK_URLS))
