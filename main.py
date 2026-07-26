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
from bs4 import BeautifulSoup

app = Flask(__name__)

# ---------- КОНФИГУРАЦИЯ ----------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Модель - автоматический выбор бесплатной модели
MODEL = "tencent/hy3:free"
# Если хотите фиксированную модель, раскомментируйте:
# MODEL = "tencent/hy3:free"

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

CACHE_FILE = "/tmp/processed_news.txt"   # кеш в /tmp (подходит для Render)

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

def is_processed(game, title):
    if not os.path.exists(CACHE_FILE):
        return False
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    return f"{game}|{title}" in content

def mark_processed(game, title):
    with open(CACHE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{game}|{title}\n")

def clean_html(text):
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return re.sub(r'\s+', ' ', text).strip()

def fetch_full_article(url):
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log(f"Ошибка загрузки {url}: {r.status_code}")
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        content_div = soup.find('div', class_=re.compile(r'(post-content|news_content|announcement_content|body|content)'))
        if content_div:
            for tag in content_div(["script", "style"]):
                tag.decompose()
            text = content_div.get_text(separator="\n", strip=True)
        else:
            body = soup.find('body')
            if body:
                for tag in body(["script", "style"]):
                    tag.decompose()
                text = body.get_text(separator="\n", strip=True)
            else:
                text = clean_html(r.text)
        return text[:30000]   # ограничим длину
    except Exception as e:
        log(f"Ошибка загрузки статьи: {e}")
        return ""

# ---------- СИСТЕМНЫЙ ПРОМПТ (русский, JSON) ----------
SYSTEM_PROMPT = """Ты — анализатор патч-ноутов. Твоя задача — извлечь все изменения из предоставленного текста и ВСЕГДА отвечать ТОЛЬКО на РУССКОМ языке.

Для каждого пункта укажи реальные технические идентификаторы в скобках (только если они есть в тексте). Не выдумывай идентификаторы!

Пример хорошего вывода на РУССКОМ языке:
"Добавлен новый монумент Apartment Complex (префаб: assets/bundled/prefabs/autospawn/monument/apartment_complex.prefab, команда: rentroom)"

Твой ответ должен быть только в формате JSON. Никакого другого текста, кроме JSON, быть не должно.
Формат JSON:
{"main_emoji":"эмодзи","sections":[{"emoji":"эмодзи","title":"Название раздела на русском","items":["пункт 1 на русском (идентификаторы)","пункт 2 на русском (идентификаторы)"]}],"nothing_new":false}
Если изменений нет, верни: {"nothing_new":true,"reason":"причина на русском"}"""

def analyze_with_qwen(full_text):
    """Отправляет текст в OpenRouter и возвращает распарсенный JSON."""
    if not OPENROUTER_API_KEY:
        log("API ключ OpenRouter отсутствует")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    user_prompt = f"""Проанализируй следующий патч-ноут и ВЕРНИ ОТВЕТ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ В ФОРМАТЕ JSON.
Полный патч-ноут:
{full_text}"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 6000,
        "temperature": 0.2,
        "response_format": {"type": "json_object"}   # если модель поддерживает
    }

    # Повторные попытки
    for attempt in range(3):
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
            if r.status_code == 429:
                wait = 2 ** attempt
                log(f"Превышен лимит, ждём {wait}с...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                log(f"Ошибка OpenRouter: {r.status_code} {r.text[:300]}")
                return None
            response_text = r.json()["choices"][0]["message"]["content"]
            # Извлекаем JSON из ответа
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not match:
                log("Не найден JSON в ответе модели")
                log(f"Ответ: {response_text[:200]}")
                return None
            json_str = match.group(0)
            # Убираем возможные Markdown-обрамления
            json_str = re.sub(r'^```(?:json)?\s*\n?', '', json_str, flags=re.MULTILINE)
            json_str = re.sub(r'\n?```\s*$', '', json_str, flags=re.MULTILINE)
            return json.loads(json_str)
        except Exception as e:
            log(f"Ошибка при запросе (попытка {attempt+1}): {e}")
            time.sleep(1)
    return None

# ---------- ФОРМАТИРОВАНИЕ И ОТПРАВКА ----------
def format_message(game, title, link, raw_text):
    """Формирует сообщения для Discord на основе анализа."""
    game_name = GAME_NAMES.get(game, game)
    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""

    full_text = fetch_full_article(link) if link else raw_text
    if not full_text:
        log("Не удалось получить текст статьи")
        return None

    analysis = analyze_with_qwen(full_text)
    if analysis is None:
        log("Анализ не удался")
        return None

    if analysis.get("nothing_new"):
        log(f"Нет новых изменений: {analysis.get('reason', '')}")
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
        if not items:
            continue
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
    """Отправляет сообщение в Discord, проверяя кеш."""
    webhook = WEBHOOKS.get(game)
    if not webhook:
        log(f"Вебхук для {game} не настроен")
        return

    if is_processed(game, title):
        log(f"Новость уже обработана: {title}")
        return

    messages = format_message(game, title, link, raw_text)
    if not messages:
        log(f"Нет сообщений для отправки ({title})")
        return

    # Помечаем как обработанную
    mark_processed(game, title)

    for msg in messages:
        payload = {"content": msg, "allowed_mentions": {"parse": []}}
        try:
            r = requests.post(webhook, json=payload)
            if r.status_code == 204:
                log(f"Отправлено в Discord для {game}: {title}")
            else:
                log(f"Ошибка Discord {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log(f"Ошибка отправки в Discord: {e}")
        time.sleep(2)

def is_old(published_parsed):
    """Проверяет, старше ли новость 30 дней."""
    if not published_parsed:
        return False
    try:
        pub_date = datetime.datetime(*published_parsed[:6])
        return (datetime.datetime.now() - pub_date).days > 30
    except:
        return False

def check_feeds():
    """Проверяет все RSS-ленты и обрабатывает только последнюю запись каждой."""
    log("Проверка RSS...")
    for game, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                log(f"Нет записей в {game}")
                continue
            # Берём только самую свежую запись
            entry = feed.entries[0]
            if is_old(entry.get("published_parsed")):
                log(f"Новость старая: {entry.get('title')}")
                continue
            title = entry.get("title", "Без названия")
            link = entry.get("link", "")
            raw = entry.get("summary", entry.get("description", ""))
            log(f"Обработка {game}: {title}")
            send_to_discord(game, title, link, raw)
        except Exception as e:
            log(f"Ошибка RSS для {game}: {e}")

# ---------- FLASK ----------
@app.route("/")
def home():
    return "Monitor running"

@app.route("/check")
def check():
    check_feeds()
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
