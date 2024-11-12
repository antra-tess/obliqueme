import asyncio
import aiohttp
import discord
import os
from datetime import datetime
from collections import deque
from bs4 import BeautifulSoup  # For stripping HTML content
from discord import ButtonStyle
from discord.ui import Button, View


class LLMAgent:
    def __init__(self, name, config, callback):
        self.name = name
        self.config = config
        self.callback = callback  # Function to call with the response
        self.state = {}
        self.queue = asyncio.Queue()
        self.session = aiohttp.ClientSession()
        self.task = asyncio.create_task(self.process_queue())
        self.rate_limit = asyncio.Semaphore(5)  # Adjust based on API rate limits
        
        # Set up logging directory
        self.log_dir = "logs"
        os.makedirs(self.log_dir, exist_ok=True)

    async def process_queue(self):
        while True:
            print("\nWaiting for queue item...")
            data = await self.queue.get()
            print(f"Processing queue item for user {data.get('username')}")
            await self.handle_message(data)
            print("Queue item processed")
            self.queue.task_done()

    async def handle_message(self, data):
        """
        Processes a single message: formats, sends to LLM, handles response.

        Args:
            data (dict): Contains 'message', 'generating_message_id', 'channel_id', 'username', 'bot', 'webhook', 'max_tokens'.
        """
        try:
            message = data['message']
            bot = data.get('bot')
            max_tokens = data.get('max_tokens', self.config.MAX_RESPONSE_LENGTH)
            temperature = data.get('temperature', 0.7)  # Default to 0.7 if not specified

            formatted_messages = await self.format_messages(message, bot)
            custom_name = data.get('custom_name')
            # Handle both Message and Interaction objects
            if isinstance(message, discord.Interaction):
                name = (custom_name or message.user.display_name).replace("[oblique]", "")
            else:
                name = (custom_name or message.author.display_name).replace("[oblique]", "")

            # Add seed text if provided
            prompt = formatted_messages
            if data.get('seed'):
                prompt += f'<{name}> {data["seed"]}\n'
            elif not data.get('suppress_name', False):
                prompt += f'<{name}>\n'

            # Request three completions concurrently
            completion_tasks = [self.send_completion_request(prompt, max_tokens, temperature) for _ in range(3)]
            completions = await asyncio.gather(*completion_tasks)

            # Filter out empty completions and process valid ones
            valid_completions = []
            for response_text in completions:
                replacement_text = self.process_response(response_text, data)
                if replacement_text and replacement_text != "Error: No response from LLM.":
                    valid_completions.append(replacement_text)

            # Handle case when all completions are empty
            if not valid_completions:
                replacement_text = "No valid response generated. Please try again."
                await self.callback(data, replacement_text, page=1, total_pages=1)
                print(f"LLMAgent '{self.name}' generated no valid completions.")
                return

            # Send valid completions to callback with correct page numbers
            total_pages = len(valid_completions)
            for i, replacement_text in enumerate(valid_completions):
                await self.callback(data, replacement_text, page=i + 1, total_pages=total_pages)

            # Handle both Message and Interaction objects
            if isinstance(message, discord.Interaction):
                user_id = message.user.id
            else:
                user_id = message.author.id
            if not hasattr(self, 'message_history'):
                self.message_history = {}
            if user_id not in self.message_history:
                self.message_history[user_id] = deque(maxlen=10)

            self.message_history[user_id].append({
                'id': data['generating_message_id'],
                'content': valid_completions
            })

            print(f"LLMAgent '{self.name}' generated {total_pages} valid replacement texts.")

        except Exception as e:
            print(f"Error in LLMAgent '{self.name}': {e}")
            import traceback
            traceback.print_exc()

    async def format_messages(self, message, bot=None):
        """
        Formats the last 50 messages into XML tags.

        Args:
            message (discord.Message): The triggering message.
            bot (discord.Client, optional): The bot instance.

        Returns:
            str: Formatted XML string.
        """
        channel = message.channel if isinstance(message, discord.Message) else bot.get_channel(message.channel_id)
        formatted = []
        try:
            async for msg in channel.history(limit=self.config.MESSAGE_HISTORY_LIMIT,
                                             before=message if isinstance(message, discord.Message) else None):
                # if msg.author.bot:
                #    continue  # Skip bot messages if desired
                username = msg.author.display_name
                username = username.replace("[oblique]", "")
                content = msg.content

                #                print("content", "[" + content + "]")
                if content == "oblique_clear":
                    print("clearing")
                    break

                # Strip HTML content
                try:
                    soup = BeautifulSoup(content, "html.parser")
                    clean_content = soup.get_text()
                except Exception as e:
                    clean_content = content

                if clean_content.startswith(".") or clean_content == "Oblique: Generating..." or clean_content == "Regenerating...":
                    continue

                # Preserve newlines
                clean_content = clean_content.replace('\n', '\\n')  # Escape newlines

                formatted.append(f'<{username}> {clean_content}\n')
        except Exception as e:
            print(f"Error formatting messages: {e}")
        formatted.reverse()
        return "".join(formatted)

    async def send_completion_request(self, prompt, max_tokens, temperature):
        """
        Sends the prompt to OpenRouter's completion endpoint.

        Args:
            prompt (str): The formatted XML prompt.
            max_tokens (int): The maximum number of tokens for the response.

        Returns:
            str: The response text from the LLM.
        """
        # Create log file name with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_file = os.path.join(self.log_dir, f"{self.name}_{timestamp}.log")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.OPENROUTER_API_KEY}",
            "X-Title": "Oblique"
        }

        if temperature is None:
            temperature = 0.8

        payload = {
            "model": "meta-llama/Meta-Llama-3.1-405B",
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "provider": {
                "quantizations": ["bf16"]
            }
        }

        # Log the request
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("=== REQUEST ===\n")
            f.write(f"Timestamp: {timestamp}\n")
            f.write(f"Temperature: {temperature}\n")
            f.write(f"Max Tokens: {max_tokens}\n")
            f.write("=== PROMPT ===\n")
            f.write(prompt)
            f.write("\n")

        print(f"Sending LLM request, length: {len(prompt)}, max_tokens: {max_tokens}, temperature: {temperature}")

        for _ in range(10):
            try:
                async with self.rate_limit:
                    print(f"Sending LLM request, length: {len(prompt)}, max_tokens: {max_tokens}")
                    async with self.session.post(self.config.OPENROUTER_ENDPOINT, json=payload,
                                                 headers=headers) as resp:
                        response_text = await resp.text()
                        
                        # Log the raw response
                        with open(log_file, "a", encoding="utf-8") as f:
                            f.write("\n=== RESPONSE ===\n")
                            f.write(f"Status: {resp.status}\n")
                            f.write(response_text)
                            f.write("\n")

                        if resp.status != 200:
                            print(f"OpenRouter API returned status {resp.status}: {response_text}")
                            return ""
                        
                        data = await resp.json()
                        if 'error' in data:
                            print(f"OpenRouter API returned error: {data['error']}")
                            if data['error'].get('code') == 429:
                                print("Rate limit hit. Retrying in 1 second...")
                                await asyncio.sleep(1)
                                continue
                            return ""
                        
                        result = data.get("choices", [{}])[0].get("text", "")
                        
                        # Log the extracted result
                        with open(log_file, "a", encoding="utf-8") as f:
                            f.write("\n=== EXTRACTED RESULT ===\n")
                            f.write(result)
                            f.write("\n")
                            
                        return result
            except aiohttp.ClientError as e:
                print(f"HTTP Client Error: {e}. Retrying in 5 seconds...")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"Error sending completion request: {e}")
                return ""
        print("Failed to send completion request after 10 retries.")
        return ""

    def process_response(self, response_text, data=None):
        """
        Processes the LLM response to extract the required text.

        Args:
            response_text (str): The raw response from the LLM.
            data (dict): The data dictionary containing mode and username information.

        Returns:
            str: The processed replacement text.
        """
        if not response_text:
            return "Error: No response from LLM."

        # Find the first occurrence of </xml>
        termination_tag = "</stop>"
        termination_index = response_text.find(termination_tag)
        if termination_index != -1:
            processed_text = response_text[:termination_index]
        else:
            processed_text = response_text  # Use the whole output if termination tag not found

        # Strip the termination tag if present
        processed_text = processed_text.replace(termination_tag, "")

        # Unescape any escaped newlines
        processed_text = processed_text.replace('\\n', '\n')

        # Handle different modes
        if data and data.get('mode') == 'self':
            # Get the username for filtering
            username = data.get('username', '').replace("[oblique]", "")
            
            # Split into lines and filter for user's messages
            lines = processed_text.split('\n')
            user_messages = []
            for line in lines:
                if line.strip():
                    # Check if line starts with a username tag
                    if line.startswith(f'<{username}>'):
                        # Remove the username tag
                        message = line[len(f'<{username}>'):]
                        user_messages.append(message.strip())
            
            # Join the filtered messages
            return '\n'.join(user_messages)
        else:
            return processed_text

    async def enqueue_message(self, data):
        await self.queue.put(data)

    async def shutdown(self):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        await self.session.close()
