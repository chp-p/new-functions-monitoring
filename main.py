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
from supabase import create_client, Client

app = Flask(__name__)

# ---------- КОНФИГУРАЦИЯ ----------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Единая модель для всех игр
MODELS = {
    "rust": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "garrysmod": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "unturned": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "sbox": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "warthunder": "nvidia/nemotron-3-ultra-550b-a55b:free"
}

# Запасные модели при ошибках
FALLBACK_MODELS = [
    "qwen/qwen-2.5-72b-instruct:free",
    "google/gemma-4-31b-it:free",
    "deepseek/deepseek-v4-flash:free"
]

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

# ---------- SUPABASE ----------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
supabase: Client = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        supabase.table("processed_news").select("*").limit(1).execute()
        print("✅ Supabase подключен, таблица существует.")
    except Exception as e:
        print(f"⚠️ Ошибка Supabase: {e}")
        supabase = None
else:
    print("⚠️ SUPABASE_URL или SUPABASE_KEY не заданы, используется файловый кеш.")

# ---------- ОГРАНИЧИТЕЛЬ ЧАСТОТЫ ----------
class RateLimiter:
    def __init__(self, requests_per_minute=17):
        self.interval = 60.0 / requests_per_minute
        self.last_request_time = 0

    def wait_if_needed(self):
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self.last_request_time = time.time()

rate_limiter = RateLimiter(requests_per_minute=17)

# ---------- ФУНКЦИИ БАЗЫ ДАННЫХ ----------
def is_processed(game, title):
    if supabase:
        try:
            resp = supabase.table("processed_news").select("*").eq("game", game).eq("title", title).execute()
            return len(resp.data) > 0
        except:
            pass
    # fallback на файл
    CACHE_FILE = "/tmp/processed_news.txt"
    if not os.path.exists(CACHE_FILE):
        return False
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return f"{game}|{title}" in f.read()

def mark_processed(game, title):
    if supabase:
        try:
            supabase.table("processed_news").insert({"game": game, "title": title}).execute()
            return
        except:
            pass
    CACHE_FILE = "/tmp/processed_news.txt"
    with open(CACHE_FILE, "a", encoding="utf-8") as f:
        f.write(f"{game}|{title}\n")

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

# ---------- ПАРСИНГ СТАТЕЙ ----------
def fetch_full_article(url, game=None):
    try:
        log(f"Загрузка статьи: {url}")
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log(f"Ошибка загрузки {url}: {r.status_code}")
            return "", []
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()

        content = None
        if "facepunch.com" in url:
            content = soup.find("div", class_="blog")
        elif "warthunder.com" in url:
            content = soup.find("div", class_="news-text") or soup.find("div", class_="content")
        elif "steampowered.com" in url:
            content = soup.find("div", class_="announcement_body") or soup.find("div", class_="news_post_content")
            if not content:
                for div in soup.find_all("div"):
                    if len(div.get_text(strip=True)) > 2000:
                        content = div
                        break
        else:
            content = soup.find("article") or soup.find("main") or soup.find("div", class_="content")

        text = content.get_text(separator="\n", strip=True) if content else soup.get_text(separator="\n", strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        log(f"Загружено символов: {len(text)}")

        image_urls = []
        if game == "warthunder":
            for img in soup.find_all("img"):
                src = img.get("src")
                if src:
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://warthunder.com" + src
                    image_urls.append(src)
            log(f"Найдено изображений: {len(image_urls)}")

        return text[:50000], image_urls
    except Exception as e:
        log(f"Ошибка загрузки статьи: {e}")
        return "", []

# ---------- HTML-ПАРСИНГ WAR THUNDER ----------
def fetch_warthunder_news_from_html():
    try:
        url = "https://warthunder.com/en/news/"
        log(f"Парсинг HTML новостей War Thunder: {url}")
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.find_all("div", class_=re.compile(r"news-item|news-block|news-card")):
            title_tag = item.find("h2") or item.find("h3") or item.find("a")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            link_tag = item.find("a")
            if link_tag:
                href = link_tag.get("href")
                if href and href.startswith("/"):
                    href = "https://warthunder.com" + href
                return {"title": title, "link": href}
        # если не нашли, ищем ссылки
        for a in soup.find_all("a", href=re.compile(r"/en/news/")):
            if a.get_text(strip=True):
                title = a.get_text(strip=True)
                href = a.get("href")
                if href and href.startswith("/"):
                    href = "https://warthunder.com" + href
                return {"title": title, "link": href}
        log("Не удалось найти новости на HTML-странице War Thunder")
        return None
    except Exception as e:
        log(f"Ошибка парсинга HTML War Thunder: {e}")
        return None

# ---------- СИСТЕМНЫЙ ПРОМПТ ----------
SYSTEM_PROMPT = """Ты — анализатор патч-ноутов. Извлеки ВСЕ изменения из текста и представь их в виде JSON на РУССКОМ языке.
КРИТИЧЕСКИ ВАЖНО: ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ.
В тексте могут быть технические термины (команды, консольные переменные, префабы, хуки, методы, классы) – вытащи их и укажи в скобках.
НЕ ВЫДУМЫВАЙ идентификаторы! Если точного названия нет – не пиши.
Формат JSON:
{"main_emoji":"эмодзи","sections":[{"emoji":"эмодзи","title":"Название раздела","items":["пункт 1 (идентификаторы)","пункт 2"]}],"nothing_new":false}
Если изменений нет: {"nothing_new": true, "reason": "причина на русском"}
ОТВЕТ ТОЛЬКО JSON, БЕЗ ЛИШНЕГО ТЕКСТА, ВСЁ НА РУССКОМ."""

# ---------- ОТПРАВКА ЗАПРОСА ----------
def send_request(payload, model):
    rate_limiter.wait_if_needed()
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload["model"] = model

    for attempt in range(3):
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                log(f"Превышен лимит для {model}, ждём {wait}с...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                log(f"Ошибка OpenRouter: {r.status_code} {r.text[:300]}")
                if attempt < len(FALLBACK_MODELS):
                    fallback = FALLBACK_MODELS[attempt]
                    log(f"Переключаемся на {fallback}")
                    payload["model"] = fallback
                    continue
                return None
            response_text = r.json()["choices"][0]["message"]["content"]
            log(f"Получен ответ модели (символов: {len(response_text)})")
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not match:
                log("Не найден JSON")
                return None
            json_str = re.sub(r'^```(?:json)?\s*\n?', '', match.group(0), flags=re.MULTILINE)
            json_str = re.sub(r'\n?```\s*$', '', json_str, flags=re.MULTILINE)
            return json.loads(json_str)
        except Exception as e:
            log(f"Попытка {attempt+1} ошибка: {e}")
            time.sleep(2 ** attempt)
    return None

def analyze_with_qwen(full_text, model):
    if not OPENROUTER_API_KEY:
        log("Нет API-ключа")
        return None
    user_prompt = f"""Проанализируй патч-ноут. Вытащи ВСЕ технические термины (команды, консольные переменные, префабы, хуки, методы, классы) и укажи в скобках. ОТВЕЧАЙ НА РУССКОМ.
Патч-ноут:
{full_text}"""
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 8000,
        "temperature": 0.1
    }
    result = send_request(payload, model)
    if result:
        has_id = any(re.search(r'\([^)]+\)', item) for sec in result.get("sections", []) for item in sec.get("items", []))
        if has_id:
            log("Идентификаторы найдены.")
            return result
        else:
            log("Идентификаторы не найдены, повторный запрос с требованием")
            payload["messages"][1]["content"] = f"""Ты не вытащил технические термины! Найди их и укажи в скобках. ОТВЕЧАЙ НА РУССКОМ.
Патч-ноут:
{full_text}"""
            result2 = send_request(payload, model)
            return result2 if result2 else result
    return None

# ---------- ФОРМАТИРОВАНИЕ И ОТПРАВКА В DISCORD ----------
def format_message(game, title, link, raw_text, model):
    game_name = GAME_NAMES.get(game, game)
    version_match = re.search(r'(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+|\d+\.\d+)', title)
    version = version_match.group(1) if version_match else ""

    full_text, image_urls = fetch_full_article(link, game) if link else (raw_text, [])
    if not full_text or len(full_text) < 50:
        log("Не удалось загрузить полную статью, используем краткий анонс")
        full_text = raw_text
    if not full_text or len(full_text) < 20:
        log("Нет текста для анализа")
        return None, []

    analysis = analyze_with_qwen(full_text, model)
    if analysis is None:
        return None, []
    if analysis.get("nothing_new"):
        log(f"Нет новых изменений: {analysis.get('reason', '')}")
        return [], []

    main_emoji = analysis.get("main_emoji", "📦")
    title_line = f"{main_emoji} Обновление: **{title}**" + (f" — `{version}`" if version else "")
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
        item_lines = [f"🔹 {item}" for item in items[:10]]
        if len(current + block + "\n".join(item_lines)) > max_len:
            current += f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name} (часть {part})"
            messages.append(current)
            part += 1
            current = f"## {title_line} (часть {part})\n"
        current += block + "\n".join(item_lines) + "\n"

    footer = f"\n📎 [Патч-ноут]({link}) | 🎮 {game_name}" + (f" (часть {part})" if part > 1 else "")
    if len(current + footer) > max_len:
        messages.append(current)
        current = footer
    else:
        current += footer
    messages.append(current)
    return messages, image_urls

def send_to_discord(game, title, link, raw_text):
    webhook = WEBHOOKS.get(game)
    if not webhook:
        log(f"Вебхук для {game} не настроен")
        return
    if is_processed(game, title):
        log(f"Новость уже обработана: {title}")
        return

    model = MODELS.get(game, "nvidia/nemotron-3-ultra-550b-a55b:free")
    text_messages, image_urls = format_message(game, title, link, raw_text, model)
    if not text_messages:
        log(f"Нет сообщений для отправки ({title})")
        return

    mark_processed(game, title)

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
        time.sleep(2)

    if image_urls and game == "warthunder":
        log(f"Отправка {len(image_urls)} изображений для {game}")
        chunk_size = 5
        for i in range(0, len(image_urls), chunk_size):
            chunk = image_urls[i:i+chunk_size]
            img_text = "📷 **Изображения из новости:**\n" + "\n".join(chunk)
            if len(img_text) > 2000:
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

# ---------- RSS ----------
def is_old(published_parsed):
    if not published_parsed:
        return False
    try:
        pub_date = datetime.datetime(*published_parsed[:6])
        return (datetime.datetime.now() - pub_date).days > 30
    except:
        return False

def process_game(game, url):
    try:
        feed = feedparser.parse(url)
        entries = feed.entries
        if not entries:
            if game == "warthunder":
                log("RSS War Thunder пуст, пробуем HTML...")
                news = fetch_warthunder_news_from_html()
                if news:
                    title, link = news.get("title"), news.get("link")
                    log(f"Обработка {game} (из HTML): {title}")
                    send_to_discord(game, title, link, "")
                else:
                    log(f"Не удалось получить новости для {game}")
            else:
                log(f"Нет записей в {game}")
            return
        entry = entries[0]
        if is_old(entry.get("published_parsed")):
            log(f"Новость старая: {entry.get('title')}")
            return
        title = entry.get("title", "Без названия")
        link = entry.get("link", "")
        raw = entry.get("summary", entry.get("description", ""))
        log(f"Обработка {game} (из RSS): {title}")
        send_to_discord(game, title, link, raw)
    except Exception as e:
        log(f"Ошибка обработки для {game}: {e}")

def check_feeds():
    log("Проверка RSS...")
    order = ["warthunder", "garrysmod", "unturned", "sbox", "rust"]
    for game in order:
        url = RSS_FEEDS.get(game)
        if url:
            process_game(game, url)
            time.sleep(3.5)

# ---------- ЭНДПОИНТЫ ----------
@app.route("/")
def home():
    return "Monitor running"

@app.route("/check")
def check():
    check_feeds()
    return "OK"

@app.route("/quota")
def check_quota():
    if not OPENROUTER_API_KEY:
        return {"error": "API key not set"}, 400
    try:
        resp = requests.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "limit": data.get("limit"),
                "remaining": data.get("limit_remaining"),
                "daily_usage": data.get("usage_daily")
            }
        else:
            return {"error": resp.text}, resp.status_code
    except Exception as e:
        return {"error": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
