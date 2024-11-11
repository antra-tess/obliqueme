import random

import discord
from discord.ext import commands
from discord import ButtonStyle
from discord.ui import Button, View
import asyncio
from agents.llm_agent import LLMAgent
from collections import deque
from generation.context import GenerationManager


class MessageHandler(commands.Cog):
    def __init__(self, bot, webhook_manager, config):
        super().__init__()  # Initialize the superclass
        self.bot = bot
        self.webhook_manager = webhook_manager
        self.config = config
        self.agents = {}
        self.agents_lock = asyncio.Lock()
        self.generation_manager = GenerationManager()

    @discord.app_commands.command(
        name="oblique",
        description="Generate a base model simulation of the conversation"
    )
    @discord.app_commands.describe(
        seed="Starting text for the simulation (optional)",
        mode="Simulation mode: 'self' (default) or 'full'",
        suppress_name="Don't add your name at the end of the prompt",
        custom_name="Use a custom name instead of your display name",
        temperature="Set the temperature for generation (0.1-1.0)"
    )
    async def oblique_command(
        self, 
        interaction: discord.Interaction,
        seed: str = None,
        mode: str = "self",
        suppress_name: bool = False,
        custom_name: str = None,
        temperature: float = None
    ):
        """Slash command version of obliqueme"""
        # Defer the response as ephemeral and delete it later
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get webhooks for this guild
            guild_webhooks = self.webhook_manager.webhook_objects.get(interaction.guild_id, {})
            if not guild_webhooks:
                webhook = await self.webhook_manager.create_webhook('oblique_main', interaction.channel_id)
                webhook_name = 'oblique_main'
            else:
                webhook_name = random.choice(list(guild_webhooks.keys()))
                webhook = await self.webhook_manager.move_webhook(interaction.guild_id, webhook_name, interaction.channel)

            if not webhook:
                await interaction.followup.send("Failed to set up webhook.", ephemeral=True)
                return

            # Create a View with just the Cancel button for generation state
            view = View()
            cancel_button = Button(style=ButtonStyle.danger, label="Cancel", custom_id="cancel")
            view.add_item(cancel_button)

            # Send initial message
            generating_content = "Oblique: Generating..."
            reversed_username = interaction.user.display_name + "[oblique]"
            sent_message = await self.webhook_manager.send_via_webhook(
                name=webhook_name,
                content=generating_content,
                username=reversed_username,
                avatar_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                guild_id=interaction.guild_id,
                view=view
            )

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
                temperature=temperature
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
                'temperature': context.parameters.get('temperature')
            }

            # Get or create agent and process request
            agent = await self.get_or_create_agent(interaction.user.id)
            await agent.enqueue_message(data)
            
            # Delete the deferred response
            await interaction.delete_original_response()

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

            # Get webhooks for this guild
            guild_webhooks = self.webhook_manager.webhook_objects.get(message.guild.id, {})
            if not guild_webhooks:
                # Create initial webhook for this guild if none exist
                webhook = await self.webhook_manager.create_webhook('oblique_main', message.channel.id)
                webhook_name = 'oblique_main'
            else:
                # Select random webhook from this guild's webhooks
                webhook_name = random.choice(list(guild_webhooks.keys()))
                webhook = await self.webhook_manager.move_webhook(message.guild.id, webhook_name, message.channel)

            if not webhook:
                print("Failed to move webhook for the replacement.")
                return

            # Create a View with just the Cancel button for generation state
            view = View()
            cancel_button = Button(style=ButtonStyle.danger, label="Cancel", custom_id="cancel")
            view.add_item(cancel_button)

            # Send 'Generating...' via webhook, capture the message object
            generating_content = "Oblique: Generating..."
            reversed_username = message.author.display_name + "[oblique]"
            sent_message = await self.webhook_manager.send_via_webhook(
                name=webhook_name,
                content=generating_content,
                username=reversed_username,
                avatar_url=message.author.display_avatar.url if message.author.display_avatar else None,
                guild_id=message.guild.id,
                view=view
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
                'temperature': context.parameters.get('temperature')
            }

            # Interact with the LLM agent (stateful)
            agent = await self.get_or_create_agent(message.author.id)
            await agent.enqueue_message(data)

        except discord.errors.NotFound:
            print("The message or webhook was not found.")
        except discord.errors.Forbidden:
            print("Missing permissions to delete messages or manage webhooks.")
        except Exception as e:
            print(f'Error handling keyword: {e}')
            # print stack trace
            import traceback
            traceback.print_exc()

    async def get_or_create_agent(self, user_id):
        """
        Retrieves an existing LLM agent for the user or creates a new one.

        Args:
            user_id (int): The Discord user ID.

        Returns:
            LLMAgent: The stateful LLM agent instance.
        """
        async with self.agents_lock:
            if user_id not in self.agents:
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
                        # Extract necessary information
                        generating_message_id = data['generating_message_id']
                        webhook_name = data['webhook']
                        channel_id = data['channel_id']
                        username = data['username']
                        # Handle both Message and Interaction objects
                        if isinstance(data['message'], discord.Interaction):
                            user_id = data['message'].user.id
                        else:
                            user_id = data['message'].author.id

                        # Get the channel object
                        channel = self.bot.get_channel(channel_id)
                        if not channel:
                            print(f"Channel with ID {channel_id} not found.")
                            return

                        # Get or create context
                        context = await self.generation_manager.get_context(generating_message_id)
                        if not context:
                            print(f"No context found for message {generating_message_id}")
                            return

                        # Add the new generation to context
                        await context.add_generation(replacement_text)
                        
                        # Only show full button set if this isn't the first generation
                        if len(context.history) > 0:
                            view = View()
                            view.add_item(Button(style=ButtonStyle.secondary, label="Prev", custom_id="prev",
                                               disabled=(context.current_index == 0)))
                            view.add_item(Button(style=ButtonStyle.primary, label="+3", custom_id="reroll"))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Next", custom_id="next",
                                               disabled=(context.current_index == len(context.history) - 1)))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Trim", custom_id="trim"))
                            view.add_item(Button(style=ButtonStyle.success, label="Commit", custom_id="commit"))
                            view.add_item(Button(style=ButtonStyle.danger, label="Delete", custom_id="delete"))
                        else:
                            # Keep the cancel button for the first generation
                            view = View()
                            view.add_item(Button(style=ButtonStyle.danger, label="Cancel", custom_id="cancel"))

                        # Add page information to the content
                        content_with_page = f"{replacement_text}"

                        # Edit the message with the LLM-generated replacement and updated view
                        await self.webhook_manager.edit_via_webhook(
                            name=webhook_name,
                            message_id=generating_message_id,
                            new_content=content_with_page,
                            guild_id=data['message'].guild.id,
                            view=view
                        )
                        print(
                            f"Sent LLM-generated replacement (page {page}/{total_pages}) with updated view for user '{username}'.")
                    except Exception as e:
                        print(f"Error sending LLM-generated replacement: {e}")
                        import traceback
                        traceback.print_exc()

                agent = LLMAgent(name=f"Agent_{user_id}", config=self.config, callback=llm_callback)
                self.agents[user_id] = agent
                print(f"Created new LLM agent for user ID {user_id}.")
            return self.agents[user_id]

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
                            # Remove all buttons and keep the content
                            await self.webhook_manager.edit_via_webhook(
                                name=next(iter(self.webhook_manager.webhook_objects.get(interaction.guild_id, {}))),
                                message_id=original_message.id,
                                new_content=original_message.content,
                                guild_id=interaction.guild_id,
                                view=None  # No buttons
                            )
                            # Clean up context
                            await self.generation_manager.remove_context(original_message.id)
                            print(f"Committed message for {interaction.user.display_name}")
                        else:
                            # Handle delete and cancel
                            await self.webhook_manager.delete_webhook_message(
                                name=next(iter(self.webhook_manager.webhook_objects.get(interaction.guild_id, {}))),
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

                        # Prepare data for the LLM agent
                        data = {
                            'message': original_message,
                            'generating_message_id': original_message.id,
                            'channel_id': interaction.channel_id,
                            'username': context.parameters.get('custom_name') or interaction.user.display_name,
                            'webhook': next(iter(self.webhook_manager.webhook_objects.get(interaction.guild_id, {}))),
                            'context': context,
                            'mode': context.parameters.get('mode', 'self'),
                            'seed': context.parameters.get('seed'),
                            'suppress_name': context.parameters.get('suppress_name', False),
                            'custom_name': context.parameters.get('custom_name'),
                            'temperature': context.parameters.get('temperature')
                        }

                        # Edit the message to show "Regenerating..."
                        await self.webhook_manager.edit_via_webhook(
                            name=data['webhook'],
                            message_id=original_message.id,
                            new_content="Regenerating...",
                            guild_id=interaction.guild_id
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
                            
                            # Update the message with trimmed content and buttons
                            view = View()
                            view.add_item(Button(style=ButtonStyle.secondary, label="Prev", custom_id="prev",
                                               disabled=(context.current_index == 0)))
                            view.add_item(Button(style=ButtonStyle.primary, label="+3", custom_id="reroll"))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Next", custom_id="next",
                                               disabled=(context.current_index == len(context.history) - 1)))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Trim", custom_id="trim"))
                            view.add_item(Button(style=ButtonStyle.success, label="Commit", custom_id="commit"))
                            view.add_item(Button(style=ButtonStyle.danger, label="Delete", custom_id="delete"))

                            await self.webhook_manager.edit_via_webhook(
                                name=next(iter(self.webhook_manager.webhook_objects.get(interaction.guild_id, {}))),
                                message_id=original_message.id,
                                new_content=new_content,
                                guild_id=interaction.guild_id,
                                view=view
                            )
                            print(f"Updated message after trim for {interaction.user.display_name}")
                        else:
                            new_index = context.current_index - 1 if custom_id == "prev" else context.current_index + 1
                            new_content = await context.navigate(new_index)
                            if new_content is None:
                                print(f"Cannot navigate to index {new_index}")
                                return

                            # Update the message content and button states
                            view = View()
                            view.add_item(Button(style=ButtonStyle.secondary, label="Prev", custom_id="prev",
                                                 disabled=(context.current_index == 0)))
                            view.add_item(Button(style=ButtonStyle.primary, label="+3", custom_id="reroll"))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Next", custom_id="next",
                                                 disabled=(context.current_index == len(context.history) - 1)))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Trim", custom_id="trim"))
                            view.add_item(Button(style=ButtonStyle.success, label="Commit", custom_id="commit"))
                            view.add_item(Button(style=ButtonStyle.danger, label="Delete", custom_id="delete"))

                            await self.webhook_manager.edit_via_webhook(
                                name=next(iter(self.webhook_manager.webhook_objects.get(interaction.guild_id, {}))),
                                message_id=original_message.id,
                                new_content=new_content,
                                guild_id=interaction.guild_id,
                                view=view
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
