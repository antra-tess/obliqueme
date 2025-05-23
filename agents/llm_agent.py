import asyncio
import aiohttp
import discord
import os
from datetime import datetime
from collections import deque
from bs4 import BeautifulSoup  # For stripping HTML content
from discord import ButtonStyle
from discord.ui import Button, View
import re


class LLMAgent:
    def __init__(self, name, config, callback, model_config=None):
        self.name = name
        self.config = config
        self.model_config = model_config or config.get_model_config(config.get_default_model_key())
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
            print(f"Model config in use: {self.model_config.get('name', 'Unknown')} ({self.model_config.get('model_id', 'Unknown')})")
            try:
                await self.handle_message(data)
                print("Queue item processed successfully")
            except Exception as e:
                print(f"Error processing queue item: {e}")
                import traceback
                traceback.print_exc()
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
            max_tokens = data.get('max_tokens', self.model_config.get('max_tokens', 200))
            temperature = data.get('temperature', 1)  # Default to 0.7 if not specified

            formatted_messages = await self.format_messages(message, bot)
            custom_name = data.get('custom_name')
            # Handle both Message and Interaction objects
            if isinstance(message, discord.Interaction):
                name = self._clean_username(custom_name or message.user.display_name)
            else:
                name = self._clean_username(custom_name or message.author.display_name)

            # Add seed text if provided
            prompt = formatted_messages
            if self.model_config.get('type') == 'instruct':
                # Use colon format for instruct models
                if data.get('seed'):
                    prompt += f'{name}: {data["seed"]}'
                else:
                    prompt += f'{name}:'
            else:
                # Use XML tag format for base models
                if data.get('seed'):
                    prompt += f'<{name}> {data["seed"]}\n'
                elif not data.get('suppress_name', False):
                    prompt += f'<{name}>\n'

            # Request completions - use n parameter if supported, otherwise make separate requests
            if self.model_config.get('supports_n_parameter', False):
                # Use single request with n=3 for models that support it
                print(f"Using n parameter for model {self.model_config.get('name')}")
                completions = await self.send_completion_request_with_n(prompt, max_tokens, temperature, n=3)
            else:
                # Fall back to separate requests for models that don't support n parameter
                print(f"Using separate requests for model {self.model_config.get('name')}")
                completion_tasks = [self.send_completion_request(prompt, max_tokens, temperature) for _ in range(3)]
                completions = await asyncio.gather(*completion_tasks)

            print(f"Received {len(completions)} completions from API")

            # Filter out empty completions and process valid ones
            valid_completions = []
            for response_text in completions:
                print(f"Response text: {response_text}")
                replacement_text = self.process_response(response_text, data)
                if replacement_text and replacement_text != "Error: No response from LLM.":
                    print("is valid")
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
        Formats the last messages into appropriate format based on model type.
        - Base models: XML tags format
        - Instruct models: Colon format
        For threads, includes both parent channel and thread history.

        Args:
            message (discord.Message): The triggering message.
            bot (discord.Client, optional): The bot instance.

        Returns:
            str: Formatted string.
        """
        channel = message.channel if isinstance(message, discord.Message) else bot.get_channel(message.channel_id)
        formatted = []
        all_messages = []
        
        try:
            # Check if we're in a thread
            if hasattr(channel, 'parent_id') and channel.parent_id:
                print(f"[DEBUG] Collecting history from thread '{channel.name}' and parent channel")
                
                # Get parent channel
                parent_channel = bot.get_channel(channel.parent_id) if bot else message.guild.get_channel(channel.parent_id)
                
                if parent_channel:
                    # Collect messages from parent channel (limit to half the total)
                    parent_limit = self.config.MESSAGE_HISTORY_LIMIT // 2
                    print(f"[DEBUG] Getting {parent_limit} messages from parent channel")
                    async for msg in parent_channel.history(limit=parent_limit):
                        all_messages.append((msg, 'parent'))
                    
                    # Collect messages from thread (remaining limit)
                    thread_limit = self.config.MESSAGE_HISTORY_LIMIT - len(all_messages)
                    print(f"[DEBUG] Getting {thread_limit} messages from thread")
                    async for msg in channel.history(limit=thread_limit, 
                                                   before=message if isinstance(message, discord.Message) else None):
                        all_messages.append((msg, 'thread'))
                else:
                    print(f"[DEBUG] Parent channel {channel.parent_id} not found, using thread only")
                    async for msg in channel.history(limit=self.config.MESSAGE_HISTORY_LIMIT,
                                                   before=message if isinstance(message, discord.Message) else None):
                        all_messages.append((msg, 'thread'))
            else:
                print(f"[DEBUG] Regular channel, collecting normal history")
                # Regular channel - collect normally
                async for msg in channel.history(limit=self.config.MESSAGE_HISTORY_LIMIT,
                                               before=message if isinstance(message, discord.Message) else None):
                    all_messages.append((msg, 'channel'))
            
            # Sort all messages by timestamp (oldest first for context)
            all_messages.sort(key=lambda x: x[0].created_at)
            
            print(f"[DEBUG] Total messages collected: {len(all_messages)}")
            
            # Process messages
            for msg, source in all_messages:
                # Skip bot messages if desired
                # if msg.author.bot:
                #    continue
                
                # Get clean username without any square bracket content
                username = msg.author.display_name
                username = self._clean_username(username)

                # Clean up content if it contains oblique tags
                content = msg.content
                if "[oblique:" in content:
                    content = content.split("[oblique:")[0].strip()
                content = content.replace("[oblique]", "").strip()

                # Convert mentions to readable format
                for mention in msg.mentions:
                    content = content.replace(f'<@{mention.id}>', f'@{mention.display_name}')
                    content = content.replace(f'<@!{mention.id}>', f'@{mention.display_name}')  # Handle mentions with !
                for role_mention in msg.role_mentions:
                    content = content.replace(f'<@&{role_mention.id}>', f'@{role_mention.name}')
                if msg.mention_everyone:
                    content = content.replace('@everyone', '@everyone')
                    content = content.replace('@here', '@here')

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
                
                # Format based on model type
                if self.model_config.get('type') == 'instruct':
                    # Use colon format for instruct models
                    formatted.append(f'{username}: {clean_content}\n')
                else:
                    # Use XML tag format for base models
                    formatted.append(f'{username}> {clean_content}\n')
                    
        except Exception as e:
            print(f"Error formatting messages: {e}")
            
        return "".join(formatted)

    async def send_completion_request_with_n(self, prompt, max_tokens, temperature, n=3):
        """
        Sends a single completion request with n parameter for multiple completions.

        Args:
            prompt (str): The formatted prompt.
            max_tokens (int): The maximum number of tokens for the response.
            temperature (float): The temperature for generation.
            n (int): Number of completions to generate.

        Returns:
            list[str]: List of response texts from the LLM.
        """
        print(f"[DEBUG] Starting send_completion_request_with_n with n={n}")
        print(f"[DEBUG] Model: {self.model_config.get('model_id')}")
        print(f"[DEBUG] Endpoint: {self.model_config.get('endpoint')}")
        print(f"[DEBUG] Prompt length: {len(prompt)}")
        
        # Create log file name with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        # Sanitize agent name for filesystem by replacing unsafe characters
        safe_name = self.name.replace("/", "_").replace("\\", "_").replace(":", "_")
        log_file = os.path.join(self.log_dir, f"{safe_name}_{timestamp}.log")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.OPENROUTER_API_KEY}",
            "X-Title": "Oblique"
        }

        if temperature is None:
            temperature = 1

        # Choose API format based on model type
        if self.model_config.get('type') == 'instruct':
            # Use chat API with prefill for instruct models
            payload = {
                "model": self.model_config.get('model_id'),
                "messages": [
                    {"role": "system", "content": self.model_config.get('system_prompt', '')},
                    {"role": "user", "content": self.model_config.get('user_prefix', '')},
                    {"role": "assistant", "content": prompt}  # Prefill with the entire chat history
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "n": n
            }
            
            # Add provider settings if quantization is specified
            if self.model_config.get('quantization'):
                payload["provider"] = {
                    "quantizations": [self.model_config.get('quantization')]
                }
                
            endpoint = self.model_config.get('endpoint')
        else:
            # Use completions API for base models
            payload = {
                "model": self.model_config.get('model_id'),
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "n": n
            }
            
            # Add provider settings if quantization is specified
            if self.model_config.get('quantization'):
                payload["provider"] = {
                    "quantizations": [self.model_config.get('quantization')]
                }
                
            endpoint = self.model_config.get('endpoint')

        # Log the request
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("=== REQUEST ===\n")
            f.write(f"Timestamp: {timestamp}\n")
            f.write(f"Model Type: {self.model_config.get('type')}\n")
            f.write(f"Model: {self.model_config.get('model_id')}\n")
            f.write(f"Temperature: {temperature}\n")
            f.write(f"Max Tokens: {max_tokens}\n")
            f.write(f"N: {n}\n")
            f.write(f"Endpoint: {endpoint}\n")
            if self.model_config.get('type') == 'instruct':
                f.write("=== MESSAGES ===\n")
                for msg in payload["messages"]:
                    f.write(f"{msg['role']}: {msg['content']}\n")
            else:
                f.write("=== PROMPT ===\n")
                f.write(prompt)
            f.write("\n")

        print(f"Sending LLM request with n={n}, model_type: {self.model_config.get('type')}, model: {self.model_config.get('model_id')}, length: {len(prompt)}, max_tokens: {max_tokens}, temperature: {temperature}")

        for _ in range(10):
            try:
                print(f"[DEBUG] Attempting API request to {endpoint}")
                async with self.rate_limit:
                    async with self.session.post(endpoint, json=payload,
                                                 headers=headers) as resp:
                        print(f"[DEBUG] Received response with status {resp.status}")
                        response_text = await resp.text()
                        print(f"[DEBUG] Response text length: {len(response_text)}")
                        
                        # Log the raw response
                        with open(log_file, "a", encoding="utf-8") as f:
                            f.write("\n=== RESPONSE ===\n")
                            f.write(f"Status: {resp.status}\n")
                            f.write(response_text)
                            f.write("\n")

                        if resp.status != 200:
                            print(f"API returned status {resp.status}: {response_text}")
                            return [""] * n  # Return empty strings for all expected completions
                        
                        data = await resp.json()
                        print(f"[DEBUG] Parsed JSON response, processing {len(data.get('choices', []))} choices")
                        if 'error' in data:
                            print(f"API returned error: {data['error']}")
                            if data['error'].get('code') == 429:
                                print("Rate limit hit. Retrying in 1 second...")
                                await asyncio.sleep(1)
                                continue
                            return [""] * n
                        
                        # Extract all results based on API type
                        results = []
                        choices = data.get("choices", [])
                        
                        for choice in choices:
                            if self.model_config.get('type') == 'instruct':
                                # Chat API response format
                                result = choice.get("message", {}).get("content", "")
                            else:
                                # Completions API response format
                                result = choice.get("text", "")
                            results.append(result)
                        
                        # Ensure we return the expected number of results
                        while len(results) < n:
                            results.append("")
                        
                        # Log the extracted results
                        with open(log_file, "a", encoding="utf-8") as f:
                            f.write("\n=== EXTRACTED RESULTS ===\n")
                            for i, result in enumerate(results):
                                f.write(f"Result {i+1}: {result}\n")
                            f.write("\n")
                            
                        return results[:n]  # Return exactly n results
            except aiohttp.ClientError as e:
                print(f"HTTP Client Error: {e}. Retrying in 5 seconds...")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"Error sending completion request: {e}")
                return [""] * n
        print("Failed to send completion request after 10 retries.")
        return [""] * n

    async def send_completion_request(self, prompt, max_tokens, temperature):
        """
        Sends the prompt to the completion or chat endpoint based on model type.

        Args:
            prompt (str): The formatted prompt.
            max_tokens (int): The maximum number of tokens for the response.
            temperature (float): The temperature for generation.

        Returns:
            str: The response text from the LLM.
        """
        # Create log file name with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        # Sanitize agent name for filesystem by replacing unsafe characters
        safe_name = self.name.replace("/", "_").replace("\\", "_").replace(":", "_")
        log_file = os.path.join(self.log_dir, f"{safe_name}_{timestamp}.log")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.OPENROUTER_API_KEY}",
            "X-Title": "Oblique"
        }

        if temperature is None:
            temperature = 1

        # Choose API format based on model type
        if self.model_config.get('type') == 'instruct':
            # Use chat API with prefill for instruct models
            payload = {
                "model": self.model_config.get('model_id'),
                "messages": [
                    {"role": "system", "content": self.model_config.get('system_prompt', '')},
                    {"role": "user", "content": self.model_config.get('user_prefix', '')},
                    {"role": "assistant", "content": prompt}  # Prefill with the entire chat history
                ],
                "max_tokens": max_tokens,
                "temperature": temperature
            }
            
            # Add provider settings if quantization is specified
            if self.model_config.get('quantization'):
                payload["provider"] = {
                    "quantizations": [self.model_config.get('quantization')]
                }
                
            endpoint = self.model_config.get('endpoint')
        else:
            # Use completions API for base models
            payload = {
                "model": self.model_config.get('model_id'),
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature
            }
            
            # Add provider settings if quantization is specified
            if self.model_config.get('quantization'):
                payload["provider"] = {
                    "quantizations": [self.model_config.get('quantization')]
                }
                
            endpoint = self.model_config.get('endpoint')

        # Log the request
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("=== REQUEST ===\n")
            f.write(f"Timestamp: {timestamp}\n")
            f.write(f"Model Type: {self.model_config.get('type')}\n")
            f.write(f"Model: {self.model_config.get('model_id')}\n")
            f.write(f"Temperature: {temperature}\n")
            f.write(f"Max Tokens: {max_tokens}\n")
            f.write(f"Endpoint: {endpoint}\n")
            if self.model_config.get('type') == 'instruct':
                f.write("=== MESSAGES ===\n")
                for msg in payload["messages"]:
                    f.write(f"{msg['role']}: {msg['content']}\n")
            else:
                f.write("=== PROMPT ===\n")
                f.write(prompt)
            f.write("\n")

        print(f"Sending LLM request, model_type: {self.model_config.get('type')}, model: {self.model_config.get('model_id')}, length: {len(prompt)}, max_tokens: {max_tokens}, temperature: {temperature}")

        for _ in range(10):
            try:
                async with self.rate_limit:
                    async with self.session.post(endpoint, json=payload,
                                                 headers=headers) as resp:
                        response_text = await resp.text()
                        
                        # Log the raw response
                        with open(log_file, "a", encoding="utf-8") as f:
                            f.write("\n=== RESPONSE ===\n")
                            f.write(f"Status: {resp.status}\n")
                            f.write(response_text)
                            f.write("\n")

                        if resp.status != 200:
                            print(f"API returned status {resp.status}: {response_text}")
                            return ""
                        
                        data = await resp.json()
                        if 'error' in data:
                            print(f"API returned error: {data['error']}")
                            if data['error'].get('code') == 429:
                                print("Rate limit hit. Retrying in 1 second...")
                                await asyncio.sleep(1)
                                continue
                            return ""
                        
                        # Extract result based on API type
                        if self.model_config.get('type') == 'instruct':
                            # Chat API response format
                            result = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        else:
                            # Completions API response format
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

        print(f"[DEBUG] Raw response has {response_text.count(chr(10))} newlines")

        # Remove common termination tags if present
        termination_tags = ["</stop>", "</xml>", "<|end|>", "<|endoftext|>"]
        processed_text = response_text
        for tag in termination_tags:
            if tag in processed_text:
                termination_index = processed_text.find(tag)
                processed_text = processed_text[:termination_index]
                break

        # Clean up any remaining termination tags
        for tag in termination_tags:
            processed_text = processed_text.replace(tag, "")

        print(f"[DEBUG] After tag removal has {processed_text.count(chr(10))} newlines")

        # Clean up oblique tags from the response
        processed_text = self._clean_oblique_tags(processed_text)

        print(f"[DEBUG] After oblique tag cleaning has {processed_text.count(chr(10))} newlines")

        # Handle different modes
        if data and data.get('mode') == 'self':
            # Get the username for filtering
            username = data.get('username', '').replace("[oblique]", "")
            
            # For self mode, extract content that belongs to the target user
            if self.model_config.get('type') == 'instruct':
                # For colon format, find the user's section
                result = self._extract_user_content_colon_format(processed_text, username)
            else:
                # For XML format, find the user's section  
                result = self._extract_user_content_xml_format(processed_text, username)
                
            final_result = result if result.strip() else processed_text.strip()
        else:
            # For full mode, return the entire response (cleaned)
            final_result = processed_text.strip()

        print(f"[DEBUG] Final processed result has {final_result.count(chr(10))} newlines")
        return final_result

    def _extract_user_content_colon_format(self, text, username):
        """
        Extract content for a specific user from colon-formatted text.
        This handles multi-line responses better by looking for the user's section.
        Uses sophisticated heuristics to distinguish speaker changes from regular colons.
        """
        lines = text.split('\n')
        user_content = []
        in_user_section = False
        
        for line in lines:
            original_line = line
            line = line.strip()
            if not line:
                if in_user_section:
                    user_content.append('')  # Keep empty lines within user section
                continue
                
            # Check if this line likely starts a new speaker
            is_speaker_line = self._is_likely_speaker_line_colon(line)
            
            if is_speaker_line:
                speaker_part = line.split(':', 1)[0].strip()
                # If this line starts with our target username
                if speaker_part.lower() == username.lower():
                    in_user_section = True
                    # Add the content after the colon
                    content_after_colon = line.split(':', 1)[1].strip()
                    if content_after_colon:
                        user_content.append(content_after_colon)
                else:
                    # This line starts with a different speaker, stop collecting
                    if in_user_section:
                        break
                    in_user_section = False
            else:
                # This is a continuation line (not a speaker change)
                if in_user_section:
                    user_content.append(line)
        
        return '\n'.join(user_content)

    def _is_likely_speaker_line_colon(self, line):
        """
        Determine if a line with a colon is likely indicating a speaker change
        rather than just containing a colon in regular text.
        
        Heuristics:
        - Colon should be early in the line (within first ~30 characters)
        - Speaker part should be reasonable length (1-25 characters)
        - Speaker part shouldn't contain certain punctuation
        - Speaker part should look like a name/identifier
        """
        if ':' not in line:
            return False
            
        colon_pos = line.find(':')
        speaker_part = line[:colon_pos].strip()
        
        # Colon should be reasonably early in the line (not buried in a sentence)
        if colon_pos > 30:
            return False
            
        # Speaker part should be reasonable length
        if len(speaker_part) < 1 or len(speaker_part) > 25:
            return False
            
        # Speaker part shouldn't contain punctuation that's unlikely in names
        # Allow spaces, hyphens, underscores, apostrophes, but not much else
        invalid_chars = set('.,!?;()[]{}|\\/"<>+=*&^%$#@`~')
        if any(char in invalid_chars for char in speaker_part):
            return False
            
        # Speaker part shouldn't contain numbers in patterns that suggest time/dates
        # e.g., "3:00", "12:30", "2023:01"
        if any(char.isdigit() for char in speaker_part):
            # If it's all digits or digits with common time separators, probably not a speaker
            cleaned = speaker_part.replace(' ', '').replace('-', '').replace(':', '')
            if cleaned.isdigit() or len(cleaned) <= 4:
                return False
                
        # If we get here, it looks like a plausible speaker line
        return True

    def _extract_user_content_xml_format(self, text, username):
        """
        Extract content for a specific user from XML-formatted text.
        This handles multi-line responses better by looking for the user's section.
        """
        lines = text.split('\n')
        user_content = []
        in_user_section = False
        
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                if in_user_section:
                    user_content.append('')  # Keep empty lines within user section
                continue
                
            # Check if this line starts a new speaker with XML tags
            if line_stripped.startswith('<') and '>' in line_stripped:
                # Extract the tag name
                end_tag_pos = line_stripped.find('>')
                tag_content = line_stripped[1:end_tag_pos]
                
                # If this tag matches our target username
                if tag_content.lower() == username.lower():
                    in_user_section = True
                    # Add the content after the tag
                    content_after_tag = line_stripped[end_tag_pos + 1:].strip()
                    if content_after_tag:
                        user_content.append(content_after_tag)
                else:
                    # This line starts with a different speaker, stop collecting
                    if in_user_section:
                        break
                    in_user_section = False
            else:
                # This is a continuation line (no XML tag at start)
                if in_user_section:
                    user_content.append(line_stripped)
        
        return '\n'.join(user_content)

    def _clean_oblique_tags(self, text):
        """
        Cleans oblique tags from the text while preserving other content.
        Removes patterns like [oblique:username] and [oblique].

        Args:
            text (str): The input text.

        Returns:
            str: The cleaned text.
        """
        # Remove [oblique:username] patterns
        text = re.sub(r'\[oblique:[^\]]*\]', '', text)
        
        # Remove standalone [oblique] tags
        text = text.replace('[oblique]', '')
        
        # Clean up any extra whitespace that might remain
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text

    def _clean_username(self, username):
        """
        Cleans username by removing all square bracket content.

        Args:
            username (str): The input username.

        Returns:
            str: The cleaned username.
        """
        return re.sub(r'\[.*?\]', '', username).strip()

    async def enqueue_message(self, data):
        print(f"[DEBUG] LLMAgent.enqueue_message called for {self.name}")
        print(f"[DEBUG] Queue size before: {self.queue.qsize()}")
        await self.queue.put(data)
        print(f"[DEBUG] Message added to queue, size now: {self.queue.qsize()}")

    async def shutdown(self):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        await self.session.close()
