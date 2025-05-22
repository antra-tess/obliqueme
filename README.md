# Oblique Discord Bot

A Discord bot that uses language models to generate simulated conversations based on chat history.

## Features

- Generates contextual responses based on recent chat history
- Supports both base models and instruct models
- Multiple generation modes: self (single character) or full (all characters)
- Temperature control for response variation
- Custom character impersonation

## Model Configuration

The bot supports two model types:

### Base Models (Default)
Uses the completions API with XML-style formatting:
```
<user1> message content
<user2> another message
<assistant>
```

### Instruct Models (Prefill Mode)
Uses the chat API with prefill technique and colon formatting:
```
user1: message content
user2: another message
assistant:
```

## Configuration

Create a `.env` file with the following variables:

```env
# Discord Configuration
BOT_TOKEN=your_bot_token_here
APPLICATION_ID=your_application_id_here

# API Configuration
OPENROUTER_API_KEY=your_api_key_here

# Model Configuration
MODEL_TYPE=base  # or 'instruct'
MODEL_NAME=meta-llama/Meta-Llama-3.1-405B

# For instruct models only
INSTRUCT_SYSTEM_PROMPT=The assistant is in CLI simulation mode, and responds to the user's CLI commands only with outputs of the commands.
INSTRUCT_USER_PREFIX=<cmd>cat untitled.log</cmd>
```

## Usage

### Message Trigger
Type `obliqueme` in any message to trigger generation:
```
obliqueme [options] [seed text]
```

Options:
- `-s`: Suppress adding your name at the end
- `-m [self|full]`: Set generation mode (default: self)
- `-n [name]`: Use a custom character name
- `-p [0.1-1.0]`: Set temperature

### Slash Command
Use `/oblique` for the same functionality with a cleaner interface.

## How It Works

1. **Message Collection**: The bot reads the last 80 messages from the channel
2. **Formatting**: Messages are formatted according to the model type:
   - Base models: `<username> message content`
   - Instruct models: `username: message content`
3. **Generation**: For instruct models, the chat history is placed in the assistant message as prefill
4. **Response Processing**: The generated text is filtered based on the selected mode

## Installation

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Create a `.env` file with your configuration
4. Run the bot: `python main.py`

## Requirements

- Python 3.8+
- discord.py
- aiohttp
- python-dotenv
- beautifulsoup4 