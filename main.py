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

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_API_BASE = "https://discord.com/api/v10"

WEBHOOKS = {
    "rust": os.environ.get("WEBHOOK_RUST", ""),
    "garrysmod": os.environ.get("WEBHOOK_GMOD", ""),
    "unturned": os.environ.get("WEBHOOK_UNTURNED", ""),
    "sbox": os.environ.get("WEBHOOK_SBOX", "")
}

# ID каналов получим из вебхуков при первом запросе
CHANNEL_IDS = {}

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

SYSTEM_PROMPT = """Ты — анализатор патч-ноутов. Извлеки ВСЕ изменения из этого фрагмента патча.
Для каждого пункта укажи все технические идентификаторы в скобках: (команда: ...), (хук: ...), (префаб: ...), (метод: ...), (предмет: ...), (консольная команда: ...), (переменная: ...), (класс: ...).
Верни ТОЛЬКО JSON: {"sections":[{"emoji":"эмодзи","title":"Раздел","items":["пункт (идентификаторы)"]}]}
Если ничего нет: {"sections":[]}. Пиши на русском, не сокращай слова."""

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
            return text.strip()
    except:
        pass
    return ""

def call_groq(text_chunk):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Фрагмент патч-ноута:\n{text_chunk}"}
        ],
        "max_tokens": 1500,
        "temperature": 0.3
    }
    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            log(f"Groq error: {r.status_code} {r.text[:200]}")
            return None
        content = r.json()["choices"][0]["message"]["content"].strip()
        if not content.startswith('{'):
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                content = match.group(0)
            else:
                return []
        content = re.sub(r'^```(?:json)?\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content)
        data = json.loads(content)
        return data.get("sections", [])
    except Exception as e:
        log(f"Groq exception: {e}")
        return None

def analyze_patch_full(text):
    chunk_size = 1800
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    log(f"Разбито на {len(chunks)} частей")
    
    all_sections = []
    for i, chunk in enumerate(chunks):
        log(f"Анализ части {i+1}/{len(chunks)}...")
        sections = call_groq(chunk)
        if sections:
            all_sections.extend(sections)
        time.sleep(5)
    
    merged = {}
    for sec in all_sections:
        title = sec.get("title", "Прочее")
        if title not in merged:
            merged[title] = {"emoji": sec.get("emoji", "🔹"), "title": title, "items": []}
        merged[title]["items"].extend(sec.get("items", []))
    
    return list(merged.values())

def analyze_patch(title, raw_text, link=""):
    full_text = ""
    if link:
        full_text = fetch_full_article(link)
    if not full_text:
        full_text = clean_html(raw_text)
    if not full_text:
        return None
    
    sections = analyze_patch_full(full_text[:10000])
    if not sections:
        return {"main_emoji": "📦", "sections": [], "nothing_new": False}
    
    main_emoji = sections[0].get("emoji", "📦") if sections else "📦"
    return {"main_emoji": main_emoji, "sections": sections, "nothing_new": False}

def get_channel_id_from_webhook(webhook_url):
    """Извлекает ID канала из URL вебхука (если ещё не сохранён)"""
    if not webhook_url:
        return None
    try:
        r = requests.get(webhook_url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("channel_id")
    except:
        pass
    # Запасной вариант: парсим URL
    match = re.search(r'/webhooks/(\d+)/(\S+)', webhook_url)
    if match:
        # ID канала обычно не в самом вебхуке, но можно достать из самого вебхука через API
        pass
    return None

def message_already_posted(channel_id, link):
    """Проверяет последние 50 сообщений в канале, ищет link"""
    if not DISCORD_BOT_TOKEN or not channel_id:
        return False
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        r = requests.get(f"{DISCORD_API_BASE}/channels/{channel_id}/messages?limit=50", headers=headers, timeout=10)
        if r.status_code == 200:
            messages = r.json()
            for msg in messages:
                if link in msg.get("content", ""):
                    return True
        else:
            log(f"Discord check error: {r.status_code} {r.text[:100]}")
    except Exception as e:
        log(f"Discord check exception: {e}")
    return False

def format_message(game, title, link, raw_text):
    game_name = GAME_NAMES.get(game, game)
    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""
    
    analysis = analyze_patch(title, raw_text, link)
    if analysis is None:
        return None  # Не шлём, если вообще ошибка
    if analysis.get("nothing_new") or len(analysis.get("sections", [])) == 0:
        return []  # Пустой список = нечего отправлять
    
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

def send_to_discord(game, title, link, raw_text):
    if not WEBHOOKS.get(game):
        return
    
    # Получаем ID канала (один раз)
    if game not in CHANNEL_IDS:
        ch_id = get_channel_id_from_webhook(WEBHOOKS[game])
        if ch_id:
            CHANNEL_IDS[game] = ch_id
            log(f"Канал {game}: ID={ch_id}")
        else:
            log(f"Не удалось получить ID канала для {game}")
    
    channel_id = CHANNEL_IDS.get(game)
    
    # Проверка на дубликат
    if channel_id and message_already_posted(channel_id, link):
        log(f"ПРОПУСК (уже в канале) {game}: {title}")
        return
    
    messages = format_message(game, title, link, raw_text)
    if not messages:  # None или []
        log(f"ПРОПУСК (нет данных) {game}: {title}")
        return
    
    for msg in messages:
        payload = {"content": msg, "allowed_mentions": {"parse": []}}
        try:
            r = requests.post(WEBHOOKS[game], json=payload)
            if r.status_code == 204:
                log(f"DISCORD OK {game}: {title}")
            else:
                log(f"DISCORD error {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log(f"DISCORD error: {e}")
        time.sleep(2)

def is_old(published_parsed):
    if not published_parsed:
        return False
    try:
        pub_date = datetime.datetime(*published_parsed[:6])
        return (datetime.datetime.now() - pub_date).days > 30
    except:
        return False

def check_feeds():
    log("CHECK: начинаю проверку RSS")
    for game, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            log(f"RSS {game}: записей: {len(feed.entries)}")
            if not feed.entries:
                continue

            # Находим самую свежую новость (не старше 30 дней)
            for entry in feed.entries[:10]:
                if is_old(entry.get("published_parsed")):
                    continue
                title = entry.get("title", "Без названия")
                link = entry.get("link", "")
                raw = entry.get("summary", entry.get("description", ""))
                log(f"Найдено {game}: {title}")
                send_to_discord(game, title, link, raw)
                break  # Отправляем только одну (последнюю)
        except Exception as e:
            log(f"RSS error {game}: {e}")
    log("CHECK: завершено")

@app.route("/")
def home():
    return "Monitor running"

@app.route("/check")
def check():
    check_feeds()
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
