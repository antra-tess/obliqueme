import json
import os

import discord
from discord.ext import commands
import asyncio
from utils.webhook_utils import parse_webhook_url
from utils.channel_utils import is_thread_channel, get_parent_channel, format_channel_info


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
        """Get the next available webhook in the pool, preferring webhooks already in the target channel.
        
        For threads: webhooks stay in the parent channel and we use thread= parameter when sending.
        """
        print(f"\nGetting next webhook for guild {guild_id}, channel {channel_id}")
        
        async with self.lock:
            # Initialize if needed
            if guild_id not in self.webhook_objects:
                self.webhook_objects[guild_id] = {}
            if guild_id not in self.current_index:
                self.current_index[guild_id] = 0
                
            # Get the channel object to check if it's a thread
            channel = self.bot.get_channel(channel_id)
            
            # For webhook placement, always use the parent channel for threads
            # Webhooks can't truly "be in" a thread - they stay in parent and use thread= param
            if is_thread_channel(channel):
                webhook_channel_id = channel.parent_id
                print(f"Channel {channel_id} is a thread in parent channel {channel.parent_id}")
                print(f"Webhook will be placed in parent channel {webhook_channel_id}")
            else:
                webhook_channel_id = channel_id
                print(f"Channel {channel_id} is a regular channel")
            
            # First, look for webhooks already in the webhook channel (parent for threads)
            print(f"Looking for webhooks already in channel {webhook_channel_id}")
            for name, webhook in self.webhook_objects[guild_id].items():
                if webhook.channel_id == webhook_channel_id:
                    print(f"Found webhook {name} already in target channel")
                    return name, webhook
            
            # If no webhook is in the target channel, use round-robin selection
            next_index = (self.current_index[guild_id] + 1) % self.pool_size
            self.current_index[guild_id] = next_index
            webhook_name = f'oblique_{next_index + 1}'
            print(f"No webhook found in target channel, selected {webhook_name}")
            webhook = self.webhook_objects[guild_id].get(webhook_name)

        # Handle webhook creation or movement outside lock
        if not webhook:
            print(f"Creating new webhook {webhook_name}")
            webhook = await self.create_webhook(webhook_name, webhook_channel_id)
        else:
            print(f"Moving webhook {webhook_name} from channel {webhook.channel_id} to {webhook_channel_id}")
            # Move webhook to the parent channel (not the thread itself)
            target_channel = self.bot.get_channel(webhook_channel_id)
            webhook = await self.move_webhook(guild_id, webhook_name, target_channel)
            if webhook:
                print(f"Successfully moved webhook {webhook_name}")
            else:
                print(f"Failed to move webhook {webhook_name}")
        
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
        For thread channels, creates the webhook in the parent channel.
        
        Note: Webhooks always stay in parent channels. For threads, use thread= param when sending.

        Args:
            name (str): The name of the webhook.
            channel_id (int): The ID of the target channel (should be parent channel, not thread).

        Returns:
            discord.Webhook: The created or existing webhook object.
        """
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            print(f"Error: Channel with ID {channel_id} not found.")
            return None

        # If someone passed a thread ID, get the parent channel instead
        if is_thread_channel(channel):
            print(f"Warning: create_webhook called with thread ID {channel_id}, using parent channel instead")
            channel = get_parent_channel(self.bot, channel)
            if not channel:
                print(f"Error: Parent channel not found for thread {channel_id}")
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
                print(f"Webhook '{name}' created in channel '{channel.name}' (ID: {channel.id}) in guild {guild_id}.")
                return webhook
            except Exception as e:
                print(f"Error managing webhook '{name}' in guild {guild_id}: {e}")
                return None

    async def move_webhook(self, guild_id, name, channel):
        """
        Moves the specified webhook to a different channel within the same guild.
        
        Note: Webhooks cannot be moved to threads. For threads, keep webhook in parent
        and use thread= parameter when sending.

        Args:
            guild_id (int): The ID of the guild.
            name (str): The name of the webhook.
            channel (discord.TextChannel): The target channel (must not be a thread).

        Returns:
            discord.Webhook: The updated webhook object.
        """
        # Get webhook under lock
        async with self.lock:
            webhook = self.webhook_objects.get(guild_id, {}).get(name)
            
        if not webhook:
            print(f"Webhook '{name}' not found in guild {guild_id}.")
            return None
        
        # Don't allow moving to threads - use thread= param when sending instead
        if is_thread_channel(channel):
            print(f"Cannot move webhook to thread. Use thread= parameter when sending.")
            # Return the existing webhook - it can still be used with thread= param
            return webhook
        
        if channel.guild.id != guild_id:
            print(f"Cannot move webhook '{name}' to a different guild.")
            return None
        
        # Check if it's already in the target channel
        if webhook.channel_id == channel.id:
            print(f"Webhook '{name}' is already in the target channel.")
            return webhook
            
        try:
            print(f"Attempting to move webhook '{name}' to channel '{channel.name}'...")
            print(f"Webhook before move - Channel: {webhook.channel_id}")
            
            try:
                # Move webhook without lock
                await webhook.edit(channel=channel)
                print(f"Successfully moved webhook '{name}' to {format_channel_info(channel)} in guild {guild_id}")
                
                # Verify move was successful
                async with self.lock:
                    moved_webhook = self.webhook_objects[guild_id][name]
                    print(f"Webhook after move - Channel: {moved_webhook.channel_id}")
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

    async def send_via_webhook(self, name, content, username, avatar_url, guild_id, view=None, target_channel_id=None):
        """
        Sends a message via the specified webhook.
        Supports sending to both regular channels and threads.
        
        For threads: webhook stays in parent channel, we use thread= parameter.

        Args:
            name (str): The name of the webhook.
            content (str): The message content.
            username (str): The username to display.
            avatar_url (str): The avatar URL to display.
            guild_id (int): The ID of the guild.
            view (discord.ui.View, optional): The view containing components to add to the message.
            target_channel_id (int, optional): The target channel/thread ID for sending.

        Returns:
            discord.Message: The sent webhook message object.
        """
        print(f"\nAttempting to send message via webhook '{name}'")
        print(f"Guild ID: {guild_id}")
        print(f"Target channel ID: {target_channel_id}")
        print(f"Content length: {len(content)}")
        print(f"Username: {username}")
        
        if self.initialized == False:
            print("Initializing webhooks...")
            await self.initialize_webhooks()

        webhook = await self.get_webhook(guild_id, name)
        if not webhook:
            print(f"Webhook '{name}' not found in guild {guild_id}")
            print(f"Available webhooks: {list(self.webhook_objects.get(guild_id, {}).keys())}")
            return None
        try:
            # Build kwargs for webhook.send()
            kwargs = {
                "content": content,
                "username": username,
                "avatar_url": avatar_url,
                "wait": True,
                "view": view
            }
            
            # Check if target is a thread - if so, use thread= parameter
            if target_channel_id:
                target_channel = self.bot.get_channel(target_channel_id)
                if target_channel and is_thread_channel(target_channel):
                    # Verify webhook is in the parent channel
                    if webhook.channel_id == target_channel.parent_id:
                        kwargs["thread"] = target_channel
                        print(f"Sending to thread '{target_channel.name}' (ID: {target_channel_id}) via parent channel webhook")
                    else:
                        print(f"Warning: webhook channel {webhook.channel_id} doesn't match thread's parent {target_channel.parent_id}")
                        # Try to send anyway with thread param
                        kwargs["thread"] = target_channel
                elif target_channel_id != webhook.channel_id:
                    print(f"Warning: target_channel_id {target_channel_id} doesn't match webhook channel {webhook.channel_id}")
            
            sent_message = await webhook.send(**kwargs)
            channel_info = f"channel {sent_message.channel.id}"
            if hasattr(sent_message.channel, 'parent_id') and sent_message.channel.parent_id:
                channel_info = f"thread '{sent_message.channel.name}' in channel {sent_message.channel.parent_id}"
            
            print(f"Successfully sent message via webhook '{name}' with ID {sent_message.id} to {channel_info}")
            return sent_message
        except Exception as e:
            print(f"Error sending message via webhook '{name}': {e}")
            import traceback
            traceback.print_exc()
            return None

    async def edit_via_webhook(self, name, message_id, new_content, guild_id, view=None, target_channel_id=None):
        """
        Edits a specific message sent via the specified webhook.
        Supports editing messages in both regular channels and threads.

        Args:
            name (str): The name of the webhook.
            message_id (int): The ID of the message to edit.
            new_content (str): The new content for the message.
            guild_id (int): The ID of the guild.
            view (discord.ui.View, optional): The view containing components to add to the message.
            target_channel_id (int, optional): The target channel/thread ID.

        Returns:
            discord.Message: The edited webhook message object.
        """
        webhook = await self.get_webhook(guild_id, name)
        if not webhook:
            print(f"Webhook '{name}' not found.")
            return None
        try:
            # For editing, we need to specify the thread if the message is in a thread
            kwargs = {"content": new_content, "view": view}
            
            # Check if target is a thread
            if target_channel_id:
                target_channel = self.bot.get_channel(target_channel_id)
                if target_channel and is_thread_channel(target_channel):
                    kwargs["thread"] = target_channel
                    print(f"Editing message in thread '{target_channel.name}' (ID: {target_channel_id})")
            
            edited_message = await webhook.edit_message(message_id, **kwargs)
            channel_info = "channel"
            if target_channel_id:
                target_channel = self.bot.get_channel(target_channel_id)
                if target_channel and is_thread_channel(target_channel):
                    channel_info = f"thread '{target_channel.name}'"
            print(f"Message ID {message_id} edited via webhook '{name}' in {channel_info}.")
            return edited_message
        except Exception as e:
            print(f"Error editing message via webhook '{name}': {e}")
            import traceback
            traceback.print_exc()
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
