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

def fetch_article_text(url):
    """Скачивает и очищает ТОЛЬКО содержимое новости."""
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return ""
        html_text = r.text

        # Попытка вырезать основной блок контента
        content = ""
        # Facepunch
        match = re.search(r'<div[^>]*class="[^"]*post-content[^"]*"[^>]*>(.*?)</div>', html_text, re.DOTALL)
        if match:
            content = match.group(1)
        # Steam
        if not content:
            match = re.search(r'<div[^>]*class="[^"]*news_content[^"]*"[^>]*>(.*?)</div>', html_text, re.DOTALL)
            if match:
                content = match.group(1)
        # Если не нашли, берём всё тело
        if not content:
            match = re.search(r'<body[^>]*>(.*?)</body>', html_text, re.DOTALL)
            if match:
                content = match.group(1)
            else:
                content = html_text

        # Убираем скрипты, стили, теги
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<nav[^>]*>.*?</nav>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<footer[^>]*>.*?</footer>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<header[^>]*>.*?</header>', '', content, flags=re.DOTALL|re.IGNORECASE)
        content = re.sub(r'<[^>]+>', ' ', content)
        content = html.unescape(content)
        content = re.sub(r'\s+', ' ', content)
        return content.strip()
    except Exception as e:
        log(f"Fetch error: {e}")
        return ""

def extract_sections(text):
    """Разбивает текст на разделы по заголовкам и ищет технические идентификаторы."""
    # Ищем строки, похожие на заголовки (окружены ** или начинаются с #, или заканчиваются двоеточием)
    heading_pattern = re.compile(r'(?:(?:^|\n)\s*(?:\*{1,3}|\#{1,3})\s*(.+?)(?:\*{1,3})?(?:\s*\n|$)|(?:(?:^|\n)\s*([A-Za-zА-Яа-я0-9 ]+):(?:\s|$)))', re.MULTILINE)
    headings = [(m.group(1) or m.group(2)).strip() for m in heading_pattern.finditer(text)]

    # Если заголовков нет, создаём один раздел
    if not headings:
        sections = [{"title": "Общие изменения", "emoji": "📦", "text": text}]
        return sections

    # Разбиваем текст между заголовками
    sections = []
    prev_pos = 0
    for i, heading in enumerate(headings):
        start = text.find(heading, prev_pos)
        if start == -1:
            continue
        if i == 0 and start > 0:
            # Текст до первого заголовка — вступление
            intro = text[0:start].strip()
            if intro:
                sections.append({"title": "Введение", "emoji": "📄", "text": intro})
        # Конец раздела — до следующего заголовка
        end = text.find(headings[i+1], start+len(heading)) if i+1 < len(headings) else len(text)
        section_text = text[start+len(heading):end].strip()
        if section_text:
            # Подбираем эмодзи по названию заголовка
            emoji = "📦"
            hl = heading.lower()
            if any(w in hl for w in ['weapon','gun','rifle','pistol','оруж','пистолет','винтовк']):
                emoji = "🔫"
            elif any(w in hl for w in ['vehicle','helicopter','car','boat','транспорт','вертолёт','машин']):
                emoji = "🚁"
            elif any(w in hl for w in ['building','base','construction','стен','фундамент','постройк']):
                emoji = "🏗️"
            elif any(w in hl for w in ['monument','map','world','карт','монумент','биом']):
                emoji = "🗺️"
            elif any(w in hl for w in ['skin','cosmetic','скин','косметик']):
                emoji = "🎨"
            elif any(w in hl for w in ['api','hook','oxide','carbon','modding','plugin','моддинг']):
                emoji = "🧩"
            elif any(w in hl for w in ['ui','hud','menu','interface','интерфейс','меню']):
                emoji = "🖥️"
            elif any(w in hl for w in ['sound','audio','звук','аудио']):
                emoji = "🔊"
            elif any(w in hl for w in ['performance','optimization','производитель','оптимизац']):
                emoji = "⚡"
            elif any(w in hl for w in ['event','halloween','christmas','событие','хэллоуин']):
                emoji = "🎉"
            elif any(w in hl for w in ['economy','scrap','trade','vendor','экономик','торгов']):
                emoji = "💰"

            sections.append({"title": heading, "emoji": emoji, "text": section_text})
        prev_pos = end
    return sections

def find_tech_identifiers(paragraph):
    """Ищет технические идентификаторы в строке/абзаце."""
    identifiers = []

    # Префаб (путь к .prefab)
    prefab = re.search(r'(?:prefab:?\s*)?([\w/\.-]+\.prefab)', paragraph, re.IGNORECASE)
    if prefab:
        identifiers.append(f"префаб: {prefab.group(1)}")

    # Консольные команды/переменные (sv_..., convar, command)
    convar = re.search(r'(?:convar|convariable|server\.|client\.)?\b(sv_\w+)\b', paragraph, re.IGNORECASE)
    if convar:
        identifiers.append(f"консольная команда: {convar.group(1)}")

    # Хуки (On..., часто с большой буквы)
    hook = re.search(r'(?:hook:?\s*)?\b(On\w+)\b', paragraph)
    if hook:
        identifiers.append(f"хук: {hook.group(1)}")

    # Методы (Class.Method)
    method = re.search(r'(?:method:?\s*)?([A-Z]\w+\.[A-Z]\w+)', paragraph)
    if method:
        identifiers.append(f"метод: {method.group(1)}")

    # Предметы (часто в кавычках)
    item = re.search(r'(?:item:?\s*)?["\u201c]([^"\u201d]+)["\u201d]', paragraph)
    if item:
        identifiers.append(f"предмет: {item.group(1)}")

    return identifiers

def parse_patch(text):
    """Формирует структурированные разделы с пунктами."""
    sections = extract_sections(text)
    if not sections:
        return []

    result = []
    for sec in sections:
        items = []
        # Разбиваем текст секции на предложения
        sentences = re.split(r'(?<=[.!?])\s+', sec["text"])
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            # Ищем технические детали
            tech_tags = find_tech_identifiers(sent)
            if tech_tags:
                # Обрезаем слишком длинное описание
                desc = sent[:120]
                item = f"{desc} ({', '.join(tech_tags)})"
                items.append(item)
            else:
                # Если нет идентификаторов, просто добавляем описание (если оно не слишком длинное)
                if len(sent) > 30:
                    items.append(sent[:150])

        if items:
            result.append({"emoji": sec["emoji"], "title": sec["title"], "items": items})
    return result

def analyze_patch(title, raw_text, link=""):
    full_text = ""
    if link:
        full_text = fetch_article_text(link)
    if not full_text:
        full_text = clean_html(raw_text)
    if not full_text:
        return None

    sections = parse_patch(full_text)
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

def clean_html(text):
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()

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
