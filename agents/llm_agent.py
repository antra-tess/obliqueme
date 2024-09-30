import asyncio
import aiohttp
from bs4 import BeautifulSoup  # For stripping HTML content

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
            data (dict): Contains 'message', 'generating_message_id', 'channel_id', 'username'.
        """
        try:
            # Extract necessary info
            message = data['message']

            # Step 1: Fetch and format the last 50 messages
            formatted_messages = await self.format_messages(message)

            name = message.author.display_name
            # remove "[oblique]" from name if it exists
            if name.endswith("[oblique]"):
                name = name[:-9]

            # Step 2: Append the triggering message tag
            prompt = formatted_messages + f'<msg username="{name}">'

            # Step 3: Send the prompt to OpenRouter
            response_text = await self.send_completion_request(prompt)
            print("response_text", response_text)

            # Step 4: Process the LLM response
            replacement_text = self.process_response(response_text)

            # Step 5: Use the replacement text via the callback
            await self.callback(data, replacement_text)

            print(f"LLMAgent '{self.name}' generated replacement text.")

        except Exception as e:
            print(f"Error in LLMAgent '{self.name}': {e}")

    async def format_messages(self, message):
        """
        Formats the last 50 messages into XML tags.

        Args:
            message (discord.Message): The triggering message.

        Returns:
            str: Formatted XML string.
        """
        channel = message.channel
        formatted = []
        try:
            async for msg in channel.history(limit=self.config.MESSAGE_HISTORY_LIMIT, before=message):
                #if msg.author.bot:
                #    continue  # Skip bot messages if desired
                username = msg.author.display_name
                content = msg.content

                # Strip HTML content
                soup = BeautifulSoup(content, "html.parser")
                clean_content = soup.get_text()

                # Preserve newlines
                clean_content = clean_content.replace('\n', '\\n')  # Escape newlines

                formatted.append(f'<msg username="{username}">{clean_content}</msg>\n')
        except Exception as e:
            print(f"Error formatting messages: {e}")
        formatted.reverse()
        return "".join(formatted)

    async def send_completion_request(self, prompt):
        """
        Sends the prompt to OpenRouter's completion endpoint.

        Args:
            prompt (str): The formatted XML prompt.

        Returns:
            str: The response text from the LLM.
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.OPENROUTER_API_KEY}",
            "X-Title": "Oblique"
        }

        payload = {
            "model": "meta-llama/llama-3.1-405b",
            "prompt": prompt,
            "max_tokens": self.config.MAX_RESPONSE_LENGTH,
            "temperature": 0.9,
            "stop": ["</msg>"],  # Assuming </xml> is used to terminate
            "provider": {
                "quantizations": ["bf16"]
            }
        }

        for _ in range(10):
            try:
                async with self.rate_limit:
                    print("Sending llm request, length", len(prompt))
                    # save payload to payload.json
                    # with open("payload.json", "w") as f:
                    #     import json
                    #     json.dump(payload, f)
                    # # save headers as a curl command to curl.sh
                    # with open("curl.sh", "w") as f:
                    #     f.write(f"curl -X POST {self.config.OPENROUTER_ENDPOINT} \\\n")
                    #     for key, value in headers.items():
                    #         f.write(f"  -H '{key}: {value}' \\\n")
                    #     f.write(f"  -d @payload.json")

                    async with self.session.post(self.config.OPENROUTER_ENDPOINT, json=payload, headers=headers) as resp:
                        print("Status", resp.status)
                        if resp.status != 200:
                            error_text = await resp.text()
                            print(f"OpenRouter API returned status {resp.status}: {error_text}")
                            print(f"Prompt: {prompt}")
                            print(resp)
                            return ""
                        data = await resp.json()
                        print("LLM", data)
                        if 'error' in data:
                            print(f"OpenRouter API returned error: {data['error']}")
                            if data['error'].get('code') == 429:
                                print("Rate limit (error code 429) hit in response. Retrying in 1 second...")
                                await asyncio.sleep(1)
                                continue  # Retr
                            return ""
                        return data.get("choices", [{}])[0].get("text", "")
            except aiohttp.ClientError as e:
                print(f"HTTP Client Error: {e}. Retrying in 1 second...")
                await asyncio.sleep(5)
                continue  # Retry the request
            except asyncio.CancelledError:
                # Allow the task to be cancelled
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
