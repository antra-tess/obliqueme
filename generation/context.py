from collections import deque
from datetime import datetime
from typing import Optional, Dict, Any

class GenerationContext:
    def __init__(self, owner_id: int, guild_id: int, **parameters):
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.parameters = {
            'mode': parameters.get('mode', 'self'),
            'seed': parameters.get('seed'),
            'suppress_name': parameters.get('suppress_name', False),
            'custom_name': parameters.get('custom_name'),
            'temperature': parameters.get('temperature'),
            'avatar_url': parameters.get('avatar_url'),
            'webhook_name': parameters.get('webhook_name'),  # Store the webhook name
            'model_key': parameters.get('model_key'),  # Store the selected model key for reroll
            'target_member_id': parameters.get('target_member_id'),  # Store target member for avatar
            'llm_username': parameters.get('llm_username')  # Store username for LLM identification
        }
        self.history = deque(maxlen=10)
        self.current_index = 0
        self.created_at = datetime.now()
        self.message_id: Optional[int] = None

    async def add_generation(self, content: str):
        """Add a new generation to the history."""
        self.history.append(content)
        self.current_index = len(self.history) - 1

    async def navigate(self, index: int) -> Optional[str]:
        """Navigate to a specific index in history."""
        if 0 <= index < len(self.history):
            self.current_index = index
            return self.history[index]
        return None

    @property
    def current_content(self) -> Optional[str]:
        """Get the current generation content."""
        if self.history and 0 <= self.current_index < len(self.history):
            return self.history[self.current_index]
        return None

class GenerationManager:
    def __init__(self):
        self.contexts: Dict[int, GenerationContext] = {}  # message_id -> context
        self.user_contexts: Dict[int, list[int]] = {}    # user_id -> [message_ids]
        self.guild_contexts: Dict[int, list[int]] = {}   # guild_id -> [message_ids]

    async def create_context(self, owner_id: int, guild_id: int, **parameters) -> GenerationContext:
        """Create a new generation context."""
        context = GenerationContext(owner_id, guild_id, **parameters)
        
        # Initialize tracking lists if needed
        if owner_id not in self.user_contexts:
            self.user_contexts[owner_id] = []
        if guild_id not in self.guild_contexts:
            self.guild_contexts[guild_id] = []
            
        return context

    async def register_message(self, context: GenerationContext, message_id: int):
        """Register a webhook message with a context."""
        context.message_id = message_id
        self.contexts[message_id] = context
        self.user_contexts[context.owner_id].append(message_id)
        self.guild_contexts[context.guild_id].append(message_id)

    async def get_context(self, message_id: int) -> Optional[GenerationContext]:
        """Get the context for a message."""
        return self.contexts.get(message_id)

    async def remove_context(self, message_id: int):
        """Remove a context when its message is deleted."""
        if context := self.contexts.get(message_id):
            self.contexts.pop(message_id)
            self.user_contexts[context.owner_id].remove(message_id)
            self.guild_contexts[context.guild_id].remove(message_id)
