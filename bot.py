import os
import requests
import time
import threading
from flask import Flask
import telegram
import json
import re
import random
import math
import traceback

# ==============================================================================
# --- MAIN CONFIGURATION ---
# ==============================================================================

# --- General Bot Settings ---
RAPIDAPI_KEYS_STR = os.environ.get('RAPIDAPI_KEYS', '')
RAPIDAPI_HOST = "real-time-amazon-data.p.rapidapi.com"
EARNKARO_API_TOKEN = os.environ.get('EARNKARO_API_TOKEN')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
COUNTRY = os.environ.get('COUNTRY', 'IN')
POSTED_DEALS_FILE = 'posted_deals.txt'
CHECK_INTERVAL_SECONDS = 1
POSTING_WINDOW_SECONDS = 3600

# ==============================================================================
# --- DEAL QUALITY FILTERS ---
# ==============================================================================
MINIMUM_DISCOUNT_PERCENT = 10
MINIMUM_STAR_RATING = 0.0
KEYWORD_BLACKLIST = [
    "egg", "eggs", "vegetable", "paneer", "cauliflower", "marigold",
    "banana", "gourd", "brinjal", "butter", "farm fresh", "pantry"
]

# --- Category Filtering ---
SPECIFIC_CATEGORIES_TO_FETCH = [
    {"name": "Fashion", "id": "2478868012"},
    {"name": "Home_Kitchen", "id": "976442031"}, # Use underscores for hashtags
    {"name": "Electronics", "id": "1389432031"},
    {"name": "Health_PC", "id": "1389436031"},
    {"name": "Computers", "id": "1389433031"},
    {"name": "Mobiles", "id": "1389437031"}
]

# --- System Globals ---
posted_product_ids = set()
app = Flask('')
API_KEYS = []
current_api_key_index = 0

# ==============================================================================
# --- HELPER & PARSER FUNCTIONS ---
# ==============================================================================

def initialize_api_keys():
    global API_KEYS
    if RAPIDAPI_KEYS_STR:
        API_KEYS = [key.strip() for key in RAPIDAPI_KEYS_STR.split(',') if key.strip()]
    if API_KEYS:
        print(f"[*] Successfully loaded {len(API_KEYS)} API Key(s).")
    else:
        print("!!! CRITICAL WARNING: No RapidAPI keys found.")

def apply_filters(product_data, category_name):
    title = product_data.get('product_title')
    if not title: return None
    title_lower = title.lower()
    for word in KEYWORD_BLACKLIST:
        if word in title_lower:
            print(f"[FILTERED] Skipping '{title[:40]}...' (Blacklisted: '{word}')")
            return None
    try:
        star_rating_str = product_data.get('product_star_rating', '0')
        star_rating = float(star_rating_str) if star_rating_str else 0
        if star_rating < MINIMUM_STAR_RATING:
            print(f"[FILTERED] Skipping '{title[:40]}...' (Rating {star_rating} < {MINIMUM_STAR_RATING})")
            return None
    except (ValueError, TypeError): star_rating = 0.0
    deal_price_str = product_data.get('product_price')
    original_price_str = product_data.get('product_original_price')
    try:
        deal_price = float(re.sub(r'[^\d.]', '', deal_price_str)) if deal_price_str else 0
        original_price = float(re.sub(r'[^\d.]', '', original_price_str)) if original_price_str else 0
        if not original_price or not deal_price or deal_price >= original_price: return None
        discount = round(((original_price - deal_price) / original_price) * 100)
        if discount < MINIMUM_DISCOUNT_PERCENT:
            print(f"[FILTERED] Skipping '{title[:40]}...' (Discount {discount}% < {MINIMUM_DISCOUNT_PERCENT}%)")
            return None
    except (ValueError, AttributeError): return None
    image_url = product_data.get('product_photo')
    if not all([product_data.get('asin'), image_url, product_data.get('product_url')]): return None
    return {
        'product_id': product_data.get('asin'), 'deal_title': title, 'deal_photo': image_url,
        'product_url': product_data.get('product_url'), 'deal_price': deal_price,
        'original_price': original_price, 'star_rating': star_rating, 'category_name': category_name,
        'source': 'Amazon'
    }

def parse_api_response(api_data, category_name):
    standardized_deals = []
    products_list = api_data.get('data', {}).get('products', []) or api_data.get('data', {}).get('deals', [])
    if not isinstance(products_list, list): return []
    for product in products_list:
        deal = apply_filters(product, category_name)
        if deal: standardized_deals.append(deal)
    return standardized_deals

# ==============================================================================
# --- CORE BOT ENGINE ---
# ==============================================================================

@app.route('/')
def home(): return "The Deal Bot is active and running."
def run_flask(): app.run(host='0.0.0.0', port=8080)

def load_posted_deals():
    try:
        if os.path.exists(POSTED_DEALS_FILE):
            with open(POSTED_DEALS_FILE, 'r') as f:
                posted_product_ids.update(line.strip() for line in f)
            print(f"[*] Loaded {len(posted_product_ids)} previously posted deal IDs.")
    except Exception as e:
        print(f"[ERROR] Could not load posted deals file: {e}")

def save_posted_deal(product_id):
    try:
        with open(POSTED_DEALS_FILE, 'a') as f:
            f.write(product_id + '\n')
    except Exception as e:
        print(f"[ERROR] Could not save new deal ID to file: {e}")

def escape_markdown(text):
    if not isinstance(text, str): return ''
    # This escape function is specifically for Telegram's MarkdownV2
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def make_api_request(endpoint, params):
    global current_api_key_index
    if not API_KEYS: return None
    for i in range(len(API_KEYS)):
        key_index = (current_api_key_index + i) % len(API_KEYS)
        key = API_KEYS[key_index]
        headers = {"x-rapidapi-key": key, "x-rapidapi-host": RAPIDAPI_HOST}
        url = f"https://{RAPIDAPI_HOST}{endpoint}"
        print(f"[*] Attempting API call to '{endpoint}' with Key #{key_index + 1}...")
        try:
            response = requests.get(url, headers=headers, params=params, timeout=45)
            response.raise_for_status()
            current_api_key_index = key_index
            return response.json()
        except Exception as e:
            print(f"  -> [WARNING] Key #{key_index + 1} failed: {e}. Trying next key...")
    print(f"[ERROR] All API keys failed for endpoint '{endpoint}'.")
    return None

def get_amazon_deals():
    all_deals, found_product_ids = [], set()
    print("\n--- Searching For Deals in Specific Categories ---")
    for category in SPECIFIC_CATEGORIES_TO_FETCH:
        response_data = make_api_request("/products-by-category", {"category_id": category['id'], "page": "1", "country": COUNTRY})
        if response_data:
            parsed_deals = parse_api_response(response_data, category['name'])
            print(f"  -> Found {len(parsed_deals)} valid deals in '{category['name']}'.")
            new_deals_count = 0
            for deal in parsed_deals:
                pid = deal.get('product_id')
                if pid and pid not in found_product_ids:
                    all_deals.append(deal)
                    found_product_ids.add(pid)
                    new_deals_count += 1
            if new_deals_count > 0:
                print(f"  -> Added {new_deals_count} unique new deals from this category.")
        time.sleep(5)
    print(f"\n[SUCCESS] Found a total of {len(all_deals)} unique, valid deals.")
    return all_deals

def create_affiliate_link(url):
    if not EARNKARO_API_TOKEN or not url: return url
    try:
        response = requests.post("https://ekaro-api.affiliaters.in/api/converter/public", 
            headers={'Authorization': f'Bearer {EARNKARO_API_TOKEN}', 'Content-Type': 'application/json'},
            data=json.dumps({"deal": url, "convert_option": "convert_only"}), timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("success") == 1 and "data" in data:
            return "https" + data["data"].split("https", 1)[1].split(" ", 1)[0]
    except Exception: pass
    return url

def get_star_emojis(rating):
    if not rating or rating <= 0: return ""
    full_stars = math.floor(rating)
    half_star = "☆" if (rating - full_stars) >= 0.25 else ""
    empty_stars = 5 - full_stars - (1 if half_star else 0)
    return f"{'⭐' * full_stars}{half_star}{'✩' * empty_stars} `({rating} Stars)`"

def post_deal_to_telegram(deal):
    try:
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        original_price, deal_price = int(deal['original_price']), int(deal['deal_price'])
        discount = round(((original_price - deal_price) / original_price) * 100)

        # We need to escape the title BEFORE using it in the caption
        product_name = escape_markdown(deal['deal_title'])
        affiliate_link = create_affiliate_link(deal['product_url'])
        rating_line = get_star_emojis(deal.get('star_rating'))

        price_line = f"💰 ~₹{original_price}~  *₹{deal_price}* `({discount}% OFF\\!)`"

        # --- THIS IS THE FIX: The hashtag line has been removed ---
        caption_parts = [
            f"🔥 *DEAL ALERT* 🔥",
            f"*{product_name}*",
            rating_line,
            # hashtag_line,  <-- REMOVED
            price_line,
            f"🛒 [Buy Now]({affiliate_link})",
            f"👉 Join @bestsshoppingdeal for more\\!"
        ]
        caption = "\n\n".join(filter(None, caption_parts))

        bot.send_photo(chat_id=TELEGRAM_CHANNEL_ID, photo=deal['deal_photo'], caption=caption, parse_mode=telegram.ParseMode.MARKDOWN_V2)
        print(f"✅ Posted: {deal['deal_title'][:50]}...")
        return True
    except Exception as e:
        print(f"❌ Failed to post deal: {deal.get('deal_title', 'Unknown')}")
        # Uncomment the line below for extremely detailed error reports if problems continue
        # traceback.print_exc() 
        return False

def main_bot_loop():
    print("Bot loop started. Initial check will run shortly...")
    while True:
        print("\n" + "="*50 + "\nRUNNING NEW DEAL CHECK CYCLE\n" + "="*50)
        all_deals = get_amazon_deals()
        new_deals = [d for d in all_deals if d.get('product_id') not in posted_product_ids]
        if not new_deals:
            print("No new valid deals found that passed all filters.")
        else:
            print(f"Found {len(new_deals)} new deals! Starting dynamic posting...")
            random.shuffle(new_deals)
            delay = max(1, POSTING_WINDOW_SECONDS / len(new_deals))
            print(f"Posting one deal every {delay:.1f}s over {POSTING_WINDOW_SECONDS/60:.1f} min.")
            for deal in new_deals:
                if post_deal_to_telegram(deal):
                    pid = deal.get('product_id')
                    posted_product_ids.add(pid)
                    save_posted_deal(pid)
                time.sleep(delay)
            print("Finished posting all new deals for this cycle.")

        print(f"\nCycle complete. Starting next check in {CHECK_INTERVAL_SECONDS} second(s).")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    initialize_api_keys()
    load_posted_deals()
    threading.Thread(target=run_flask, daemon=True).start()
    main_bot_loop()
