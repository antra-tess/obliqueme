import random

import discord
from discord.ext import commands
from discord import ButtonStyle
from discord.ui import Button, View
import asyncio
from agents.llm_agent import LLMAgent
from collections import deque


class MessageHandler(commands.Cog):
    def __init__(self, bot, webhook_manager, config):
        super().__init__()  # Initialize the superclass
        self.bot = bot
        self.webhook_manager = webhook_manager
        self.config = config
        self.agents = {}
        self.agents_lock = asyncio.Lock()
        self.message_history = {}  # This will now store history per message
        self.message_current_index = {}  # This will store the current index for each message

    @discord.app_commands.command(
        name="oblique",
        description="Generate a base model simulation of the conversation"
    )
    @discord.app_commands.describe(
        suppress_name="Don't add your name at the end of the prompt",
        custom_name="Use a custom name instead of your display name",
        temperature="Set the temperature for generation (0.1-1.0)"
    )
    async def oblique_command(
        self, 
        interaction: discord.Interaction, 
        suppress_name: bool = False,
        custom_name: str = None,
        temperature: float = None
    ):
        """Slash command version of obliqueme"""
        # Defer the response since this will take some time
        await interaction.response.defer()
        
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

            # Initialize message history
            self.message_history[sent_message.id] = {
                'messages': deque(maxlen=10),
                'options': {
                    'suppress_name': suppress_name,
                    'custom_name': custom_name
                }
            }

            # Prepare data for LLM agent
            data = {
                'message': interaction,  # Pass interaction instead of message
                'generating_message_id': sent_message.id,
                'channel_id': interaction.channel_id,
                'username': custom_name or interaction.user.display_name,
                'webhook': webhook_name,
                'bot': self.bot,
                'user_id': interaction.user.id,
                'suppress_name': suppress_name,
                'custom_name': custom_name,
                'temperature': temperature
            }

            # Get or create agent and process request
            agent = await self.get_or_create_agent(interaction.user.id)
            await agent.enqueue_message(data)

            # Send a completion message
            await interaction.followup.send("Generation started!", ephemeral=True)

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

            # Check for options
            options = message.content.split()[1:]  # Get all words after the keyword
            suppress_name = "-s" in options

            # Check for -n option and extract the name
            custom_name = None
            if "-n" in options:
                name_index = options.index("-n") + 1
                if name_index < len(options):
                    custom_name = options[name_index]

            # Check for -p option and extract the temperature
            temperature = None
            if "-p" in options:
                p_index = options.index("-p") + 1
                if p_index < len(options):
                    try:
                        temperature = float(options[p_index])
                    except ValueError:
                        print(f"Invalid temperature value: {options[p_index]}")
                        temperature = None  # Default or handle error as needed

            await self.handle_keyword(message, suppress_name, custom_name, temperature)

        await self.bot.process_commands(message)

    async def handle_keyword(self, message, suppress_name=False, custom_name=None, temperature=None):
        """
        Handles the keyword detection by deleting the user's message,
        replacing it with 'Generating...', and interacting with the LLM agent.

        Args:
            message (discord.Message): The message that triggered the keyword.
            suppress_name (bool): Whether to suppress adding the name at the end of the prompt.
            custom_name (str, optional): A custom name to use instead of the author's display name.
        """
        try:
            # Delete the user's original message
            await message.delete()
            print(f'Deleted message from {message.author.display_name} in channel {message.channel.name}.')

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

            # Initialize message history for this message
            self.message_history[sent_message.id] = {
                'messages': deque(maxlen=10),
                'options': {
                    'suppress_name': suppress_name,
                    'custom_name': custom_name
                }
            }
            if not sent_message:
                print("Failed to send 'Generating...' message via webhook.")
                return
            print(f"Sent 'Generating...' message via webhook '{webhook}' with message ID {sent_message.id}.")

            # Prepare data for the LLM agent
            data = {
                'message': message,
                'generating_message_id': sent_message.id,
                'channel_id': message.channel.id,
                'username': custom_name or message.author.display_name,
                'webhook': webhook_name,
                'bot': self.bot,
                'user_id': message.author.id,
                'suppress_name': suppress_name,
                'custom_name': custom_name,
                'temperature': temperature  # Add this line
            }

            # Store the original options
            self.message_history[sent_message.id] = {
                'options': {
                    'suppress_name': suppress_name,
                    'custom_name': custom_name
                }
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
                        user_id = data['message'].author.id

                        # Get the channel object
                        channel = self.bot.get_channel(channel_id)
                        if not channel:
                            print(f"Channel with ID {channel_id} not found.")
                            return

                        # Update the message history
                        if generating_message_id not in self.message_history:
                            self.message_history[generating_message_id] = {'messages': deque(maxlen=10),
                                                                           'options': data.get('options', {})}
                        elif 'messages' not in self.message_history[generating_message_id]:
                            self.message_history[generating_message_id]['messages'] = deque(maxlen=10)

                        # Append the new replacement text
                        self.message_history[generating_message_id]['messages'].append(replacement_text)
                        self.message_current_index[generating_message_id] = len(
                            self.message_history[generating_message_id]['messages']) - 1

                        # Create full button set after generation completes
                        history = self.message_history[generating_message_id]['messages']
                        current_index = self.message_current_index[generating_message_id]
                        
                        # Only show full button set if this isn't the first generation
                        if len(history) > 0:
                            view = View()
                            view.add_item(Button(style=ButtonStyle.secondary, label="Prev", custom_id="prev",
                                               disabled=(current_index == 0)))
                            view.add_item(Button(style=ButtonStyle.primary, label="Reroll", custom_id="reroll"))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Next", custom_id="next",
                                               disabled=(current_index == len(history) - 1)))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Trim", custom_id="trim"))
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
                if custom_id in ["reroll", "prev", "next", "trim", "delete", "cancel"]:
                    await interaction.response.defer()

                    original_message = interaction.message
                    user_id = interaction.user.id

                    if custom_id in ["delete", "cancel"]:
                        print(f"{custom_id.capitalize()} button clicked by {interaction.user.display_name}")
                        await self.webhook_manager.delete_webhook_message(
                            name=next(iter(self.webhook_manager.webhook_objects.get(interaction.guild_id, {}))),
                            message_id=original_message.id,
                            guild_id=interaction.guild_id
                        )
                        print(f"Deleted message for {interaction.user.display_name}")
                        
                        if custom_id == "cancel":
                            # TODO: Add logic to cancel the generation in the LLM agent
                            if original_message.id in self.message_history:
                                del self.message_history[original_message.id]
                        return

                    if custom_id == "reroll":
                        print(f"Reroll button clicked by {interaction.user.display_name}")

                        # Retrieve the original options
                        original_options = self.message_history.get(original_message.id, {}).get('options', {})

                        # Prepare data for the LLM agent
                        data = {
                            'message': original_message,
                            'generating_message_id': original_message.id,
                            'channel_id': interaction.channel_id,
                            'username': original_options.get('custom_name') or interaction.user.display_name,
                            'webhook': next(iter(self.webhook_manager.webhook_objects.get(interaction.guild_id, {}))),  # Get the first webhook name
                            'suppress_name': original_options.get('suppress_name', False),
                            'custom_name': original_options.get('custom_name')
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

                        if original_message.id in self.message_history:
                            history = self.message_history[original_message.id]['messages']
                            current_index = self.message_current_index.get(original_message.id, len(history) - 1)

                            if custom_id == "trim":
                                new_content = self.trim_message(history[current_index])
                                history[current_index] = new_content
                            else:
                                new_index = current_index - 1 if custom_id == "prev" else current_index + 1
                                if 0 <= new_index < len(history):
                                    new_content = history[new_index]
                                    self.message_current_index[original_message.id] = new_index
                                    current_index = new_index
                                else:
                                    print(
                                        f"Cannot go {custom_id} from the current message. Current index: {current_index}, New index: {new_index}, History length: {len(history)}")
                                    return

                            # Update the message content and button states
                            view = View()
                            view.add_item(Button(style=ButtonStyle.secondary, label="Prev", custom_id="prev",
                                                 disabled=(current_index == 0)))
                            view.add_item(Button(style=ButtonStyle.primary, label="Reroll", custom_id="reroll"))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Next", custom_id="next",
                                                 disabled=(current_index == len(history) - 1)))
                            view.add_item(Button(style=ButtonStyle.secondary, label="Trim", custom_id="trim"))
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
