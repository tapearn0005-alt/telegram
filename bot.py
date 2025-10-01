import os
import re
import logging
import requests
import asyncio
from telegram import Bot
from telegram.error import TelegramError

# --- Configuration ---
# It's recommended to use environment variables for sensitive data.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SOURCE_CHAT_ID = os.environ.get("SOURCE_CHAT_ID") # e.g., -1001234567890
TARGET_CHAT_ID = os.environ.get("TARGET_CHAT_ID") # e.g., -1009876543210
AFFILIATE_API_ENDPOINT = os.environ.get("AFFILIATE_API_ENDPOINT") # Your affiliate API endpoint
PROCESSED_IDS_FILE = 'processed_message_ids.txt'

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Message Parsing Regex ---
# This regex is designed to be robust and capture the required fields.
message_regex = re.compile(
    r"📦\s*Product:\s*(?P<product_name>.+?)\s*(?:\n💡|\n💰)"
    r".*?"
    r"💰\s*Price:\s*₹(?P<before_price>[\d,]+)\s*→\s*₹(?P<after_price>[\d,]+)"
    r".*?"
    r"Link:\s*(?P<link>https?://\S+)",
    re.DOTALL | re.IGNORECASE
)

def load_processed_ids():
    """Loads processed message IDs from a file into a set for fast lookups."""
    try:
        with open(PROCESSED_IDS_FILE, 'r') as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()

def save_processed_id(message_id):
    """Appends a new processed message ID to the file."""
    with open(PROCESSED_IDS_FILE, 'a') as f:
        f.write(str(message_id) + '\n')

def get_affiliate_link(original_link: str) -> str:
    """
    Calls the affiliate API to convert the original link.
    Returns the original link if the API call fails.
    """
    if not AFFILIATE_API_ENDPOINT:
        logger.warning("AFFILIATE_API_ENDPOINT is not set. Returning original link.")
        return original_link

    try:
        # Example: The API expects a payload like {'url': 'http://...'}
        payload = {'url': original_link}
        response = requests.post(AFFILIATE_API_ENDPOINT, json=payload, timeout=10)
        response.raise_for_status() # Raises an HTTPError for bad responses (4xx or 5xx)

        # Assuming the API returns JSON with a key 'affiliate_link'
        data = response.json()
        if 'affiliate_link' in data:
            logger.info(f"Successfully generated affiliate link for {original_link}")
            return data['affiliate_link']
        else:
            logger.error(f"Affiliate API response missing 'affiliate_link' key for {original_link}")
            return original_link
    except requests.RequestException as e:
        logger.error(f"Affiliate API request failed for {original_link}: {e}")
        return original_link # Fallback to original link

async def process_message(bot: Bot, message):
    """Processes a single message, parses it, and forwards the formatted version."""
    message_id = str(message.message_id)
    message_text = message.text

    if not message_text:
        return

    processed_ids = load_processed_ids()
    if message_id in processed_ids:
        logger.info(f"Skipping already processed message ID: {message_id}")
        return

    match = message_regex.search(message_text)
    if not match:
        logger.warning(f"Message ID {message_id} did not match regex pattern.")
        return

    # Extract data using regex named groups
    product_name = match.group('product_name').strip()
    before_price = match.group('before_price').strip()
    after_price = match.group('after_price').strip()
    original_link = match.group('link').strip()

    logger.info(f"Extracted from message {message_id}: {product_name}")

    # Generate Affiliate Link
    affiliate_link = get_affiliate_link(original_link)

    # Format the final message
    final_message = (
        f"📦 **Product:** {product_name}\n"
        f"💰 **Price:** ₹{before_price} → ₹{after_price}\n"
        f"🔗 **Affiliate Link:** [Buy Now]({affiliate_link})"
    )

    # Send the message to the target group
    try:
        await bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text=final_message,
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
        logger.info(f"Successfully forwarded message for '{product_name}' to {TARGET_CHAT_ID}")
        save_processed_id(message_id)
    except TelegramError as e:
        logger.error(f"Failed to send message to {TARGET_CHAT_ID}: {e}")

async def main():
    """Main function to initialize the bot and check for new messages."""
    if not all([TELEGRAM_BOT_TOKEN, SOURCE_CHAT_ID, TARGET_CHAT_ID]):
        logger.critical("Missing one or more required environment variables. Exiting.")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    processed_ids = load_processed_ids()

    try:
        # Get the last 50 messages from the source chat
        # You can adjust the limit based on how frequently your bot runs
        updates = await bot.get_updates(offset=-50, timeout=10)

        # In a real channel, you might get updates from getChatHistory instead
        # For simplicity, get_updates works well for recent messages
        # Note: This is a simple polling mechanism suitable for a cron job.
        # It doesn't use webhooks.

        # We'll simulate fetching history by looking at recent updates.
        # A more robust approach might involve `get_chat_history`.
        # However, `get_updates` is often sufficient and simpler for this use case.
        # Let's consider a channel post as a 'message' in an update.
        
        # This part requires a more complex setup to truly get chat history,
        # as bots can't easily read arbitrary history in channels they don't own.
        # Assuming the bot is an admin with rights to read messages.
        # A common pattern is to use a user bot or have messages forwarded to the bot.
        # For this script, we'll assume the bot is in a group and can see messages.
        
        logger.info(f"Found {len(updates)} recent updates to check.")

        for update in updates:
            message = update.channel_post or update.message
            if message and str(message.chat_id) == str(SOURCE_CHAT_ID):
                 await process_message(bot, message)

    except TelegramError as e:
        logger.error(f"Error fetching updates from Telegram: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())
