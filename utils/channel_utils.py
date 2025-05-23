"""
Channel and thread utilities for Discord bot.
"""

import discord


def is_thread_channel(channel):
    """
    Check if a channel is a thread.
    
    Args:
        channel: The channel object to check
        
    Returns:
        bool: True if the channel is a thread, False otherwise
    """
    if not channel:
        return False
    return hasattr(channel, 'parent_id') and channel.parent_id is not None


def get_parent_channel(bot, channel):
    """
    Get the parent channel of a thread, or the channel itself if not a thread.
    
    Args:
        bot: The bot instance
        channel: The channel object
        
    Returns:
        discord.TextChannel: The parent channel or the channel itself
    """
    if is_thread_channel(channel):
        return bot.get_channel(channel.parent_id)
    return channel


def get_effective_channel_id(channel):
    """
    Get the effective channel ID for webhook operations.
    For threads, returns the thread ID. For regular channels, returns the channel ID.
    
    Args:
        channel: The channel object
        
    Returns:
        int: The channel ID to use for webhook operations
    """
    return channel.id if channel else None


def format_channel_info(channel):
    """
    Format channel information for logging.
    
    Args:
        channel: The channel object
        
    Returns:
        str: Formatted channel information
    """
    if not channel:
        return "Unknown channel"
    
    if is_thread_channel(channel):
        return f"Thread '{channel.name}' (ID: {channel.id}) in channel {channel.parent_id}"
    else:
        return f"Channel '{channel.name}' (ID: {channel.id})" 