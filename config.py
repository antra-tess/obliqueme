import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    APPLICATION_ID = os.getenv('APPLICATION_ID')
    WEBHOOK_URLS = {
        # Add more webhooks as needed
        # 'alerts': os.getenv('WEBHOOK_ALERTS'),
    }
    KEYWORD = 'obliqueme'
    RANDOM_STRING_LENGTH = 10
    MESSAGE_HISTORY_LIMIT = 200
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
    MAX_RESPONSE_LENGTH = 600
    OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/completions"
    #OPENROUTER_ENDPOINT = "https://api.hyperbolic.xyz/v1/completions"
    
    # Model configuration
    MODEL_TYPE = os.getenv('MODEL_TYPE', 'base')  # 'base' or 'instruct'
    MODEL_NAME = os.getenv('MODEL_NAME', 'meta-llama/Meta-Llama-3.1-405B')
    
    # Optional provider settings
    MODEL_QUANTIZATION = os.getenv('MODEL_QUANTIZATION', '')  # e.g., 'bf16' - leave empty for no quantization
    
    # Instruct mode settings (used when MODEL_TYPE is 'instruct')
    INSTRUCT_SYSTEM_PROMPT = os.getenv('INSTRUCT_SYSTEM_PROMPT', 
        'The assistant is in CLI simulation mode, and responds to the user\'s CLI commands only with outputs of the commands.')
    INSTRUCT_USER_PREFIX = os.getenv('INSTRUCT_USER_PREFIX', '<cmd>cat untitled.log</cmd>')
    #CHAT_ENDPOINT = os.getenv('CHAT_ENDPOINT', 'https://api.hyperbolic.xyz/v1/chat/completions')
    CHAT_ENDPOINT = os.getenv('CHAT_ENDPOINT', 'https://openrouter.ai/api/v1/chat/completions')

