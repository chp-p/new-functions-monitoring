import os
import sys
import time
import datetime
import requests
import re
import html
import json
import feedparser
from flask import Flask

app = Flask(__name__)

TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY", "")
TOGETHER_URL = "https://api.together.xyz/v1/chat/completions"
MODEL = "deepseek-ai/DeepSeek-V3"

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

SYSTEM_PROMPT = """Ты — анализатор патч-ноутов. Извлеки ВСЕ изменения из полного текста.
Для каждого пункта укажи реальные технические идентификаторы в скобках (только если они есть в тексте).
Форматы:
- (команда: точная_команда)
- (хук: точный_хук)
- (префаб: полный_путь_к_префабу)
- (метод: Класс.Метод)
- (предмет: название_предмета)
- (консольная команда: команда)
- (переменная: имя_переменной)
- (класс: имя_класса)

Не выдумывай идентификаторы! Если в тексте нет точного имени, не пиши его.
Пример хорошего вывода:
"Добавлен новый монумент Apartment Complex (префаб: assets/bundled/prefabs/autospawn/monument/apartment_complex.prefab, команда: rentroom)"
"Ускоренная прогрессия (консольная команда: sv_accelerated_progression 2, переменная: ConVar.Server.accelerated_progression)"

Верни ТОЛЬКО JSON:
{"main_emoji":"эмодзи","sections":[{"emoji":"эмодзи","title":"Раздел","items":["пункт (идентификаторы)"]}],"nothing_new":false}
Если изменений нет: {"nothing_new":true,"reason":"причина"}"""

def fetch_full_article(url):
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return ""
        text = r.text
        content = ""
        for tag in ['post-content', 'news_content', 'announcement_content']:
            match = re.search(rf'<div[^>]*class="[^"]*{tag}[^"]*"[^>]*>(.*?)</div>', text, re.DOTALL)
            if match:
                content = match.group(1)
                break
        if not content:
            match = re.search(r'<body[^>]*>(.*?)</body>', text, re.DOTALL)
            content = match.group(1) if match else text
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<nav[^>]*>.*?</nav>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<footer[^>]*>.*?</footer>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<header[^>]*>.*?</header>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<[^>]+>', ' ', content)
        content = html.unescape(content)
        content = re.sub(r'\s+', ' ', content).strip()
        return content[:15000]
    except Exception as e:
        log(f"Fetch error: {e}")
        return ""

def analyze_with_together(full_text):
    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Полный патч-ноут:\n{full_text}"}
        ],
        "max_tokens": 6000,
        "temperature": 0.2
    }
    try:
        r = requests.post(TOGETHER_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            log(f"Together error: {r.status_code} {r.text[:200]}")
            return None
        response_text = r.json()["choices"][0]["message"]["content"]
        # Извлекаем JSON
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not match:
            log("No JSON in response")
            return None
        json_str = match.group(0)
        json_str = re.sub(r'^```(?:json)?\s*\n?', '', json_str)
        json_str = re.sub(r'\n?```\s*$', '', json_str)
        return json.loads(json_str)
    except Exception as e:
        log(f"Together exception: {e}")
        return None

def analyze_patch(title, raw_text, link=""):
    text = fetch_full_article(link) if link else raw_text
    if not text:
        return None
    return analyze_with_together(text)

def format_message(game, title, link, raw_text):
    game_name = GAME_NAMES.get(game, game)
    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""

    analysis = analyze_patch(title, raw_text, link)
    if analysis is None:
        return None
    if analysis.get("nothing_new"):
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
