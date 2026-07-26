import os
import requests
import hashlib
import re
import html
import json
import feedparser
from flask import Flask

app = Flask(__name__)

# ====== НАСТРОЙКИ ======

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-flash-1.5-8b"

WEBHOOKS = {
    "rust": "https://discord.com/api/webhooks/1530915163325075467/s0hhJdWkYb4eGEhfPF7GPYEpsFGTLXD5rjiwdFq8eMsHF8ndf_NRMbwaIvqkxOkCdjy4",
    "garrysmod": "https://discord.com/api/webhooks/1530915445224378398/3J8-YM7OjeqxaUJaL1LJobQD9O0KnZjj8h-PjP4IYTIjUQXkXRQwtOhJTisZ52jQUSEk",
    "unturned": "https://discord.com/api/webhooks/1530915598844825620/tMPKR9KZvEKCyGNyXDbQEmshF5xzs_MS5WbnTn6bwC6QkNBALY9sFLAZmeJP4SmuOPBN",
    "sbox": "https://discord.com/api/webhooks/1530916231870156903/Vn_-MQHI9kMk1qTpXYbE7z4qItcRQA9uaXJMGxZV_2ad-iai7g6YDPdq6JhzrQZWMowq"
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

# ====== AI АНАЛИЗ ПАТЧА ======

SYSTEM_PROMPT = """Ты — анализатор патч-ноутов для игр (Rust, Garry's Mod, Unturned, s&box).
Твоя задача: прочитать патч-ноут и выделить ТОЛЬКО новые функции и изменения.
Пропускай косметику, благотворительные скины без геймплейных изменений, мелкие фиксы багов, общие слова.

Верни ТОЛЬКО JSON без markdown-обёртки:

{
  "main_emoji": "один эмодзи, подходящий под главную тему патча",
  "sections": [
    {
      "emoji": "эмодзи раздела",
      "title": "Название раздела",
      "items": ["конкретный пункт", "конкретный пункт"]
    }
  ],
  "nothing_new": false
}

Если патч не содержит НИКАКИХ новых функций, предметов, оружия, транспорта, изменений карты, API или геймплея — верни {"nothing_new": true, "reason": "краткая причина"}.

Правила:
- Разделы создавай ТОЛЬКО под то, что реально есть в патче.
- Названия разделов адаптируй под содержимое.
- Пункты должны быть конкретными.
- Пиши на русском языке.
- Уложись в 1800 символов."""


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

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Заголовок: {title}\n\nТекст патч-ноута:\n{text}"}
        ],
        "max_tokens": 1500,
        "temperature": 0.3
    }

    try:
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
        if r.status_code != 200:
            print(f"API error: {r.status_code}")
            return None

        response_text = r.json()["choices"][0]["message"]["content"]
        response_text = re.sub(r'^```(?:json)?\s*\n?', '', response_text)
        response_text = re.sub(r'\n?```\s*$', '', response_text)

        return json.loads(response_text)

    except Exception as e:
        print(f"AI error: {e}")
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
            f"📎 [Полный патч-ноут]({link}) | 🎮 {game_name}"
        )

    if analysis.get("nothing_new"):
        return (
            f"## ℹ️ Обновление: **{title}**\n\n"
            f"*{analysis.get('reason', 'Без значительных изменений.')}*\n\n"
            f"📎 [Полный патч-ноут]({link}) | 🎮 {game_name}"
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

    message += f"\n📎 [Полный патч-ноут]({link}) | 🎮 {game_name}"

    if len(message) > 1950:
        message = message[:1920] + "\n\n...\n📎 " + link

    return message


# ====== ОСНОВНАЯ ЛОГИКА ======

seen_entries = {}


def get_entry_hash(entry):
    content = entry.get("title", "") + entry.get("link", "")
    return hashlib.md5(content.encode()).hexdigest()


def send_to_discord(game, title, link, raw_text):
    if game not in WEBHOOKS:
        return

    content = format_message(game, title, link, raw_text)
    payload = {"content": content, "allowed_mentions": {"parse": []}}

    try:
        r = requests.post(WEBHOOKS[game], json=payload)
        if r.status_code == 204:
            print(f"OK {game}: {title}")
        else:
            print(f"Discord error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Send error: {e}")


def check_feeds():
    for game, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                continue

            if game not in seen_entries:
                seen_entries[game] = set()

            for entry in feed.entries[:3]:
                h = get_entry_hash(entry)
                if h not in seen_entries[game]:
                    seen_entries[game].add(h)
                    title = entry.get("title", "Без названия")
                    link = entry.get("link", "")
                    raw = entry.get("summary", entry.get("description", ""))
                    send_to_discord(game, title, link, raw)
        except Exception as e:
            print(f"Feed error {game}: {e}")


@app.route("/")
def home():
    return "Monitor running"


@app.route("/check")
def check():
    check_feeds()
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
