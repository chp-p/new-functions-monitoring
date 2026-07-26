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
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

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

CACHE_FILE = "/tmp/processed_news.txt"

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

# ---------- ПАРСИНГ СТАТЕЙ ----------
def fetch_full_article(url):
    try:
        log(f"Загрузка статьи: {url}")
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log(f"Ошибка загрузки {url}: {r.status_code}")
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        content = soup.find("div", class_="blog")
        if not content:
            content = soup.find("div", class_="announcement_body")
        if not content:
            content = soup.find("div", class_="news-section-block")
        if not content:
            content = soup.find("article")
        if not content:
            content = soup.find("main")
        if not content:
            divs = soup.find_all("div")
            for div in divs:
                if len(div.get_text(strip=True)) > 1000:
                    content = div
                    break
        if content:
            text = content.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        log(f"Загружено символов: {len(text)}")
        if len(text) < 100:
            log(f"Предупреждение: короткий текст. Фрагмент: {text[:200]}")
        return text[:50000]
    except Exception as e:
        log(f"Ошибка загрузки статьи: {e}")
        return ""

# ---------- СИСТЕМНЫЙ ПРОМПТ ----------
SYSTEM_PROMPT = """Ты — анализатор патч-ноутов. Извлеки ВСЕ изменения из текста и представь в виде JSON на русском языке.

КРИТИЧЕСКИ ВАЖНО: в тексте есть технические термины. Ты ОБЯЗАН найти и вытащить их все!

Ищи и вытаскивай:
- ConVars (консольные переменные)
- команды (commands)
- переменные (variables)
- префабы (prefabs)
- хуки (hooks)
- методы (methods)
- классы (classes)

ВОТ КОНКРЕТНЫЕ ПРИМЕРЫ ИЗ ЭТОГО ПАТЧ-НОУТА (ты должен найти их в тексте):
1. "Server Owners have been given a bunch of ConVars to tweak this" → (консольная переменная: ConVars)
2. "Remove client.SetPlayerSeed convar" → (переменная: client.SetPlayerSeed)
3. "Fixed some issues with cinematic_play and cinematic_stop commands" → (команда: cinematic_play, команда: cinematic_stop)
4. "duplicate of client.playerseed command" → (команда: client.playerseed)

Если в тексте есть технические термины — ты ОБЯЗАН их вытащить и указать в скобках.
НЕ ВЫДУМЫВАЙ! Если точного названия нет в тексте — не пиши его.

Формат JSON:
{"main_emoji":"эмодзи","sections":[{"emoji":"эмодзи","title":"Название раздела","items":["пункт 1 (идентификаторы)","пункт 2"]}],"nothing_new":false}

ОТВЕТ ТОЛЬКО JSON, БЕЗ ЛИШНЕГО ТЕКСТА."""

# ---------- ОТПРАВКА ЗАПРОСА (без логирования полного ответа) ----------
def send_request(payload):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
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
            # Логируем только длину ответа, чтобы не засорять логи
            log(f"Получен ответ модели (символов: {len(response_text)})")
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not match:
                log("Не найден JSON в ответе модели")
                return None
            json_str = match.group(0)
            json_str = re.sub(r'^```(?:json)?\s*\n?', '', json_str, flags=re.MULTILINE)
            json_str = re.sub(r'\n?```\s*$', '', json_str, flags=re.MULTILINE)
            return json.loads(json_str)
        except Exception as e:
            log(f"Попытка {attempt+1} ошибка: {e}")
            time.sleep(1)
    return None

def has_identifiers(analysis):
    for section in analysis.get("sections", []):
        for item in section.get("items", []):
            if re.search(r'\([^)]+\)', item):
                return True
    return False

# ---------- АНАЛИЗ ----------
def analyze_with_qwen(full_text):
    if not OPENROUTER_API_KEY:
        log("Нет API-ключа")
        return None

    user_prompt = f"""Проанализируй патч-ноут. В тексте есть технические термины: ConVars, команды, префабы, хуки, методы, классы, переменные.
ВЫТАЩИ ИХ ВСЕ и укажи в скобках после каждого пункта.

Патч-ноут:
{full_text}"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 8000,
        "temperature": 0.1
    }

    result = send_request(payload)
    if result:
        if has_identifiers(result):
            log("Идентификаторы найдены.")
            return result
        else:
            log("Идентификаторы не найдены, повторный запрос")
            user_prompt2 = f"""Ты не вытащил технические термины! Найди и укажи в скобках.
Патч-ноут:
{full_text}"""
            payload["messages"][1]["content"] = user_prompt2
            result2 = send_request(payload)
            return result2 if result2 else result
    return None

# ---------- ФОРМАТИРОВАНИЕ (улучшенная разбивка) ----------
def format_message(game, title, link, raw_text):
    game_name = GAME_NAMES.get(game, game)
    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""

    full_text = fetch_full_article(link) if link else raw_text
    if not full_text or len(full_text) < 50:
        log("Не удалось загрузить полную статью, используем краткий анонс")
        full_text = raw_text

    if not full_text or len(full_text) < 20:
        log("Нет текста для анализа")
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
    max_len = 1900  # чуть меньше лимита Discord (2000)

    for section in analysis.get("sections", []):
        emoji = section.get("emoji", "🔹")
        section_title = section.get("title", "Изменения")
        items = section.get("items", [])
        if not items:
            continue
        block = f"\n### {emoji} {section_title}:\n"
        # Формируем блок из пунктов
        item_lines = []
        for item in items[:10]:
            item_lines.append(f"🔹 {item}")
        # Если блок не влезает в текущее сообщение, отправляем его отдельно
        if len(current + block + "\n".join(item_lines)) > max_len:
            # Закрываем текущее сообщение
            current += f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name} (часть {part})"
            messages.append(current)
            part += 1
            # Начинаем новое сообщение с заголовка
            current = f"## {title_line} (часть {part})\n"
        # Добавляем блок и пункты
        current += block + "\n".join(item_lines) + "\n"

    # Добавляем ссылку в конец последнего сообщения
    footer = f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"
    if part > 1:
        footer += f" (часть {part})"
    # Проверяем, влезет ли футер в текущее сообщение
    if len(current + footer) > max_len:
        messages.append(current)
        current = footer
    else:
        current += footer
    messages.append(current)

    return messages

# ---------- ОТПРАВКА В DISCORD ----------
def send_to_discord(game, title, link, raw_text):
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

    mark_processed(game, title)

    for msg in messages:
        if len(msg) > 2000:
            log(f"Предупреждение: сообщение длиной {len(msg)} символов, может не отправиться")
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
    if not published_parsed:
        return False
    try:
        pub_date = datetime.datetime(*published_parsed[:6])
        return (datetime.datetime.now() - pub_date).days > 30
    except:
        return False

def check_feeds():
    log("Проверка RSS...")
    for game, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                log(f"Нет записей в {game}")
                continue
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
