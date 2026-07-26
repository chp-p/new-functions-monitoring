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

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"

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

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

SYSTEM_PROMPT = """Ты — анализатор патч-ноутов для игр. Выдели ТОЛЬКО новые функции и изменения геймплея.
Верни ТОЛЬКО JSON без markdown:
{"main_emoji":"эмодзи","sections":[{"emoji":"эмодзи","title":"Раздел","items":["пункт"]}],"nothing_new":false}
Если нет новых функций: {"nothing_new":true,"reason":"причина"}. Пиши на русском."""

def clean_html(text):
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()

def analyze_patch(title, raw_text):
    text = clean_html(raw_text)[:8000]
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"Заголовок: {title}\n\n{text}"}], "max_tokens": 1500, "temperature": 0.3}
    try:
        log("AI: запрос к Groq...")
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
        log(f"AI: статус {r.status_code}")
        if r.status_code != 200:
            log(f"AI: ошибка - {r.text[:300]}")
            return None
        response_text = r.json()["choices"][0]["message"]["content"]
        log(f"AI: ответ ({len(response_text)} символов)")
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
    analysis = analyze_patch(title, raw_text)
    if analysis is None:
        return f"## 📦 Обновление: **{title}**\n\n⚠️ *Не удалось проанализировать патч.*\n\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"
    if analysis.get("nothing_new"):
        return f"## ℹ️ Обновление: **{title}**\n\n*{analysis.get('reason', 'Без изменений.')}*\n\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"
    main_emoji = analysis.get("main_emoji", "📦")
    title_line = f"{main_emoji} Обновление: **{title}**"
    if version:
        title_line += f" — `{version}`"
    message = f"## {title_line}\n"
    for section in analysis.get("sections", []):
        message += f"\n### {section.get('emoji', '🔹')} {section.get('title', 'Изменения')}:\n"
        for item in section.get("items", [])[:8]:
            message += f"🔹 {item}\n"
    message += f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"
    return message[:1950]

seen_entries = {}

def get_entry_hash(entry):
    return hashlib.md5((entry.get("title","")+entry.get("link","")).encode()).hexdigest()

def send_to_discord(game, title, link, raw_text):
    if not WEBHOOKS.get(game):
        return
    log(f"DISCORD: {game}...")
    content = format_message(game, title, link, raw_text)
    try:
        r = requests.post(WEBHOOKS[game], json={"content": content, "allowed_mentions": {"parse": []}})
        log(f"DISCORD {'OK' if r.status_code==204 else 'error '+str(r.status_code)}")
    except Exception as e:
        log(f"DISCORD error: {e}")

def check_feeds():
    log("CHECK: начинаю")
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
                    log(f"НОВОЕ {game}: {entry.get('title','')}")
                    send_to_discord(game, entry.get("title",""), entry.get("link",""), entry.get("summary",""))
        except Exception as e:
            log(f"RSS error {game}: {e}")
    log("CHECK: завершено")

@app.route("/")
def home():
    return "OK"

@app.route("/check")
def check():
    check_feeds()
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
