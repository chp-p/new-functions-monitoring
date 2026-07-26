import os
import sys
import time
import datetime
import requests
import hashlib
import re
import html
import json
import feedparser
from flask import Flask

app = Flask(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

WEBHOOKS = {
    "rust": os.environ.get("WEBHOOK_RUST", ""),
    "garrysmod": os.environ.get("WEBHOOK_GMOD", ""),
    "unturned": os.environ.get("WEBHOOK_UNTURNED", ""),
    "sbox": os.environ.get("WEBHOOK_SBOX", "")
}

RSS_FEEDS = {
    "rust": "https://rust.facepunch.com/rss",
    "garrysmod": "https://store.steampowered.com/feeds/news/app/4000/",
    "unturned": "https://store.steampowered.com/feeds/news/app/304930/",
    "sbox": "https://sbox.facepunch.com/news/rss"
}

GAME_NAMES = {
    "rust": "Rust",
    "garrysmod": "Garry's Mod",
    "unturned": "Unturned",
    "sbox": "s&box"
}

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

SYSTEM_PROMPT = """Ты — анализатор патч-ноутов для игр. Выдели ВСЕ изменения, НЕ СОКРАЩАЙ.
Для каждого пункта укажи ВСЕ технические идентификаторы в скобках через запятую:
(команда: ...), (хук: ...), (префаб: ...), (метод: ...), (предмет: ...), (консольная команда: ...), (переменная: ...), (класс: ...)
Верни ТОЛЬКО JSON: {"main_emoji":"эмодзи","sections":[{"emoji":"эмодзи","title":"Раздел","items":["пункт (идентификаторы)"]}],"nothing_new":false}
Если нет изменений: {"nothing_new":true,"reason":"причина"}. Пиши на русском, полно, не пропускай."""

def clean_html(text):
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()

def fetch_full_article(url):
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            text = re.sub(r'<script[^>]*>.*?</script>', '', r.text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()[:3000]  # Ограничиваем ввод
    except:
        pass
    return ""

def analyze_patch(title, raw_text, link=""):
    text = ""
    if link:
        text = fetch_full_article(link)
    if not text:
        text = clean_html(raw_text)[:3000]

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Заголовок: {title}\n\n{text}"}
        ],
        "max_tokens": 2000,
        "temperature": 0.3
    }
    
    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            log(f"AI: ошибка {r.status_code} - {r.text[:200]}")
            return None
        response_text = r.json()["choices"][0]["message"]["content"]
        response_text = response_text.strip()
        if not response_text.startswith('{'):
            return {"main_emoji": "📦", "sections": [{"emoji": "📋", "title": "Изменения", "items": [response_text[:1800]]}], "nothing_new": False}
        response_text = re.sub(r'^```(?:json)?\s*\n?', '', response_text)
        response_text = re.sub(r'\n?```\s*$', '', response_text)
        return json.loads(response_text)
    except Exception as e:
        log(f"AI error: {e}")
        return None

def format_message(game, title, link, raw_text):
    game_name = GAME_NAMES.get(game, game)
    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""
    
    analysis = analyze_patch(title, raw_text, link)
    
    if analysis is None:
        return [f"## 📦 Обновление: **{title}**\n\n⚠️ *Не удалось проанализировать патч.*\n\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"]
    if analysis.get("nothing_new"):
        return [f"## ℹ️ Обновление: **{title}**\n\n*{analysis.get('reason', 'Без изменений.')}*\n\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"]
    
    main_emoji = analysis.get("main_emoji", "📦")
    title_line = f"{main_emoji} Обновление: **{title}**"
    if version:
        title_line += f" — `{version}`"
    
    messages = []
    current = f"## {title_line}\n"
    part = 1
    
    for section in analysis.get("sections", []):
        emoji = section.get("emoji", "🔹")
        section_title = section.get("title", "Изменения")
        items = section.get("items", [])
        block = f"\n### {emoji} {section_title}:\n"
        for item in items[:10]:
            block += f"🔹 {item}\n"
        if len(current + block) > 1900:
            current += f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name} (часть {part})"
            messages.append(current)
            part += 1
            current = f"## {title_line} (часть {part})\n"
        current += block
    
    current += f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"
    if part > 1:
        current += f" (часть {part})"
    messages.append(current)
    return messages

seen_entries = {}

def get_entry_hash(entry):
    return hashlib.md5((entry.get("title","")+entry.get("link","")).encode()).hexdigest()

def is_old(published_parsed):
    if not published_parsed:
        return False
    try:
        pub_date = datetime.datetime(*published_parsed[:6])
        return (datetime.datetime.now() - pub_date).days > 30
    except:
        return False

def send_to_discord(game, title, link, raw_text):
    if not WEBHOOKS.get(game):
        return
    log(f"DISCORD: {game}...")
    time.sleep(15)
    messages = format_message(game, title, link, raw_text)
    for msg in messages:
        try:
            r = requests.post(WEBHOOKS[game], json={"content": msg, "allowed_mentions": {"parse": []}})
            if r.status_code == 204:
                log(f"DISCORD OK {game}: {title}")
            else:
                log(f"DISCORD error {r.status_code}")
        except Exception as e:
            log(f"DISCORD error: {e}")
        time.sleep(2)

def check_feeds():
    log("CHECK: RSS")
    for game, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                continue
            if game not in seen_entries:
                seen_entries[game] = set()
                for entry in feed.entries[:10]:
                    if is_old(entry.get("published_parsed")):
                        continue
                    h = get_entry_hash(entry)
                    seen_entries[game].add(h)
                    send_to_discord(game, entry.get("title",""), entry.get("link",""), entry.get("summary",""))
                    break
                else:
                    entry = feed.entries[0]
                    seen_entries[game].add(get_entry_hash(entry))
                    send_to_discord(game, entry.get("title",""), entry.get("link",""), entry.get("summary",""))
            else:
                for entry in feed.entries[:5]:
                    h = get_entry_hash(entry)
                    if h not in seen_entries[game]:
                        seen_entries[game].add(h)
                        send_to_discord(game, entry.get("title",""), entry.get("link",""), entry.get("summary",""))
        except Exception as e:
            log(f"RSS error {game}: {e}")

@app.route("/")
def home():
    return "Monitor running"

@app.route("/check")
def check():
    check_feeds()
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
