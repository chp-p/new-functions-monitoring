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

OR_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_URL = "https://models.github.ai/inference/chat/completions"
GITHUB_MODEL = "openai/gpt-4.1"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

MODELS = {
    "rust": OR_MODEL,
    "garrysmod": OR_MODEL,
    "unturned": OR_MODEL,
    "sbox": OR_MODEL,
    "warthunder": OR_MODEL
}

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
    "sbox": "https://sbox.game/news/rss",
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
SUPABASE_ENABLED = False

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        test = supabase.table("processed_news").select("*").limit(1).execute()
        SUPABASE_ENABLED = True
        print("✅ Supabase подключен, таблица существует.")
        print("ℹ️ Если видите ошибки RLS, выполните в SQL Editor:")
        print("ALTER TABLE processed_news DISABLE ROW LEVEL SECURITY;")
    except Exception as e:
        print(f"⚠️ Ошибка подключения к Supabase: {e}")
        SUPABASE_ENABLED = False
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
    if SUPABASE_ENABLED and supabase:
        try:
            resp = supabase.table("processed_news").select("*").eq("game", game).eq("title", title).execute()
            if len(resp.data) > 0:
                log(f"Найдено в Supabase: {game}|{title}")
                return True
        except Exception as e:
            log(f"Ошибка запроса к Supabase: {e}")
    try:
        CACHE_FILE = "/tmp/processed_news.txt"
        if not os.access("/tmp", os.W_OK):
            CACHE_FILE = "processed_news.txt"
        if not os.path.exists(CACHE_FILE):
            return False
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        found = f"{game}|{title}" in content
        if found:
            log(f"Найдено в файловом кеше: {game}|{title}")
        return found
    except Exception as e:
        log(f"Ошибка файлового кеша (is_processed): {e}")
        return False

def mark_processed(game, title):
    if SUPABASE_ENABLED and supabase:
        try:
            supabase.table("processed_news").upsert({"game": game, "title": title}, on_conflict="game,title").execute()
            log(f"Записано в Supabase: {game}|{title}")
            return
        except Exception as e:
            log(f"Ошибка записи в Supabase: {e}")
    try:
        CACHE_FILE = "/tmp/processed_news.txt"
        if not os.access("/tmp", os.W_OK):
            CACHE_FILE = "processed_news.txt"
        with open(CACHE_FILE, "a", encoding="utf-8") as f:
            f.write(f"{game}|{title}\n")
        log(f"Записано в файловый кеш: {game}|{title}")
    except Exception as e:
        log(f"Ошибка записи в файловый кеш: {e}")

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
        if "facepunch.com" in url or "sbox.game" in url:
            content = soup.find("div", class_="blog") or soup.find("div", class_="post-content") or soup.find("article")
        elif "warthunder.com" in url:
            content = soup.find("div", class_="news-text") or soup.find("div", class_="content")
        elif "steampowered.com" in url:
            content = soup.find("div", class_="announcement_body") or soup.find("div", class_="news_post_content")
            if not content:
                for div in soup.find_all("div"):
                    if len(div.get_text(strip=True)) > 1000:
                        content = div
                        break
        else:
            content = soup.find("article") or soup.find("main") or soup.find("div", class_="content")

        if content:
            text = content.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        text = re.sub(r'\s+', ' ', text).strip()
        log(f"Загружено символов: {len(text)}")

        image_urls = []
        if game == "warthunder":
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src")
                if src:
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://warthunder.com" + src
                    if re.search(r'\.(jpg|jpeg|png|gif|webp|bmp|svg)(\?.*)?$', src, re.I):
                        image_urls.append(src)
            image_urls = list(dict.fromkeys(image_urls))
            log(f"Найдено изображений: {len(image_urls)}")

        return text[:50000], image_urls
    except Exception as e:
        log(f"Ошибка загрузки статьи: {e}")
        return "", []

# ---------- ПАРСИНГ HTML ДЛЯ sbox ----------
def fetch_sbox_news_from_html():
    try:
        url = "https://sbox.game/news/"
        log(f"Парсинг HTML новостей sbox: {url}")
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log(f"Ошибка загрузки страницы sbox: {r.status_code}")
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        # 1. Ищем все ссылки на /news/
        candidates = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/news/" in href and not href.endswith("/news/"):
                title = a.get_text(strip=True)
                if not title:
                    parent = a.find_parent()
                    if parent:
                        heading = parent.find(["h1", "h2", "h3"])
                        if heading:
                            title = heading.get_text(strip=True)
                if title and len(title) > 5:
                    if href.startswith("/"):
                        href = "https://sbox.game" + href
                    elif href.startswith("./"):
                        href = "https://sbox.game" + href[1:]
                    candidates.append({"title": title, "link": href})

        if candidates:
            log(f"Найдена новость через ссылку: {candidates[0]['title']}")
            return candidates[0]

        # 2. Если не нашли через ссылки, ищем любой блок с текстом > 300 символов
        for tag in soup.find_all(["div", "article", "section"]):
            text = tag.get_text(strip=True)
            if len(text) > 300:
                link_tag = tag.find("a", href=re.compile(r"/news/"))
                if link_tag:
                    href = link_tag["href"]
                    if href.startswith("/"):
                        href = "https://sbox.game" + href
                    title_tag = tag.find("h1") or tag.find("h2") or tag.find("h3") or link_tag
                    title = title_tag.get_text(strip=True) if title_tag else "Новость sbox"
                    log(f"Найдена новость через блок: {title}")
                    return {"title": title, "link": href}

        log("Не удалось найти новости на HTML-странице sbox")
        return None
    except Exception as e:
        log(f"Ошибка парсинга HTML sbox: {e}")
        return None

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
    payload_copy = payload.copy()
    payload_copy["model"] = model
    try:
        log(f"Пробуем OpenRouter модель: {model}")
        r = requests.post(OPENROUTER_URL, headers=headers, json=payload_copy, timeout=120)
        if r.status_code == 429:
            log("OpenRouter лимит, ждём 5с...")
            time.sleep(5)
            r = requests.post(OPENROUTER_URL, headers=headers, json=payload_copy, timeout=120)
            if r.status_code != 200:
                log("OpenRouter всё ещё недоступна, переключаемся на GitHub")
                return send_to_github(payload)
        if r.status_code != 200:
            log(f"OpenRouter ошибка: {r.status_code}")
            return send_to_github(payload)
        response_text = r.json()["choices"][0]["message"]["content"]
        log("Успешный ответ от OpenRouter")
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not match:
            log("Не найден JSON, переключаемся на GitHub")
            return send_to_github(payload)
        json_str = re.sub(r'^```(?:json)?\s*\n?', '', match.group(0), flags=re.MULTILINE)
        json_str = re.sub(r'\n?```\s*$', '', json_str, flags=re.MULTILINE)
        return json.loads(json_str)
    except Exception as e:
        log(f"OpenRouter ошибка: {e}")
        return send_to_github(payload)

def send_to_github(payload):
    if not GITHUB_TOKEN:
        log("GITHUB_TOKEN не задан, переключаемся на Groq")
        return send_to_groq(payload)
    try:
        log(f"Пробуем GitHub Models: {GITHUB_MODEL}")
        messages = payload.get("messages", [])
        if not any(msg.get("role") == "system" for msg in messages):
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        github_payload = {
            "model": GITHUB_MODEL,
            "messages": messages,
            "max_tokens": payload.get("max_tokens", 8000),
            "temperature": payload.get("temperature", 0.1)
        }
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        r = requests.post(GITHUB_URL, headers=headers, json=github_payload, timeout=120)
        if r.status_code == 429:
            log("GitHub Models лимит, ждём 5с...")
            time.sleep(5)
            r = requests.post(GITHUB_URL, headers=headers, json=github_payload, timeout=120)
            if r.status_code != 200:
                log("GitHub Models недоступна, переключаемся на Groq")
                return send_to_groq(payload)
        if r.status_code != 200:
            log(f"GitHub Models ошибка: {r.status_code}")
            return send_to_groq(payload)
        response_text = r.json()["choices"][0]["message"]["content"]
        log("Успешный ответ от GitHub Models")
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not match:
            log("Не найден JSON, переключаемся на Groq")
            return send_to_groq(payload)
        json_str = re.sub(r'^```(?:json)?\s*\n?', '', match.group(0), flags=re.MULTILINE)
        json_str = re.sub(r'\n?```\s*$', '', json_str, flags=re.MULTILINE)
        return json.loads(json_str)
    except Exception as e:
        log(f"GitHub Models ошибка: {e}")
        return send_to_groq(payload)

def send_to_groq(payload):
    if not GROQ_API_KEY:
        log("GROQ_API_KEY не задан")
        return None
    try:
        log(f"Пробуем Groq модель: {GROQ_MODEL}")
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        messages = payload.get("messages", [])
        if not any(msg.get("role") == "system" for msg in messages):
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        groq_payload = {
            "model": GROQ_MODEL,
            "messages": messages,
            "max_tokens": payload.get("max_tokens", 8000),
            "temperature": payload.get("temperature", 0.1)
        }
        r = requests.post(GROQ_URL, headers=headers, json=groq_payload, timeout=120)
        if r.status_code == 429:
            log("Groq лимит, ждём 5с...")
            time.sleep(5)
            r = requests.post(GROQ_URL, headers=headers, json=groq_payload, timeout=120)
            if r.status_code != 200:
                log("Groq недоступна")
                return None
        if r.status_code != 200:
            log(f"Groq ошибка: {r.status_code}")
            return None
        response_text = r.json()["choices"][0]["message"]["content"]
        log("Успешный ответ от Groq")
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not match:
            log("Не найден JSON в ответе Groq")
            return None
        json_str = re.sub(r'^```(?:json)?\s*\n?', '', match.group(0), flags=re.MULTILINE)
        json_str = re.sub(r'\n?```\s*$', '', json_str, flags=re.MULTILINE)
        return json.loads(json_str)
    except Exception as e:
        log(f"Groq ошибка: {e}")
        return None

def analyze_with_qwen(full_text, model):
    if not OPENROUTER_API_KEY and not GITHUB_TOKEN and not GROQ_API_KEY:
        log("Нет API-ключей (OpenRouter, GitHub, Groq)")
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
    else:
        log("Анализ не удался, возвращаем nothing_new")
        return {"nothing_new": True, "reason": "Анализ не удался"}
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
        return [f"## ⚠️ Обновление: **{title}**\n\nНе удалось загрузить текст статьи.\n📎 [Ссылка]({link})"], []

    analysis = analyze_with_qwen(full_text, model)
    if analysis is None:
        return [f"## ⚠️ Обновление: **{title}**\n\nНе удалось обработать статью.\n📎 [Ссылка]({link})"], []

    if analysis.get("nothing_new"):
        reason = analysis.get("reason", "Изменений не найдено")
        log(f"Нет новых изменений: {reason}")
        return [f"## ℹ️ Обновление: **{title}**\n\n{reason}\n📎 [Ссылка]({link})"], []

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

    log(f"Новая новость: {game}|{title}")

    model = MODELS.get(game, OR_MODEL)
    text_messages, image_urls = format_message(game, title, link, raw_text, model)
    if not text_messages:
        log(f"Нет сообщений для отправки ({title})")
        mark_processed(game, title)
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
                log(f"Ошибка Discord: {r.status_code}")
        except Exception as e:
            log(f"Ошибка отправки в Discord: {e}")
        time.sleep(2)

    if image_urls and game == "warthunder":
        log(f"Отправка {len(image_urls)} изображений для {game}")
        for idx, url in enumerate(image_urls[:10]):
            embed = {
                "title": "📷 Изображение из новости",
                "image": {"url": url},
                "color": 0x00ff00,
                "footer": {"text": f"Изображение {idx+1} из {len(image_urls)}"}
            }
            payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
            try:
                r = requests.post(webhook, json=payload)
                if r.status_code == 204:
                    log(f"Отправлено изображение {idx+1} (embed)")
                else:
                    log(f"Не удалось отправить embed, отправляем ссылкой")
                    link_payload = {"content": f"📷 <{url}>", "allowed_mentions": {"parse": []}}
                    requests.post(webhook, json=link_payload)
            except Exception as e:
                log(f"Ошибка отправки изображения {idx+1}: {e}")
                try:
                    link_payload = {"content": f"📷 <{url}>", "allowed_mentions": {"parse": []}}
                    requests.post(webhook, json=link_payload)
                except:
                    pass
            time.sleep(1)
        if len(image_urls) > 10:
            extra_urls = image_urls[10:]
            links_text = "📷 **Дополнительные изображения:**\n" + "\n".join(extra_urls[:10])
            payload = {"content": links_text, "allowed_mentions": {"parse": []}}
            try:
                r = requests.post(webhook, json=payload)
                if r.status_code == 204:
                    log("Отправлены дополнительные ссылки")
                else:
                    log(f"Ошибка отправки дополнительных ссылок: {r.status_code}")
            except Exception as e:
                log(f"Ошибка отправки дополнительных ссылок: {e}")

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
            log(f"RSS для {game} пуст")
            if game == "sbox":
                log("Пробуем получить новости sbox через HTML...")
                news = fetch_sbox_news_from_html()
                if news:
                    title, link = news.get("title"), news.get("link")
                    log(f"Обработка {game} (из HTML): {title}")
                    send_to_discord(game, title, link, "")
                else:
                    log(f"Не удалось получить новости для {game}")
            elif game == "warthunder":
                log("Пробуем получить новости War Thunder через HTML...")
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

        max_entries = min(2, len(entries))
        for i in range(max_entries):
            entry = entries[i]
            if game != "garrysmod":
                if is_old(entry.get("published_parsed")):
                    log(f"Новость старая (пропускаем): {entry.get('title')}")
                    continue
            title = entry.get("title", "Без названия")
            link = entry.get("link", "")
            raw = entry.get("summary", entry.get("description", ""))
            log(f"Обработка {game} (из RSS, #{i+1}): {title}")
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
