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
MODEL = "openai/gpt-oss-120b"

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

SYSTEM_PROMPT = """Ты — анализатор патч-ноутов для игр (Rust, Garry's Mod, Unturned, s&box).
Твоя задача: перечислить ВСЕ изменения из патч-ноута, НИЧЕГО не пропуская.
НЕ СОКРАЩАЙ. Пиши полные названия, полные пути, полные команды.

Для каждого изменения перечисляй ВСЕ возможные технические идентификаторы через запятую в скобках.
Форматы:
- (команда: полное_название)
- (хук: полное_название)
- (префаб: полный_путь)
- (метод: полное_название)
- (предмет: полное_название)
- (консольная команда: полная_команда)
- (переменная: полное_название)
- (класс: полное_название)

Примеры:
- "Добавлена возможность отправлять UI-разметку (команда: SendMarkupMessage, метод: BasePlayer.SendMarkupMessage)"
- "Новый монумент Apartment Complex (префаб: assets/bundled/prefabs/autospawn/monument/apartment_complex.prefab, команда: spawn monument apartment_complex)"
- "Ускоренная прогрессия в Softcore (консольная команда: sv_accelerated_progression 2, переменная: ConVar.Server.accelerated_progression)"

Верни ТОЛЬКО JSON:
{"main_emoji":"эмодзи","sections":[{"emoji":"эмодзи","title":"Раздел","items":["полный пункт (все идентификаторы)"]}],"nothing_new":false}
Если нет изменений: {"nothing_new":true,"reason":"причина"}.
Пиши на русском. НЕ ПРОПУСКАЙ ни одного изменения. НЕ СОКРАЩАЙ названия. Пиши ПОЛНЫЕ пути и имена."""

def clean_html(text):
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()

def fetch_full_article(url):
    try:
        log(f"FETCH: загружаю {url[:80]}...")
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            text = re.sub(r'<script[^>]*>.*?</script>', '', r.text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text)
            log(f"FETCH: получено {len(text)} символов")
            return text.strip()[:12000]
    except Exception as e:
        log(f"FETCH error: {e}")
    return ""

def analyze_patch(title, raw_text, link=""):
    full_text = ""
    if link:
        full_text = fetch_full_article(link)
    
    if full_text:
        text = full_text[:12000]
    else:
        text = clean_html(raw_text)[:4000]

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Заголовок: {title}\n\n{text}"}
        ],
        "max_tokens": 16384,
        "temperature": 0.3
    }
    
    try:
        log("AI: запрос к Groq (GPT-OSS 120B)...")
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=120)
        log(f"AI: статус {r.status_code}")
        if r.status_code != 200:
            log(f"AI: ошибка - {r.text[:300]}")
            return None
        response_text = r.json()["choices"][0]["message"]["content"]
        log(f"AI: ответ ({len(response_text)} символов)")
        
        response_text = response_text.strip()
        if not response_text.startswith('{'):
            log("AI: ответ не JSON, заворачиваю")
            return {
                "main_emoji": "📦",
                "sections": [{"emoji": "📋", "title": "Изменения", "items": [response_text[:1800]]}],
                "nothing_new": False
            }
        
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
    
    analysis = analyze_patch(title, raw_text, link)
    
    if analysis is None:
        return [f"## 📦 Обновление: **{title}**\n\n⚠️ *Не удалось проанализировать патч.*\n\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"]
    
    if analysis.get("nothing_new"):
        return [f"## ℹ️ Обновление: **{title}**\n\n*{analysis.get('reason', 'Без значительных изменений.')}*\n\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"]
    
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
        for item in items[:15]:
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
    log(f"DISCORD: отправка в {game}...")
    time.sleep(20)
    messages = format_message(game, title, link, raw_text)
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

def check_feeds():
    log("CHECK: начинаю проверку RSS")
    for game, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            log(f"RSS {game}: записей: {len(feed.entries)}")
            
            if not feed.entries:
                continue

            if game not in seen_entries:
                seen_entries[game] = set()
                found = False
                for entry in feed.entries[:10]:
                    if is_old(entry.get("published_parsed")):
                        continue
                    h = get_entry_hash(entry)
                    seen_entries[game].add(h)
                    title = entry.get("title", "Без названия")
                    log(f"RSS ПЕРВЫЙ {game}: {title}")
                    link = entry.get("link", "")
                    raw = entry.get("summary", entry.get("description", ""))
                    send_to_discord(game, title, link, raw)
                    found = True
                    break
                if not found:
                    entry = feed.entries[0]
                    h = get_entry_hash(entry)
                    seen_entries[game].add(h)
                    title = entry.get("title", "Без названия")
                    log(f"RSS ПЕРВЫЙ (все старые) {game}: {title}")
                    link = entry.get("link", "")
                    raw = entry.get("summary", entry.get("description", ""))
                    send_to_discord(game, title, link, raw)
            else:
                for entry in feed.entries[:5]:
                    h = get_entry_hash(entry)
                    title = entry.get("title", "Без названия")
                    if h not in seen_entries[game]:
                        seen_entries[game].add(h)
                        log(f"RSS НОВОЕ {game}: {title}")
                        link = entry.get("link", "")
                        raw = entry.get("summary", entry.get("description", ""))
                        send_to_discord(game, title, link, raw)
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
