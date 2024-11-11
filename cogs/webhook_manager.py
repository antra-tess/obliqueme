import json
import os

import discord
from discord.ext import commands
import asyncio
from utils.webhook_utils import parse_webhook_url


class WebhookManager(commands.Cog):
    def __init__(self, bot, webhook_urls, pool_size=6):
        super().__init__()  # Initialize the superclass
        self.bot = bot
        self.webhook_urls = webhook_urls
        self.webhook_objects = {}  # Format: {guild_id: {webhook_name: webhook}}
        self.lock = asyncio.Lock()
        self.initialized = False
        self.pool_size = pool_size
        self.current_index = {}  # Format: {guild_id: last_used_index}

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
            
            # Initialize webhook pool for this guild
            for i in range(self.pool_size):
                webhook_name = f'oblique_{i+1}'
                existing_webhook = discord.utils.get(bot_webhooks, name=webhook_name)
                if existing_webhook:
                    self.webhook_objects[guild.id][webhook_name] = existing_webhook
                    print(f"Found existing webhook '{webhook_name}' in guild {guild.id}: {existing_webhook.url}")
                else:
                    # Create new webhook if it doesn't exist
                    default_channel = guild.text_channels[0]  # Get first text channel as default
                    new_webhook = await self.create_webhook(webhook_name, default_channel.id)
                    if new_webhook:
                        print(f"Created new webhook '{webhook_name}' in guild {guild.id}")

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

    async def get_next_webhook(self, guild_id, channel_id):
        """Get the next available webhook in the pool and move it to the target channel."""
        print(f"\nGetting next webhook for guild {guild_id}, channel {channel_id}")
        
        # Get next index and webhook name under lock
        async with self.lock:
            if guild_id not in self.current_index:
                self.current_index[guild_id] = 0
            next_index = (self.current_index[guild_id] + 1) % self.pool_size
            self.current_index[guild_id] = next_index
            webhook_name = f'oblique_{next_index + 1}'
            print(f"Selected webhook name: {webhook_name}")
            webhook = self.webhook_objects.get(guild_id, {}).get(webhook_name)

        # Handle webhook creation or movement outside lock
        if not webhook:
            print(f"Creating new webhook {webhook_name}")
            webhook = await self.create_webhook(webhook_name, channel_id)
        else:
            print(f"Found existing webhook {webhook_name}")
            print(f"Moving webhook to channel {channel_id}")
            webhook = await self.move_webhook(guild_id, webhook_name, self.bot.get_channel(channel_id))
            if webhook:
                print("Successfully moved webhook")
            else:
                print("Failed to move webhook")
        
        print(f"Returning webhook {webhook_name}: {webhook}")
        return webhook_name, webhook

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

        guild_id = channel.guild.id
        async with self.lock:
            try:
                # Initialize guild dict if it doesn't exist
                if guild_id not in self.webhook_objects:
                    self.webhook_objects[guild_id] = {}

                # Check if the webhook already exists in our objects for this guild
                if name in self.webhook_objects[guild_id]:
                    print(f"Webhook '{name}' already exists in guild {guild_id}.")
                    return self.webhook_objects[guild_id][name]

                # Check if the webhook already exists in the channel
                existing_webhooks = await channel.webhooks()
                existing_webhook = discord.utils.get(existing_webhooks, name=name)

                if existing_webhook:
                    print(f"Webhook '{name}' already exists in channel '{channel.name}' (ID: {channel.id}).")
                    self.webhook_objects[guild_id][name] = existing_webhook
                    return existing_webhook

                # Create a new webhook if it doesn't exist
                webhook = await channel.create_webhook(name=name)
                self.webhook_objects[guild_id][name] = webhook
                print(f"Webhook '{name}' created in channel '{channel.name}' (ID: {channel.id}) for guild {guild_id}.")
                return webhook
            except Exception as e:
                print(f"Error managing webhook '{name}' in guild {guild_id}: {e}")
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
        # Get webhook under lock
        async with self.lock:
            webhook = self.webhook_objects.get(guild_id, {}).get(name)
            
        if not webhook:
            print(f"Webhook '{name}' not found in guild {guild_id}.")
            return None
        
        if channel.guild.id != guild_id:
            print(f"Cannot move webhook '{name}' to a different guild.")
            return None
            
        try:
            print(f"Attempting to move webhook '{name}' to channel '{channel.name}'...")
            print(f"Webhook before move - Channel: {webhook.channel_id}, Token: {webhook.token is not None}")
            
            try:
                # Move webhook without lock
                await webhook.edit(channel=channel)
                print(f"Successfully moved webhook '{name}' to channel '{channel.name}' (ID: {channel.id}) in guild {guild_id}")
                
                # Verify move was successful
                async with self.lock:
                    moved_webhook = self.webhook_objects[guild_id][name]
                    print(f"Webhook after move - Channel: {moved_webhook.channel_id}, Token: {moved_webhook.token is not None}")
                    if moved_webhook.channel_id != channel.id:
                        print("WARNING: Webhook channel ID mismatch after move!")
                
                return webhook
            except discord.HTTPException as e:
                print(f"HTTP error moving webhook: {e}")
                return None
            except Exception as e:
                print(f"Unexpected error moving webhook: {e}")
                return None
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
        print(f"\nAttempting to send message via webhook '{name}'")
        print(f"Guild ID: {guild_id}")
        print(f"Content: {content}")
        print(f"Username: {username}")
        print(f"Avatar URL: {avatar_url}")
        
        if self.initialized == False:
            print("Initializing webhooks...")
            await self.initialize_webhooks()

        webhook = await self.get_webhook(guild_id, name)
        if not webhook:
            print(f"Webhook '{name}' not found in guild {guild_id}")
            print(f"Available webhooks: {list(self.webhook_objects.get(guild_id, {}).keys())}")
            return None
        try:
            print(f"Attempting to send message via webhook '{name}'")
            print(f"Webhook details - Channel: {webhook.channel_id}, Token: {webhook.token is not None}")
            print(f"Message details - Length: {len(content)}, Has View: {view is not None}")
            try:
                sent_message = await webhook.send(
                    content=content,
                    username=username,
                    avatar_url=avatar_url,
                    wait=True,  # Wait for the message to be sent to get the message object
                    view=view
                )
                print(f"Successfully sent message via webhook '{name}' with ID {sent_message.id}")
                print(f"Message details - Channel: {sent_message.channel.id}, Author: {sent_message.author}")
                return sent_message
            except discord.HTTPException as e:
                print(f"HTTP error sending message: {e}")
                return None
            except Exception as e:
                print(f"Unexpected error sending message: {e}")
                return None
            return sent_message
        except Exception as e:
            print(f"Error sending message via webhook '{name}': {e}")
            return None

    async def edit_via_webhook(self, name, message_id, new_content, guild_id, view=None):
        """
        Edits a specific message sent via the specified webhook.

        Args:
            name (str): The name of the webhook.
            message_id (int): The ID of the message to edit.
            new_content (str): The new content for the message.
            guild_id (int): The ID of the guild.
            view (discord.ui.View, optional): The view containing components to add to the message.

        Returns:
            discord.Message: The edited webhook message object.
        """
        webhook = await self.get_webhook(guild_id, name)
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

    async def delete_webhook_message(self, name, message_id, guild_id):
        """
        Deletes a specific message sent via the specified webhook.

        Args:
            name (str): The name of the webhook.
            message_id (int): The ID of the message to delete.
            guild_id (int): The ID of the guild.

        Returns:
            bool: True if the message was successfully deleted, False otherwise.
        """
        webhook = await self.get_webhook(guild_id, name)
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
