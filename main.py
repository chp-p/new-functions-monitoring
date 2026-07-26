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
    "sbox": os.environ.get("WEBHOOK_SBOX", ""),
    "warthunder": os.environ.get("WEBHOOK_WARTHUNDER", "")
}

RSS_FEEDS = {
    "rust": "https://rust.facepunch.com/rss",
    "garrysmod": "https://store.steampowered.com/feeds/news/app/4000/",
    "unturned": "https://store.steampowered.com/feeds/news/app/304930/",
    "sbox": "https://sbox.facepunch.com/news/rss",
    "warthunder": "https://warthunder.com/en/rss/news/"
}

GAME_NAMES = {
    "rust": "Rust",
    "garrysmod": "Garry's Mod",
    "unturned": "Unturned",
    "sbox": "s&box",
    "warthunder": "War Thunder"
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

# ---------- УНИВЕРСАЛЬНЫЙ ПАРСИНГ СТАТЕЙ (с извлечением изображений) ----------
def fetch_full_article(url, game=None):
    """
    Возвращает кортеж (текст_статьи, список_URL_изображений)
    """
    try:
        log(f"Загрузка статьи: {url}")
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log(f"Ошибка загрузки {url}: {r.status_code}")
            return "", []
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()

        # Определяем стратегию парсинга контента
        content = None
        if game == "rust" or "facepunch.com" in url:
            content = soup.find("div", class_="blog")
        elif game == "warthunder" or "warthunder.com" in url:
            content = soup.find("div", class_="news-text")
            if not content:
                content = soup.find("div", class_="content")
        elif "steampowered.com" in url:
            content = soup.find("div", class_="announcement_body")
            if not content:
                content = soup.find("div", class_="news_post")
        else:
            # Общий поиск
            content = soup.find("div", class_="blog")
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

        # --- Извлечение изображений (для War Thunder) ---
        image_urls = []
        if game == "warthunder":
            # Ищем все теги <img> внутри контента (или во всем soup)
            img_tags = soup.find_all("img")
            for img in img_tags:
                src = img.get("src")
                if src:
                    # Преобразуем относительные URL в абсолютные
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://warthunder.com" + src
                    # Фильтруем маленькие иконки, аватары и т.п.
                    width = img.get("width")
                    height = img.get("height")
                    # Если ширина/высота явно заданы и меньше 100px, пропускаем
                    if width and height:
                        try:
                            if int(width) < 100 or int(height) < 100:
                                continue
                        except:
                            pass
                    # Также можно проверить размер файла через HEAD, но это долго
                    image_urls.append(src)
            log(f"Найдено изображений: {len(image_urls)}")

        return text[:50000], image_urls
    except Exception as e:
        log(f"Ошибка загрузки статьи: {e}")
        return "", []

# ---------- СИСТЕМНЫЙ ПРОМПТ (для всех игр) ----------
SYSTEM_PROMPT = """Ты — анализатор патч-ноутов компьютерных игр. Извлеки ВСЕ изменения из текста и представь их в виде JSON на РУССКОМ языке.

КРИТИЧЕСКИ ВАЖНО: ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ. ВСЕ ЗАГОЛОВКИ РАЗДЕЛОВ, ПУНКТЫ И ИДЕНТИФИКАТОРЫ ДОЛЖНЫ БЫТЬ НА РУССКОМ.

В тексте могут быть технические термины (команды, консольные переменные, префабы, хуки, методы, классы). Ты ОБЯЗАН найти их все и указать в круглых скобках после каждого пункта, если они есть.

Форматы идентификаторов (на русском):
- (команда: имя)
- (консольная переменная: имя)
- (префаб: путь)
- (хук: имя)
- (метод: Класс.Метод)
- (класс: имя)

НЕ ВЫДУМЫВАЙ идентификаторы! Если точного названия нет в тексте — не пиши его.

Формат JSON:
{"main_emoji":"эмодзи","sections":[{"emoji":"эмодзи","title":"Название раздела на русском","items":["пункт 1 на русском (идентификаторы)","пункт 2"]}],"nothing_new":false}

Если изменений нет:
{"nothing_new": true, "reason": "причина на русском"}

ОТВЕТ ТОЛЬКО JSON, БЕЗ ЛИШНЕГО ТЕКСТА, ВСЁ НА РУССКОМ."""

# ---------- ОТПРАВКА ЗАПРОСА ----------
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

    user_prompt = f"""Проанализируй этот патч-ноут. В тексте есть технические термины (команды, консольные переменные, префабы, хуки, методы, классы).
ВЫТАЩИ ИХ ВСЕ и укажи в скобках после каждого пункта. ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ.

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
            log("Идентификаторы не найдены, повторный запрос с требованием")
            user_prompt2 = f"""Ты не вытащил технические термины! Найди их и укажи в скобках. ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ.
Патч-ноут:
{full_text}"""
            payload["messages"][1]["content"] = user_prompt2
            result2 = send_request(payload)
            return result2 if result2 else result
    return None

# ---------- ФОРМАТИРОВАНИЕ И ОТПРАВКА В DISCORD (с изображениями) ----------
def format_message(game, title, link, raw_text):
    game_name = GAME_NAMES.get(game, game)
    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""

    full_text, image_urls = fetch_full_article(link, game) if link else (raw_text, [])
    if not full_text or len(full_text) < 50:
        log("Не удалось загрузить полную статью, используем краткий анонс")
        full_text = raw_text
        # Для War Thunder попробуем извлечь изображения даже из анонса
        if game == "warthunder" and not image_urls:
            # Попробуем спарсить изображения из raw_text, но там обычно нет HTML
            pass

    if not full_text or len(full_text) < 20:
        log("Нет текста для анализа")
        return None, []

    analysis = analyze_with_qwen(full_text)
    if analysis is None:
        log("Анализ не удался")
        return None, []

    if analysis.get("nothing_new"):
        log(f"Нет новых изменений: {analysis.get('reason', '')}")
        return [], []

    main_emoji = analysis.get("main_emoji", "📦")
    title_line = f"{main_emoji} Обновление: **{title}**"
    if version:
        title_line += f" — `{version}`"

    messages = []
    current = f"## {title_line}\n"
    part = 1
    max_len = 1900

    for section in analysis.get("sections", []):
        emoji = section.get("emoji", "🔹")
        section_title = section.get("title", "Изменения")
        items = section.get("items", [])
        if not items:
            continue
        block = f"\n### {emoji} {section_title}:\n"
        item_lines = []
        for item in items[:10]:
            item_lines.append(f"🔹 {item}")
        if len(current + block + "\n".join(item_lines)) > max_len:
            current += f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name} (часть {part})"
            messages.append(current)
            part += 1
            current = f"## {title_line} (часть {part})\n"
        current += block + "\n".join(item_lines) + "\n"

    footer = f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name}"
    if part > 1:
        footer += f" (часть {part})"
    if len(current + footer) > max_len:
        messages.append(current)
        current = footer
    else:
        current += footer
    messages.append(current)

    # Возвращаем текстовые сообщения и список URL изображений
    return messages, image_urls

def send_to_discord(game, title, link, raw_text):
    webhook = WEBHOOKS.get(game)
    if not webhook:
        log(f"Вебхук для {game} не настроен")
        return

    if is_processed(game, title):
        log(f"Новость уже обработана: {title}")
        return

    text_messages, image_urls = format_message(game, title, link, raw_text)
    if not text_messages:
        log(f"Нет сообщений для отправки ({title})")
        return

    mark_processed(game, title)

    # Отправляем текстовые сообщения
    for msg in text_messages:
        if len(msg) > 2000:
            log(f"Предупреждение: длина сообщения {len(msg)} > 2000")
        payload = {"content": msg, "allowed_mentions": {"parse": []}}
        try:
            r = requests.post(webhook, json=payload)
            if r.status_code == 204:
                log(f"Отправлено текстовое сообщение для {game}: {title}")
            else:
                log(f"Ошибка Discord {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log(f"Ошибка отправки в Discord: {e}")
        time.sleep(1)

    # Отправляем изображения (только для War Thunder)
    if image_urls and game == "warthunder":
        log(f"Отправка {len(image_urls)} изображений для {game}")
        # Группируем изображения по 5 в одном сообщении (чтобы не флудить)
        chunk_size = 5
        for i in range(0, len(image_urls), chunk_size):
            chunk = image_urls[i:i+chunk_size]
            # Создаём сообщение со ссылками
            img_text = "📷 **Изображения из новости:**\n" + "\n".join(chunk)
            if len(img_text) > 2000:
                # Если ссылки слишком длинные, разбиваем по одной
                for url in chunk:
                    payload = {"content": f"📷 {url}", "allowed_mentions": {"parse": []}}
                    try:
                        r = requests.post(webhook, json=payload)
                        if r.status_code == 204:
                            log(f"Отправлено изображение: {url}")
                        else:
                            log(f"Ошибка отправки изображения: {r.status_code}")
                    except Exception as e:
                        log(f"Ошибка отправки изображения: {e}")
                    time.sleep(1)
            else:
                payload = {"content": img_text, "allowed_mentions": {"parse": []}}
                try:
                    r = requests.post(webhook, json=payload)
                    if r.status_code == 204:
                        log(f"Отправлена группа изображений ({len(chunk)} шт)")
                    else:
                        log(f"Ошибка отправки группы изображений: {r.status_code}")
                except Exception as e:
                    log(f"Ошибка отправки группы изображений: {e}")
                time.sleep(1)

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ RSS ----------
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
