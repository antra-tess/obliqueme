import re

def parse_webhook_url(url):
    """
    Parses the webhook URL to extract the webhook ID and token.

    Args:
        url (str): The full webhook URL.

    Returns:
        tuple: A tuple containing the webhook ID and token.
    """
    pattern = r"https?://discord\.com/api/webhooks/(\d+)/([\w-]+)"
    match = re.match(pattern, url)
    if match:
        webhook_id, webhook_token = match.groups()
        return webhook_id, webhook_token
    else:
        raise ValueError(f"Invalid webhook URL: {url}")
