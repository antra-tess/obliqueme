import random
from typing import Optional

import discord
from discord.ext import commands
from discord import ButtonStyle
from discord.ui import Button, View
import asyncio
from agents.llm_agent import LLMAgent
from collections import deque
from generation.context import GenerationManager, GenerationContext


class MessageHandler(commands.Cog):
    def __init__(self, bot, webhook_manager, config):
        super().__init__()  # Initialize the superclass
        self.bot = bot
        self.webhook_manager = webhook_manager
        self.config = config
        self.agents = {}
        self.agents_lock = asyncio.Lock()
        self.generation_manager = GenerationManager()

    async def model_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[discord.app_commands.Choice[str]]:
        """Autocomplete function for model selection"""
        models = self.config.get_models()
        choices = []
        
        for key, config in models.items():
            model_name = config['name']
            # Filter based on current input
            if current.lower() in model_name.lower():
                choices.append(discord.app_commands.Choice(name=model_name, value=key))
        
        # Return up to 25 choices (Discord limit)
        return choices[:25]

    @discord.app_commands.autocomplete(model=model_autocomplete)
    @discord.app_commands.command(
        name="oblique",
        description="Generate a model simulation of the conversation"
    )
    @discord.app_commands.describe(
        model="Choose the AI model to use for generation",
        seed="Starting text for the simulation (optional)",
        mode="Simulation mode: 'self' (default) or 'full'",
        suppress_name="Don't add your name at the end of the prompt",
        custom_name="Use a custom name instead of your display name",
        temperature="Set the temperature for generation (0.1-1.0)"
    )
    async def oblique_command(
        self, 
        interaction: discord.Interaction,
        model: str = None,
        seed: str = None,
        mode: str = "self",
        suppress_name: bool = False,
        custom_name: str = None,
        temperature: float = None
    ):
        """Slash command version of obliqueme"""
        print(f"\nSlash command received from {interaction.user.display_name} in {interaction.guild.name}")
        print(f"Selected model: {model}")
        # Defer the response as ephemeral and delete it later
        await interaction.response.defer(ephemeral=True)
        print("Response deferred")
        
        try:
            # Get model configuration
            model_key = model or self.config.get_default_model_key()
            model_config = self.config.get_model_config(model_key)
            if not model_config:
                await interaction.followup.send(f"Invalid model selected: {model}", ephemeral=True)
                return
            
            print(f"Using model config: {model_config['name']} ({model_key})")

            # Get next webhook from the pool
            webhook_name, webhook = await self.webhook_manager.get_next_webhook(interaction.guild_id, interaction.channel_id)

            if not webhook:
                await interaction.followup.send("Failed to set up webhook.", ephemeral=True)
                return

            # Create a View with just the Cancel button
            view = self.create_cancel_view()

            # Initialize variables
            target_member_id = None
            avatar_url = interaction.user.display_avatar.url if interaction.user.display_avatar else None
            
            # Handle custom name and avatar
            if custom_name:
                target_member = self.find_member_by_name(custom_name, interaction.guild)
                print(f"Looking up member '{custom_name}' in guild {interaction.guild.name}")
                if target_member:
                    display_name = target_member.display_name
                    avatar_url = target_member.display_avatar.url if target_member.display_avatar else None
                    # Store the target member's ID for avatar updates
                    target_member_id = target_member.id
                    print(f"Found member: {display_name} (ID: {target_member_id}), avatar URL: {avatar_url}")
                else:
                    display_name = custom_name
                    print(f"Member '{custom_name}' not found in guild {interaction.guild.name}")
                webhook_username = f"{display_name}[oblique:{interaction.user.display_name}:{model_config['name']}]"
            else:
                webhook_username = f"{interaction.user.display_name}[oblique:{model_config['name']}]"

            # Send initial message
            generating_content = "Oblique: Generating..."
            print(f"\nAttempting to send initial message:")
            print(f"Webhook name: {webhook_name}")
            print(f"Username: {webhook_username}")
            print(f"Avatar URL: {avatar_url}")
            sent_message = await self.webhook_manager.send_via_webhook(
                name=webhook_name,
                content=generating_content,
                username=webhook_username,
                avatar_url=avatar_url,
                guild_id=interaction.guild_id,
                view=view,
                target_channel_id=interaction.channel_id
            )
            print(f"Send result: {sent_message}")

            if not sent_message:
                await interaction.followup.send("Failed to send initial message.", ephemeral=True)
                return

            # Create and register generation context
            context = await self.generation_manager.create_context(
                owner_id=interaction.user.id,
                guild_id=interaction.guild_id,
                mode=mode,
                seed=seed,
                suppress_name=suppress_name,
                custom_name=custom_name,
                temperature=temperature,
                avatar_url=avatar_url,
                target_member_id=target_member_id,
                webhook_name=webhook_name,  # Store the webhook name
                model_key=model_key  # Store the selected model
            )
            await self.generation_manager.register_message(context, sent_message.id)

            # Prepare data for LLM agent
            data = {
                'message': interaction,
                'generating_message_id': sent_message.id,
                'channel_id': interaction.channel_id,
                'username': context.parameters.get('custom_name') or interaction.user.display_name,
                'webhook': webhook_name,
                'bot': self.bot,
                'user_id': interaction.user.id,
                'context': context,
                'mode': context.parameters.get('mode', 'self'),
                'seed': context.parameters.get('seed'),
                'suppress_name': context.parameters.get('suppress_name', False),
                'custom_name': context.parameters.get('custom_name'),
                'temperature': context.parameters.get('temperature'),
                'model_config': model_config  # Add model config to data
            }

            print(f"[DEBUG] Prepared data for LLM agent, keys: {list(data.keys())}")
            print(f"[DEBUG] About to get agent for user {interaction.user.id}")

            # Get or create agent with the specific model config
            agent = await self.get_or_create_agent(interaction.user.id, model_config)
            print(f"[DEBUG] Got agent: {agent.name}")
            print(f"[DEBUG] About to enqueue message")
            await agent.enqueue_message(data)
            print(f"[DEBUG] Message enqueued successfully")
            
            # Delete the deferred response
            await interaction.delete_original_response()
            print(f"[DEBUG] Deleted deferred response")

        except Exception as e:
            print(f"Error handling slash command: {e}")
            import traceback
            traceback.print_exc()
            await interaction.followup.send("An error occurred while processing your request.", ephemeral=True)
        

    @commands.Cog.listener()
    async def on_ready(self):
        print('MessageHandler Cog is ready.')
        await self.webhook_manager.initialize_webhooks()
        try:
            # Sync the command tree with Discord
            commands = await self.bot.tree.sync()
            print(f"Successfully synced {len(commands)} commands")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

    def find_member_by_name(self, name: str, guild: discord.Guild) -> Optional[discord.Member]:
        """Find a guild member by username or display name."""
        if not name:
            return None
            
        name_lower = name.lower()
        print(f"\nLooking for member with name '{name}' (lowercase: '{name_lower}')")
        print(f"Guild {guild.name} has {len(guild.members)} members loaded")
        print("Members intents enabled:", self.bot.intents.members)
        
        for member in guild.members:
            print(f"Checking member - Name: '{member.name}' (lower: '{member.name.lower()}'), Display name: '{member.display_name}' (lower: '{member.display_name.lower()}')")
            if member.name.lower() == name_lower or member.display_name.lower() == name_lower:
                print(f"Found matching member: {member}")
                return member
        
        print("No matching member found")
        return None

    async def get_webhook_from_context(self, message_id: int, user_name: str) -> tuple[Optional[str], Optional[GenerationContext]]:
        """Get webhook name and context for a message, with error handling."""
        context = await self.generation_manager.get_context(message_id)
        if not context:
            print(f"No context found for message {message_id}")
            return None, None
            
        webhook_name = context.parameters.get('webhook_name')
        if not webhook_name:
            print(f"No webhook name found in context for message {message_id}")
            return None, None
            
        return webhook_name, context

    def create_cancel_view(self) -> View:
        """Create a view with just the cancel button."""
        view = View()
        view.add_item(Button(style=ButtonStyle.danger, label="Cancel", custom_id="cancel"))
        return view

    def create_generation_view(self, context: GenerationContext) -> View:
        """Create a view with all generation buttons."""
        view = View()
        view.add_item(Button(style=ButtonStyle.secondary, label="Prev", custom_id="prev",
                           disabled=(context.current_index == 0)))
        view.add_item(Button(style=ButtonStyle.primary, label="+3", custom_id="reroll"))
        view.add_item(Button(style=ButtonStyle.secondary, label="Next", custom_id="next",
                           disabled=(context.current_index == len(context.history) - 1)))
        view.add_item(Button(style=ButtonStyle.secondary, label="Trim", custom_id="trim"))
        view.add_item(Button(style=ButtonStyle.success, label="Commit", custom_id="commit"))
        view.add_item(Button(style=ButtonStyle.danger, label="Delete", custom_id="delete"))
        return view

    def trim_message(self, content):
        lines = content.split('\n')
        if not lines:
            return content

        last_line = lines[-1]
        if '.' not in last_line:
            return '\n'.join(lines[:-1])

        if last_line.strip().endswith('.'):
            # Find the second to last period
            second_last_period = content.rfind('.', 0, content.rfind('.'))
            if second_last_period != -1:
                return content[:second_last_period + 1]
        else:
            # Trim everything after the last period
            last_period = last_line.rfind('.')
            if last_period != -1:
                lines[-1] = last_line[:last_period + 1]

        return '\n'.join(lines)

    @commands.Cog.listener()
    async def on_message(self, message):
        # Prevent the bot from responding to its own messages or webhooks
        if message.author == self.bot.user or isinstance(message.author, discord.Webhook):
            return

        if self.config.KEYWORD.lower() in message.content.lower() and f"`{self.config.KEYWORD.lower()}`" not in message.content.lower():
            print(
                f'Keyword "{self.config.KEYWORD}" detected in channel {message.channel.name} (ID: {message.channel.id}).')

            # Split content into words
            words = message.content.split()
            options = []
            seed_words = []
            
            # Process each word after the keyword
            for word in words[1:]:
                if word.startswith('-'):
                    options.append(word)
                else:
                    # If we're after a parameter that takes a value, it's not part of the seed
                    if options and options[-1] in ['-n', '-p']:
                        options.append(word)
                    else:
                        seed_words.append(word)

            # Extract options
            suppress_name = "-s" in options
            
            # Extract mode
            mode = "self"  # default mode
            if "-m" in options:
                mode_index = options.index("-m") + 1
                if mode_index < len(options):
                    mode_value = options[mode_index]
                    if mode_value in ["self", "full"]:
                        mode = mode_value
                        # Remove the mode from seed if it was captured there
                        if mode_value in seed_words:
                            seed_words.remove(mode_value)
            
            # Extract custom name
            custom_name = None
            if "-n" in options:
                name_index = options.index("-n") + 1
                if name_index < len(options):
                    custom_name = options[name_index]
                    # Remove the name from seed if it was captured there
                    if custom_name in seed_words:
                        seed_words.remove(custom_name)

            # Extract temperature
            temperature = None
            if "-p" in options:
                p_index = options.index("-p") + 1
                if p_index < len(options):
                    try:
                        temp_value = options[p_index]
                        temperature = float(temp_value)
                        # Remove the temperature from seed if it was captured there
                        if temp_value in seed_words:
                            seed_words.remove(temp_value)
                    except ValueError:
                        print(f"Invalid temperature value: {options[p_index]}")

            # Join remaining words as seed
            seed = " ".join(seed_words) if seed_words else None

            await self.handle_keyword(message, suppress_name, custom_name, temperature, seed, mode)

        await self.bot.process_commands(message)

    async def handle_keyword(self, message, suppress_name=False, custom_name=None, temperature=None, seed=None, mode="self"):
        """
        Handles the keyword detection by deleting the user's message,
        replacing it with 'Generating...', and interacting with the LLM agent.

        Args:
            message (discord.Message): The message that triggered the keyword.
            suppress_name (bool): Whether to suppress adding the name at the end of the prompt.
            custom_name (str, optional): A custom name to use instead of the author's display name.
        """
        try:
            # Get default model configuration
            default_model_key = self.config.get_default_model_key()
            model_config = self.config.get_model_config(default_model_key)
            print(f"Text trigger using default model: {default_model_key}")
            print(f"Model config: {model_config}")
            
            if not model_config:
                print(f"Error: Could not find model config for key '{default_model_key}'")
                return
            
            # Check if bot has permission to delete messages
            if message.guild.me.guild_permissions.manage_messages:
                # Delete the user's original message
                await message.delete()
                print(f'Deleted message from {message.author.display_name} in channel {message.channel.name}.')
            else:
                # Suggest using slash command instead
                await message.reply("I don't have permission to delete messages. Try using `/oblique` instead!", delete_after=5)
                print(f'No permission to delete messages in {message.guild.name}, suggested slash command.')

            # Get the channel object
            # channel = self.bot.get_channel(message.channel_id)
            # if not channel:
            #     print(f"Channel with ID {message.channel_id} not found.")
            #     return

            # Get next webhook from the pool
            webhook_name, webhook = await self.webhook_manager.get_next_webhook(message.guild.id, message.channel.id)

            if not webhook:
                print("Failed to move webhook for the replacement.")
                return

            # Create a View with just the Cancel button
            view = self.create_cancel_view()

            # Handle custom name and avatar
            if custom_name:
                target_member = self.find_member_by_name(custom_name, message.guild)
                print(f"Looking up member '{custom_name}' in guild {message.guild.name}")
                if target_member:
                    display_name = target_member.display_name
                    avatar_url = target_member.display_avatar.url if target_member.display_avatar else None
                    print(f"Found member: {display_name}, avatar URL: {avatar_url}")
                else:
                    display_name = custom_name
                    avatar_url = message.author.display_avatar.url if message.author.display_avatar else None
                    print(f"Member '{custom_name}' not found in guild {message.guild.name}")
                webhook_username = f"{display_name}[oblique:{message.author.display_name}:{model_config['name']}]"
            else:
                webhook_username = f"{message.author.display_name}[oblique:{model_config['name']}]"
                avatar_url = message.author.display_avatar.url if message.author.display_avatar else None

            # Send 'Generating...' via webhook, capture the message object
            generating_content = "Oblique: Generating..."
            sent_message = await self.webhook_manager.send_via_webhook(
                name=webhook_name,
                content=generating_content,
                username=webhook_username,
                avatar_url=avatar_url,
                guild_id=message.guild.id,
                view=view,
                target_channel_id=message.channel.id
            )

            if not sent_message:
                print("Failed to send 'Generating...' message via webhook.")
                return
            print(f"Sent 'Generating...' message via webhook '{webhook}' with message ID {sent_message.id}.")

            # Create and register generation context
            context = await self.generation_manager.create_context(
                owner_id=message.author.id,
                guild_id=message.guild.id,
                mode=mode,
                seed=seed,
                suppress_name=suppress_name,
                custom_name=custom_name,
                temperature=temperature
            )
            await self.generation_manager.register_message(context, sent_message.id)

            # Prepare data for the LLM agent
            data = {
                'message': message,
                'generating_message_id': sent_message.id,
                'channel_id': message.channel.id,
                'username': context.parameters.get('custom_name') or message.author.display_name,
                'webhook': webhook_name,
                'bot': self.bot,
                'user_id': message.author.id,
                'context': context,
                'mode': context.parameters.get('mode', 'self'),
                'seed': context.parameters.get('seed'),
                'suppress_name': context.parameters.get('suppress_name', False),
                'custom_name': context.parameters.get('custom_name'),
                'temperature': context.parameters.get('temperature'),
                'model_config': model_config  # Add model config to data
            }

            # Interact with the LLM agent (stateful) using default model
            agent = await self.get_or_create_agent(message.author.id, model_config)
            print(f"[DEBUG] Got agent: {agent.name}")
            print(f"[DEBUG] About to enqueue message with data keys: {list(data.keys())}")
            await agent.enqueue_message(data)
            print(f"[DEBUG] Message enqueued successfully")

        except discord.errors.NotFound:
            print("The message or webhook was not found.")
        except discord.errors.Forbidden:
            print("Missing permissions to delete messages or manage webhooks.")
        except Exception as e:
            print(f'Error handling keyword: {e}')
            # print stack trace
            import traceback
            traceback.print_exc()

    async def get_or_create_agent(self, user_id, model_config=None):
        """
        Retrieves an existing LLM agent for the user or creates a new one with the specified model config.

        Args:
            user_id (int): The Discord user ID.
            model_config (dict): The model configuration to use.

        Returns:
            LLMAgent: The stateful LLM agent instance.
        """
        # Create a unique agent key based on user_id and model
        if model_config:
            model_key = f"{user_id}_{model_config.get('model_id', 'default')}"
        else:
            model_key = f"{user_id}_default"
            model_config = self.config.get_model_config(self.config.get_default_model_key())
            
        async with self.agents_lock:
            if model_key not in self.agents:
                # Define the callback function to handle the LLM's response
                async def llm_callback(data, replacement_text, page, total_pages):
                    """
                    Callback function to handle the LLM's response.

                    Args:
                        data (dict): Data containing 'generating_message_id', 'channel_id', 'username'.
                        replacement_text (str): The text generated by the LLM.
                        page (int): The current page number.
                        total_pages (int): The total number of pages.
                    """
                    try:
                        # Get webhook name and context
                        webhook_name, context = await self.get_webhook_from_context(data['generating_message_id'], data['username'])
                        if not webhook_name or not context:
                            return

                        # Handle both Message and Interaction objects
                        if isinstance(data['message'], discord.Interaction):
                            user_id = data['message'].user.id
                        else:
                            user_id = data['message'].author.id

                        # Get the channel object
                        channel = self.bot.get_channel(data['channel_id'])
                        if not channel:
                            print(f"Channel with ID {data['channel_id']} not found.")
                            return

                        # Add the new generation to context
                        await context.add_generation(replacement_text)
                        
                        # Use appropriate view based on generation state
                        view = self.create_generation_view(context) if len(context.history) > 0 else self.create_cancel_view()

                        # Add page information to the content
                        content_with_page = f"{replacement_text}"
                        
                        # Normalize line endings (convert \r\n to \n)
                        content_with_page = content_with_page.replace('\r\n', '\n').replace('\r', '\n')
                        
                        # Debug newlines
                        newline_count = content_with_page.count('\n')
                        print(f"[DEBUG] Content has {newline_count} newlines before truncation")
                        print(f"[DEBUG] First 200 chars: {repr(content_with_page[:200])}")
                        
                        # Truncate if over Discord's 2000 character limit
                        if len(content_with_page) > 2000:
                            # Try to find a good breaking point (sentence ending)
                            target_length = 1997 - 3  # Reserve 3 chars for "..."
                            truncated = content_with_page[:target_length]
                            
                            # Try to end at a sentence boundary
                            sentence_endings = ['. ', '! ', '? ', '\n\n', '\n']
                            best_break = -1
                            
                            for ending in sentence_endings:
                                last_occurrence = truncated.rfind(ending)
                                if last_occurrence > target_length * 0.8:  # Only if we don't lose more than 20%
                                    best_break = last_occurrence + len(ending)
                                    break
                            
                            if best_break > 0:
                                content_with_page = truncated[:best_break].rstrip() + "..."
                            else:
                                # Fallback to simple truncation
                                content_with_page = truncated.rstrip() + "..."
                                
                            print(f"Truncated message from {len(replacement_text)} to {len(content_with_page)} characters")

                        # Debug newlines after truncation
                        final_newline_count = content_with_page.count('\n')
                        print(f"[DEBUG] Final content has {final_newline_count} newlines")
                        print(f"[DEBUG] Final first 200 chars: {repr(content_with_page[:200])}")

                        # Edit the message with the LLM-generated replacement and updated view
                        await self.webhook_manager.edit_via_webhook(
                            name=webhook_name,
                            message_id=data['generating_message_id'],
                            new_content=content_with_page,
                            guild_id=data['message'].guild.id,
                            view=view,
                            target_channel_id=data['channel_id']
                        )
                        print(
                            f"Sent LLM-generated replacement (page {page}/{total_pages}) with updated view for user '{data['username']}'.")
                    except Exception as e:
                        print(f"Error sending LLM-generated replacement: {e}")
                        import traceback
                        traceback.print_exc()

                agent = LLMAgent(name=f"Agent_{model_key}", config=self.config, callback=llm_callback, model_config=model_config)
                self.agents[model_key] = agent
                print(f"Created new LLM agent for user ID {user_id} with model config {model_key}")
            return self.agents[model_key]

    async def cog_unload(self):
        """
        Clean up tasks when the Cog is unloaded.
        """
        async with self.agents_lock:
            for agent in self.agents.values():
                await agent.shutdown()
            self.agents.clear()
        print("MessageHandler Cog has been unloaded and agents have been shut down.")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        try:
            if interaction.type == discord.InteractionType.component:
                custom_id = interaction.data["custom_id"]
                if custom_id in ["reroll", "prev", "next", "trim", "delete", "cancel", "commit"]:
                    await interaction.response.defer()

                    original_message = interaction.message
                    user_id = interaction.user.id

                    if custom_id in ["delete", "cancel", "commit"]:
                        print(f"{custom_id.capitalize()} button clicked by {interaction.user.display_name}")
                        
                        if custom_id == "commit":
                            # Get webhook name from context
                            context = await self.generation_manager.get_context(original_message.id)
                            if not context:
                                print(f"No context found for message {original_message.id}")
                                return
                                
                            webhook_name = context.parameters.get('webhook_name')
                            if not webhook_name:
                                print(f"No webhook name found in context for message {original_message.id}")
                                return
                                
                            print(f"Committing message using original webhook: {webhook_name}")
                            await self.webhook_manager.edit_via_webhook(
                                name=webhook_name,
                                message_id=original_message.id,
                                new_content=original_message.content,
                                guild_id=interaction.guild_id,
                                view=None,  # No buttons
                                target_channel_id=interaction.channel_id
                            )
                            # Clean up context
                            await self.generation_manager.remove_context(original_message.id)
                            print(f"Committed message for {interaction.user.display_name}")
                        else:
                            # Get webhook name from context
                            context = await self.generation_manager.get_context(original_message.id)
                            if not context:
                                print(f"No context found for message {original_message.id}")
                                return
                                
                            webhook_name = context.parameters.get('webhook_name')
                            if not webhook_name:
                                print(f"No webhook name found in context for message {original_message.id}")
                                return

                            # Handle delete and cancel
                            await self.webhook_manager.delete_webhook_message(
                                name=webhook_name,  # Use original webhook
                                message_id=original_message.id,
                                guild_id=interaction.guild_id
                            )
                            print(f"Deleted message for {interaction.user.display_name}")
                            
                            # Clean up context for both delete and cancel
                            await self.generation_manager.remove_context(original_message.id)
                        return

                    if custom_id == "reroll":
                        print(f"Reroll button clicked by {interaction.user.display_name}")

                        # Get the generation context
                        context = await self.generation_manager.get_context(original_message.id)
                        if not context:
                            print(f"No context found for message {original_message.id}")
                            return

                        # Get fresh avatar URL if we have a target member
                        avatar_url = context.parameters.get('avatar_url')
                        if target_member_id := context.parameters.get('target_member_id'):
                            if target_member := interaction.guild.get_member(target_member_id):
                                avatar_url = target_member.display_avatar.url if target_member.display_avatar else None

                        # Get webhook name from context
                        webhook_name = context.parameters.get('webhook_name')
                        if not webhook_name:
                            print(f"No webhook name found in context for message {original_message.id}")
                            return

                        # Prepare data for the LLM agent
                        data = {
                            'message': original_message,
                            'generating_message_id': original_message.id,
                            'channel_id': interaction.channel_id,
                            'username': context.parameters.get('custom_name') or interaction.user.display_name,
                            'webhook': webhook_name,
                            'context': context,
                            'mode': context.parameters.get('mode', 'self'),
                            'seed': context.parameters.get('seed'),
                            'suppress_name': context.parameters.get('suppress_name', False),
                            'custom_name': context.parameters.get('custom_name'),
                            'temperature': context.parameters.get('temperature'),
                            'avatar_url': avatar_url
                        }

                        # Edit the message to show "Regenerating..." using original webhook
                        await self.webhook_manager.edit_via_webhook(
                            name=context.parameters.get('webhook_name'),  # Use original webhook
                            message_id=original_message.id,
                            new_content="Regenerating...",
                            guild_id=interaction.guild_id,
                            target_channel_id=interaction.channel_id
                        )
                        print(f"Regenerating message for {data['username']}")

                        # Interact with the LLM agent (stateful)
                        agent = await self.get_or_create_agent(user_id)
                        await agent.enqueue_message(data)

                        # The button states and content will be updated in the LLM callback

                    elif custom_id in ["prev", "next", "trim"]:
                        print(f"{custom_id.capitalize()} button clicked by {interaction.user.display_name}")

                        context = await self.generation_manager.get_context(original_message.id)
                        if not context:
                            print(f"No context found for message {original_message.id}")
                            return

                        if custom_id == "trim":
                            new_content = self.trim_message(context.current_content)
                            await context.add_generation(new_content)  # Add trimmed version as new generation
                            
                            # Create view with updated button states
                            view = self.create_generation_view(context)
                                
                            # Get webhook name from context
                            webhook_name, _ = await self.get_webhook_from_context(original_message.id, interaction.user.display_name)
                            if not webhook_name:
                                return
                                
                            await self.webhook_manager.edit_via_webhook(
                                name=webhook_name,
                                message_id=original_message.id,
                                new_content=new_content,
                                guild_id=interaction.guild_id,
                                view=view,
                                target_channel_id=interaction.channel_id
                            )
                            print(f"Updated message after trim for {interaction.user.display_name}")
                        else:
                            new_index = context.current_index - 1 if custom_id == "prev" else context.current_index + 1
                            new_content = await context.navigate(new_index)
                            if new_content is None:
                                print(f"Cannot navigate to index {new_index}")
                                return

                            # Get webhook name and context
                            webhook_name, context = await self.get_webhook_from_context(original_message.id, interaction.user.display_name)
                            if not webhook_name or not context:
                                return

                            # Create view with updated button states
                            view = self.create_generation_view(context)
                                
                            await self.webhook_manager.edit_via_webhook(
                                name=webhook_name,
                                message_id=original_message.id,
                                new_content=new_content,
                                guild_id=interaction.guild_id,
                                view=view,
                                target_channel_id=interaction.channel_id
                            )
                            print(f"Updated message after {custom_id} for {interaction.user.display_name}")
        except Exception as e:
            print(f"Error handling interaction: {e}")
            import traceback
            traceback.print_exc()
            # Optionally, you can add more detailed error logging here



# Asynchronous setup function for the Cog
async def setup(bot):
    await bot.add_cog(MessageHandler(bot, bot.get_cog('WebhookManager'), bot.config))
