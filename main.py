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
MODEL = "openai/gpt-oss-20b"

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

SYSTEM_PROMPT = """Ты — анализатор патч-ноутов. Извлеки ВСЕ изменения из фрагмента.
Для каждого пункта указывай ТОЛЬКО реальные технические идентификаторы, которые присутствуют в тексте.
Если не можешь найти точное имя команды/хука/префаба/метода — не пиши его. Не заполняй скобки шаблонами вида (команда: ...), (хук: ...) и т.п.
Форматы (только если есть реальные данные):
- (команда: реальная_команда)
- (хук: реальный_хук)
- (префаб: путь_к_префабу)
- (метод: ИмяКласса.ИмяМетода)
- (предмет: название_предмета)
- (консольная команда: команда)
- (переменная: имя_переменной)
- (класс: имя_класса)

Верни ТОЛЬКО JSON:
{"sections":[{"emoji":"эмодзи","title":"Раздел","items":["пункт (реальные идентификаторы, если есть)"]}]}
Если изменений нет: {"sections":[]}"""

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
        "max_tokens": 2500,
        "temperature": 0.3
    }
    try:
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            log(f"Groq error: {r.status_code} {r.text[:200]}")
            return None
        content = r.json()["choices"][0]["message"]["content"].strip()
        # Ищем JSON
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if not match:
            return []
        json_str = match.group(0)
        json_str = re.sub(r'^```(?:json)?\s*\n?', '', json_str)
        json_str = re.sub(r'\n?```\s*$', '', json_str)
        data = json.loads(json_str)
        return data.get("sections", [])
    except Exception as e:
        log(f"Groq exception: {e}")
        return None

def analyze_patch_full(text):
    chunk_size = 2000
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
    chunks = chunks[:5]  # до 10000 символов
    log(f"Чанков для анализа: {len(chunks)}")

    all_sections = []
    for i, chunk in enumerate(chunks):
        log(f"Анализ чанка {i+1}/{len(chunks)}...")
        sections = call_groq(chunk)
        if sections is not None:
            all_sections.extend(sections)
        time.sleep(15)  # пауза 15 секунд

    # Объединяем разделы с одинаковыми названиями
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

    sections = analyze_patch_full(full_text)
    if not sections:
        return {"main_emoji": "📦", "sections": [], "nothing_new": False}

    main_emoji = sections[0].get("emoji", "📦") if sections else "📦"
    return {"main_emoji": main_emoji, "sections": sections, "nothing_new": False}

def format_message(game, title, link, raw_text):
    game_name = GAME_NAMES.get(game, game)
    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""

    analysis = analyze_patch(title, raw_text, link)
    if analysis is None:
        return None
    if analysis.get("nothing_new") or len(analysis.get("sections", [])) == 0:
        return []

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
    messages = format_message(game, title, link, raw_text)
    if not messages:
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
    log("CHECK: RSS")
    for game, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                continue
            for entry in feed.entries[:10]:
                if is_old(entry.get("published_parsed")):
                    continue
                title = entry.get("title", "Без названия")
                link = entry.get("link", "")
                raw = entry.get("summary", entry.get("description", ""))
                log(f"Отправка {game}: {title}")
                send_to_discord(game, title, link, raw)
                break
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
