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

def extract_technical_details(text):
    """Парсит текст патч-ноута и собирает разделы с техническими идентификаторами."""
    sections = []

    # Шаблоны для разных типов идентификаторов
    patterns = {
        "prefab": r'(?:prefab|prefab\s*:)\s*["\`]?([^"\'\,\s\)]+\.prefab)["\`]?',
        "concommand": r'(?:concommand|convariable|convar\.server|convar\.client)\s+["\`]?(\w+)["\`]?\s*(\d[^\s\)]*)?',
        "command": r'(?:added command|new command|command\s*:)\s*["\`]?(\w+)["\`]?',
        "hook": r'(?:hook\s*:?\s*|new hook\s*:?\s*)["\`]?(\w+)["\`]?',
        "method": r'(?:method\s*:?\s*|new method\s*:?\s*)["\`]?([\w.]+)["\`]?',
        "class": r'(?:class\s*:?\s*|new class\s*:?\s*)["\`]?([\w.]+)["\`]?',
        "item": r'(?:item\s*:?\s*|new item\s*:?\s*)["\`]?([\w\s]+)["\`]?(?:\s*\((\d+)\))?',
        "variable": r'(?:variable\s*:?\s*|new var\s*:?\s*)["\`]?(\w+)["\`]?'
    }

    # Разбиваем на строки для анализа
    lines = text.split('. ')
    current_section = {"emoji": "📦", "title": "Изменения", "items": []}
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Определяем, к какой категории относится строка
        line_lower = line.lower()
        if any(w in line_lower for w in ['weapon', 'gun', 'rifle', 'pistol', 'оруж', 'пистолет', 'винтовк']):
            current_section = {"emoji": "🔫", "title": "Новое оружие", "items": []}
        elif any(w in line_lower for w in ['vehicle', 'helicopter', 'car', 'boat', 'транспорт', 'вертолёт', 'машин']):
            current_section = {"emoji": "🚁", "title": "Новый транспорт", "items": []}
        elif any(w in line_lower for w in ['building', 'base', 'construction', 'стен', 'фундамент', 'постройк']):
            current_section = {"emoji": "🏗️", "title": "Строительство и базы", "items": []}
        elif any(w in line_lower for w in ['monument', 'map', 'world', 'карт', 'монумент', 'биом']):
            current_section = {"emoji": "🗺️", "title": "Карта и монументы", "items": []}
        elif any(w in line_lower for w in ['skin', 'cosmetic', 'скин', 'косметик']):
            current_section = {"emoji": "🎨", "title": "Скины и косметика", "items": []}
        elif any(w in line_lower for w in ['api', 'hook', 'oxide', 'carbon', 'modding', 'plugin', 'моддинг']):
            current_section = {"emoji": "🧩", "title": "API и моддинг", "items": []}
        elif any(w in line_lower for w in ['ui', 'hud', 'menu', 'interface', 'интерфейс', 'меню']):
            current_section = {"emoji": "🖥️", "title": "Интерфейс", "items": []}
        elif any(w in line_lower for w in ['sound', 'audio', 'звук', 'аудио']):
            current_section = {"emoji": "🔊", "title": "Звук и аудио", "items": []}
        elif any(w in line_lower for w in ['performance', 'optimization', 'производитель', 'оптимизац']):
            current_section = {"emoji": "⚡", "title": "Оптимизация", "items": []}
        elif any(w in line_lower for w in ['event', 'halloween', 'christmas', 'событие', 'хэллоуин']):
            current_section = {"emoji": "🎉", "title": "События", "items": []}
        elif any(w in line_lower for w in ['economy', 'scrap', 'trade', 'vendor', 'экономик', 'торгов']):
            current_section = {"emoji": "💰", "title": "Экономика и торговля", "items": []}
        else:
            # Если не подошло, оставляем последнюю категорию
            pass

        # Собираем все технические идентификаторы в этой строке
        tech_tags = []
        for tag_type, pat in patterns.items():
            matches = re.findall(pat, line, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]  # первая группа
                tech_tags.append(f"{tag_type}: {match.strip()}")

        if tech_tags:
            # Формируем пункт с техническими деталями
            description = line[:150]  # краткое описание
            tech_str = ", ".join(tech_tags)
            item = f"{description} ({tech_str})"
            current_section["items"].append(item)

    # Отбрасываем пустые разделы
    sections = [sec for sec in [current_section] + [s for s in sections if s["items"]]]
    return sections

def analyze_patch(title, raw_text, link=""):
    full_text = ""
    if link:
        full_text = fetch_full_article(link)
    if not full_text:
        full_text = clean_html(raw_text)
    if not full_text:
        return None

    sections = extract_technical_details(full_text)
    if not sections:
        return {"main_emoji": "📦", "sections": [], "nothing_new": True}

    main_emoji = sections[0]["emoji"]
    return {"main_emoji": main_emoji, "sections": sections, "nothing_new": False}

def format_message(game, title, link, raw_text):
    game_name = GAME_NAMES.get(game, game)
    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""

    analysis = analyze_patch(title, raw_text, link)
    if analysis is None or analysis.get("nothing_new"):
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
