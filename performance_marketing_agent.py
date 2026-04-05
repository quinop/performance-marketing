import feedparser
import requests
import logging
import os
import traceback
import time
from datetime import datetime
from google import genai
from google.genai import types

# --- 1. CONFIGURATION ---
GEMINI_KEY = os.environ.get('GEMINI_API_KEY')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL') 

# Using v1beta for access to 2026 models like 3.1-flash-lite
client = genai.Client(api_key=GEMINI_KEY, http_options={'api_version': 'v1beta'})
MEMORY_FILE = "sent_urls.txt"

# --- 2. SOURCES (Adjusted for Marketing) ---
AD_PLATFORM_SOURCES = {
    "Google Ads Blog": "https://blog.google/products/ads-commerce/rss/",
    "Search Engine Land (PPC)": "https://searchengineland.com/library/ppc/feed",
    "Social Media Today": "https://www.socialmediatoday.com/feeds/news/",
    "Marketing Dive": "https://www.marketingdive.com/feeds/news/"
}

TOOL_ASO_SOURCES = {
    "AppTweak Blog": "https://www.apptweak.com/en/aso-blog/feed",
    "Sensor Tower Blog": "https://sensortower.com/blog/rss",
    "Business of Apps": "https://www.businessofapps.com/feed/",
    "MobileDevMemo": "https://mobiledevmemo.com/feed/"
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- 3. UTILITIES ---
def load_sent_urls():
    if not os.path.exists(MEMORY_FILE): return set()
    try:
        with open(MEMORY_FILE, "r") as f: 
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        logging.error(f"Failed to read memory: {e}")
        return set()

def save_sent_urls(urls):
    if not urls: return
    try:
        existing = load_sent_urls()
        combined = list(urls) + [u for u in existing if u not in urls]
        with open(MEMORY_FILE, "w") as f:
            for url in combined[:1000]: 
                f.write(f"{url}\n")
    except Exception as e:
        logging.error(f"Failed to save memory: {e}")

def fetch_data(source_dict):
    sent_urls = load_sent_urls()
    items, new_urls = [], []
    for name, url in source_dict.items():
        try:
            resp = requests.get(url, timeout=15)
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:8]:
                if entry.link not in sent_urls:
                    summary_text = entry.get('summary', '')[:300].replace('\n', ' ')
                    items.append({"source": name, "title": entry.title, "link": entry.link, "desc": summary_text})
                    new_urls.append(entry.link)
        except Exception as e: 
            logging.error(f"Fetch failed for {name}: {e}")
    return items, new_urls

# --- 4. ROBUST SUMMARIZATION (Consolidated to avoid 429s) ---
def get_summary_safe(items, instruction):
    if not items: return None
    
    # Merge instruction into prompt to solve the 'systemInstruction' 400 error
    text_blob = "\n".join([f"- [{i['source']}] {i['title']}: {i['desc']}" for i in items])
    full_prompt = f"{instruction}\n\nContext:\n{text_blob}"
    
    # Updated 2026 Model List
    models_to_try = ["gemini-2.5-flash", "gemini-3.1-flash-lite-preview"]
    
    for model_id in models_to_try:
        for attempt in range(2): 
            try:
                response = client.models.generate_content(
                    model=model_id,
                    contents=full_prompt
                )
                logging.info(f"✅ Success with {model_id}")
                return response.text
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str:
                    logging.warning(f"🛑 Quota hit for {model_id}. Trying next model.")
                    break 
                elif "404" in err_str:
                    logging.warning(f"🔍 {model_id} not found. Trying next.")
                    break
                elif "503" in err_str:
                    time.sleep(10)
                else:
                    logging.error(f"❌ Error: {e}")
                    break 
    return None

def send_to_discord(message):
    if not DISCORD_WEBHOOK_URL: return
    payload = {
        "content": message[:1990],
        "username": "Intelligence Bot",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/1998/1998087.png"
    }
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Discord failed: {e}")

# --- 5. EXECUTION ---
def run_agent():
    logging.info("Scanning for marketing updates...")
    
    ad_items, ad_urls = fetch_data(AD_PLATFORM_SOURCES)
    tool_items, tool_urls = fetch_data(TOOL_ASO_SOURCES)

    if not ad_items and not tool_items:
        logging.info("No new news found.")
        return

    # Using the HK structure: separate summaries, then combine for the report
    ad_summary = get_summary_safe(
        ad_items, 
        "Summarize top 4 Ad Platform updates. Format: **Platform** (Source): One sentence."
    )

    tool_summary = get_summary_safe(
        tool_items, 
        "Summarize top 3 ASO/Tool updates. Format: **Tool** (Source): One sentence."
    )

    if ad_summary or tool_summary:
        final_msg = f"# 🚀 PERFORMANCE MARKETING DAILY\n*Date: {datetime.now().strftime('%Y-%m-%d')}*\n\n"
        if ad_summary:
            final_msg += f"## 📡 AD PLATFORMS\n{ad_summary}\n\n"
        if tool_summary:
            final_msg += f"## 🛠️ TOOLS & MARKET\n{tool_summary}\n"

        send_to_discord(final_msg)
        save_sent_urls(ad_urls + tool_urls)
    else:
        logging.error("AI failed to generate summaries.")

if __name__ == "__main__":
    try:
        run_agent()
    except Exception:
        logging.error(traceback.format_exc())
