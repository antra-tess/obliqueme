import asyncio
import aiohttp
import discord
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

    async def process_queue(self):
        while True:
            data = await self.queue.get()
            await self.handle_message(data)
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

            prompt = formatted_messages
            if not data.get('suppress_name', False):
                prompt += f'<{name}>\n'

            # Request three completions concurrently
            completion_tasks = [self.send_completion_request(prompt, max_tokens, temperature) for _ in range(3)]
            completions = await asyncio.gather(*completion_tasks)

            # Process each completion and send it to the callback
            for i, response_text in enumerate(completions):
                replacement_text = self.process_response(response_text)
                await self.callback(data, replacement_text, page=i + 1, total_pages=3)

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
                'content': [self.process_response(text) for text in completions]
            })

            print(f"LLMAgent '{self.name}' generated 3 replacement texts.")

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

                if clean_content.startswith(".."):
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
            "temperature": temperature,  # Use the temperature parameter
            "provider": {
                "quantizations": ["bf16"]
            }
        }
        print(f"Sending LLM request, length: {len(prompt)}, max_tokens: {max_tokens}, temperature: {temperature}")

        for _ in range(10):
            try:
                async with self.rate_limit:
                    print(f"Sending LLM request, length: {len(prompt)}, max_tokens: {max_tokens}")
                    async with self.session.post(self.config.OPENROUTER_ENDPOINT, json=payload,
                                                 headers=headers) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            print(f"OpenRouter API returned status {resp.status}: {error_text}")
                            return ""
                        data = await resp.json()
                        if 'error' in data:
                            print(f"OpenRouter API returned error: {data['error']}")
                            if data['error'].get('code') == 429:
                                print("Rate limit hit. Retrying in 1 second...")
                                await asyncio.sleep(1)
                                continue
                            return ""
                        return data.get("choices", [{}])[0].get("text", "")
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

    def process_response(self, response_text):
        """
        Processes the LLM response to extract the required text.

        Args:
            response_text (str): The raw response from the LLM.

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
