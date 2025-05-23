import os
import yaml
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
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
    
    # Load model configurations from YAML
    @classmethod
    def load_models_config(cls):
        """Load model configurations from models.yaml"""
        try:
            with open('models.yaml', 'r') as file:
                config = yaml.safe_load(file)
                cls._models_config = config
                return config
        except FileNotFoundError:
            print("Warning: models.yaml not found, using default configuration")
            cls._models_config = cls._get_default_config()
            return cls._models_config
        except Exception as e:
            print(f"Error loading models.yaml: {e}")
            cls._models_config = cls._get_default_config()
            return cls._models_config
    
    @classmethod
    def _get_default_config(cls):
        """Fallback configuration if YAML file is not found"""
        return {
            'models': {
                'llama_405b_base': {
                    'name': 'Llama 3.1 405B (Base)',
                    'model_id': 'meta-llama/Meta-Llama-3.1-405B',
                    'type': 'base',
                    'endpoint': 'https://api.hyperbolic.xyz/v1/completions',
                    'max_tokens': 200,
                    'quantization': ''
                }
            },
            'default_model': 'llama_405b_base',
            'bot': {
                'keyword': 'obliqueme',
                'random_string_length': 10,
                'message_history_limit': 80
            }
        }
    
    @classmethod
    def get_models(cls):
        """Get all available model configurations"""
        if not hasattr(cls, '_models_config'):
            cls.load_models_config()
        return cls._models_config.get('models', {})
    
    @classmethod
    def get_model_config(cls, model_key):
        """Get configuration for a specific model"""
        models = cls.get_models()
        return models.get(model_key)
    
    @classmethod
    def get_default_model_key(cls):
        """Get the default model key"""
        if not hasattr(cls, '_models_config'):
            cls.load_models_config()
        return cls._models_config.get('default_model', 'llama_405b_base')
    
    @classmethod
    def get_model_choices(cls):
        """Get model choices for Discord dropdowns (name, value pairs)"""
        models = cls.get_models()
        choices = []
        for key, config in models.items():
            choices.append((config['name'], key))
        return choices
    
    @classmethod
    def _load_bot_settings(cls):
        """Load bot settings from YAML into class attributes"""
        if not hasattr(cls, '_models_config'):
            cls.load_models_config()
        
        bot_config = cls._models_config.get('bot', {})
        cls.KEYWORD = bot_config.get('keyword', 'obliqueme')
        cls.RANDOM_STRING_LENGTH = bot_config.get('random_string_length', 10)
        cls.MESSAGE_HISTORY_LIMIT = bot_config.get('message_history_limit', 80)
    
    # Legacy properties for backward compatibility - these will use default model
    @classmethod
    def get_default_model_property(cls, property_name, default_value):
        """Helper method to get properties from default model"""
        default_model = cls.get_model_config(cls.get_default_model_key())
        return default_model.get(property_name, default_value) if default_model else default_value
    
    @property
    def MODEL_TYPE(cls):
        return cls.get_default_model_property('type', 'base')
    
    @property
    def MODEL_NAME(cls):
        return cls.get_default_model_property('model_id', 'meta-llama/Meta-Llama-3.1-405B')
    
    @property
    def MAX_RESPONSE_LENGTH(cls):
        return cls.get_default_model_property('max_tokens', 200)
    
    @property
    def OPENROUTER_ENDPOINT(cls):
        return cls.get_default_model_property('endpoint', 'https://api.hyperbolic.xyz/v1/completions')
    
    @property
    def MODEL_QUANTIZATION(cls):
        return cls.get_default_model_property('quantization', '')
    
    @property
    def INSTRUCT_SYSTEM_PROMPT(cls):
        return cls.get_default_model_property('system_prompt', 'The assistant is in CLI simulation mode, and responds to the user\'s CLI commands only with outputs of the commands.')
    
    @property
    def INSTRUCT_USER_PREFIX(cls):
        return cls.get_default_model_property('user_prefix', '<cmd>cat untitled.log</cmd>')
    
    @property
    def CHAT_ENDPOINT(cls):
        default_model = cls.get_model_config(cls.get_default_model_key())
        if default_model and default_model.get('type') == 'instruct':
            return default_model.get('endpoint', 'https://openrouter.ai/api/v1/chat/completions')
        return 'https://openrouter.ai/api/v1/chat/completions'

# Load the configuration on import
Config.load_models_config()
Config._load_bot_settings()

