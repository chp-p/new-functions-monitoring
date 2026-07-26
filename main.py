import os
import sys
import requests
import hashlib
import re
import html
import json
import feedparser
from flask import Flask

app = Flask(__name__)

# ====== НАСТРОЙКИ (все через Render Environment) ======

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# Вебхуки тоже из переменных окружения
WEBHOOKS = {
    "rust": os.environ.get("WEBHOOK_RUST", ""),
    "garrysmod": os.environ.get("WEBHOOK_GMOD", ""),
    "unturned": os.environ.get("WEBHOOK_UNTURNED", ""),
    "sbox": os.environ.get("WEBHOOK_SBOX", "")
}

RSS_FEEDS = {
    "rust": "https://rust.facepunch.com/rss",
    "garrysmod": "https://steamcommunity.com/app/4000/rss/",
    "unturned": "https://steamcommunity.com/app/304930/rss/",
    "sbox": "https://sbox.facepunch.com/rss/"
}

GAME_NAMES = {
    "rust": "Rust",
    "garrysmod": "Garry's Mod",
    "unturned": "Unturned",
    "sbox": "s&box"
}

# ====== ЛОГИ ======

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()


# ====== AI АНАЛИЗ ПАТЧА ======

SYSTEM_PROMPT = """Ты — анализатор патч-ноутов для игр (Rust, Garry's Mod, Unturned, s&box).
Выдели ТОЛЬКО новые функции и изменения геймплея.

Верни ТОЛЬКО JSON:

{
  "main_emoji": "эмодзи",
  "sections": [
    {
      "emoji": "эмодзи",
      "title": "Название раздела",
      "items": ["пункт 1", "пункт 2"]
    }
  ],
  "nothing_new": false
}

Если нет новых функций — {"nothing_new": true, "reason": "причина"}.
Пиши на русском. Создавай разделы только под то, что есть в патче."""


def clean_html(text):
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()


def analyze_patch(title, raw_text):
    text = clean_html(raw_text)
    if len(text) > 8000:
        text = text[:8000] + "..."

    payload = {
        "contents": [{
            "parts": [
                {"text": SYSTEM_PROMPT},
                {"text": f"Заголовок: {title}\n\nПатч-ноут:\n{text}"}
            ]
        }],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 1500
        }
    }

    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"

    try:
        log("AI: запрос к Gemini...")
        r = requests.post(url, json=payload, timeout=30)
        log(f"AI: статус {r.status_code}")

        if r.status_code != 200:
            log(f"AI: ошибка {r.status_code} - {r.text[:300]}")
            return None

        data = r.json()
        response_text = data["candidates"][0]["content"]["parts"][0]["text"]
        log(f"AI: ответ ({len(response_text)} символов)")

        # Чистим markdown
        response_text = re.sub(r'^```(?:json)?\s*\n?', '', response_text)
        response_text = re.sub(r'\n?```\s*$', '', response_text)

        result = json.loads(response_text)
        log(f"AI: секций: {len(result.get('sections', []))}")
        return result

    except Exception as e:
        log(f"AI error: {e}")
        return None


def format_message(game, title, link, raw_text):
    game_name = GAME_NAMES.get(game, game)

    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""

    analysis = analyze_patch(title, raw_text)

    if analysis is None:
        return (
            f"## 📦 Обновление: **{title}**\n\n"
            f"⚠️ *Не удалось проанализировать патч.*\n\n"
            f"📎 [Патч-ноут]({link}) | 🎮 {game_name}"
        )

    if analysis.get("nothing_new"):
        return (
            f"## ℹ️ Обновление: **{title}**\n\n"
            f"*{analysis.get('reason', 'Без значительных изменений.')}*\n\n"
            f"📎 [Патч-ноут]({link}) | 🎮 {game_name}"
        )

    main_emoji = analysis.get("main_emoji", "📦")
    title_line = f"{main_emoji} Обновление: **{title}**"
    if version:
        title_line += f" — `{version}`"

    message = f"## {title_line}\n"

    for section in analysis.get("sections", []):
        emoji = section.get("emoji", "🔹")
        section_title = section.get("title", "Изменения")
        items = section.get("items", [])
        message += f"\n### {emoji} {section_title}:\n"
        for item in items[:8]:
            message += f"🔹 {item}\n"

    message += f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"

    if len(message) > 1950:
        message = message[:1920] + "\n\n...\n📎 " + link

    return message


# ====== ОСНОВНАЯ ЛОГИКА ======

seen_entries = {}


def get_entry_hash(entry):
    content = entry.get("title", "") + entry.get("link", "")
    return hashlib.md5(content.encode()).hexdigest()


def send_to_discord(game, title, link, raw_text):
    if game not in WEBHOOKS or not WEBHOOKS[game]:
        log(f"DISCORD: нет вебхука для {game}")
        return

    log(f"DISCORD: отправка в {game}...")
    content = format_message(game, title, link, raw_text)
    payload = {"content": content, "allowed_mentions": {"parse": []}}

    try:
        r = requests.post(WEBHOOKS[game], json=payload)
        if r.status_code == 204:
            log(f"DISCORD OK {game}: {title}")
        else:
            log(f"DISCORD error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log(f"DISCORD error: {e}")


def check_feeds():
    log("CHECK: начинаю проверку RSS")
    for game, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                log(f"RSS {game}: пусто")
                continue

            if game not in seen_entries:
                seen_entries[game] = set()
                log(f"RSS {game}: первый запуск, {len(feed.entries)} записей")

            for entry in feed.entries[:3]:
                h = get_entry_hash(entry)
                title = entry.get("title", "Без названия")
                if h not in seen_entries[game]:
                    seen_entries[game].add(h)
                    log(f"RSS НОВОЕ {game}: {title}")
                    link = entry.get("link", "")
                    raw = entry.get("summary", entry.get("description", ""))
                    send_to_discord(game, title, link, raw)
                else:
                    log(f"RSS ПРОПУСК {game}: {title}")
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
