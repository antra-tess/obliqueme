import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    WEBHOOK_URLS = {
        'default': os.getenv('WEBHOOK_DEFAULT'),
        # Add more webhooks as needed, it will rotate through them randomly
        # 'alerts': os.getenv('WEBHOOK_ALERTS'),
    }
    KEYWORD = 'obliqueme'
    RANDOM_STRING_LENGTH = 10
    MESSAGE_HISTORY_LIMIT = 50
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
    MAX_RESPONSE_LENGTH = 200
    OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/completions"


