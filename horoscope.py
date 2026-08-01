import os
import sys
import time
import datetime
import requests
import re
import json
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

DISCORD_WEBHOOK_HOROSCOPE = os.environ.get("DISCORD_WEBHOOK_HOROSCOPE", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
supabase: Client = None
SUPABASE_ENABLED = False

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        supabase.table("horoscope_sent").select("*").limit(1).execute()
        SUPABASE_ENABLED = True
        print("✅ Supabase подключен (horoscope).")
    except Exception as e:
        print(f"⚠️ Ошибка Supabase: {e}")
        SUPABASE_ENABLED = False

# ---------- ФУНКЦИИ БАЗЫ ДАННЫХ ----------
def is_horoscope_sent_today():
    today = datetime.date.today().isoformat()
    if SUPABASE_ENABLED and supabase:
        try:
            resp = supabase.table("horoscope_sent").select("*").eq("date", today).execute()
            return len(resp.data) > 0
        except Exception as e:
            print(f"Ошибка Supabase: {e}")
    try:
        cache_file = "/tmp/horoscope_sent.txt"
        if not os.access("/tmp", os.W_OK):
            cache_file = "horoscope_sent.txt"
        if os.path.exists(cache_file):
            with open(cache_file, "r") as f:
                return today in f.read()
    except:
        pass
    return False

def mark_horoscope_sent_today():
    today = datetime.date.today().isoformat()
    if SUPABASE_ENABLED and supabase:
        try:
            supabase.table("horoscope_sent").upsert({"date": today, "sent": True}, on_conflict="date").execute()
            print(f"Записано в Supabase: {today}")
            return
        except Exception as e:
            print(f"Ошибка записи: {e}")
    try:
        cache_file = "/tmp/horoscope_sent.txt"
        if not os.access("/tmp", os.W_OK):
            cache_file = "horoscope_sent.txt"
        with open(cache_file, "a") as f:
            f.write(f"{today}\n")
        print(f"Записано в кеш: {today}")
    except Exception as e:
        print(f"Ошибка записи в кеш: {e}")

# ---------- ПАРСИНГ ПРОГНОЗОВ ----------
SIGNS = {
    "aries": "Овен",
    "taurus": "Телец",
    "gemini": "Близнецы",
    "cancer": "Рак",
    "leo": "Лев",
    "virgo": "Дева",
    "libra": "Весы",
    "scorpio": "Скорпион",
    "sagittarius": "Стрелец",
    "capricorn": "Козерог",
    "aquarius": "Водолей",
    "pisces": "Рыбы"
}

# Новые источники с несколькими вариантами URL
SOURCES = [
    {
        "name": "Mail.ru",
        "urls": [
            "https://horo.mail.ru/prediction/{sign}/today/",
            "https://horo.mail.ru/prediction/{sign}/"
        ],
        "parser": lambda soup: (
            soup.find("div", class_="prediction-text") or
            soup.find("div", class_="article__text") or
            soup.find("div", class_=re.compile(r"prediction|text|content")) or
            soup.find("article") or
            soup.find("div", class_="horoscope-text")
        )
    },
    {
        "name": "Ignio.com",
        "urls": [
            "https://www.ignio.com/r/daily/{sign}.html",
            "https://www.ignio.com/r/weekly/{sign}.html"
        ],
        "parser": lambda soup: (
            soup.find("div", class_="horoscope-content") or
            soup.find("div", class_="daily-horoscope") or
            soup.find("div", class_=re.compile(r"horoscope|content")) or
            soup.find("article")
        )
    },
    {
        "name": "Astro.ru",
        "urls": [
            "https://astro.ru/horoscope/{sign}/today/",
            "https://astro.ru/horoscope/{sign}/"
        ],
        "parser": lambda soup: (
            soup.find("div", class_="horoscope-text") or
            soup.find("div", class_="content") or
            soup.find("article")
        )
    }
]

def fetch_horoscope(sign_key, source):
    sign_name = SIGNS.get(sign_key, sign_key)
    for url_template in source["urls"]:
        url = url_template.format(sign=sign_key)
        try:
            print(f"Пробуем {source['name']}: {url}")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3"
            }
            r = requests.get(url, timeout=15, headers=headers)
            if r.status_code != 200:
                print(f"Ошибка {r.status_code} для {url}, пробуем следующий")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            # Ищем по парсеру
            content_block = source["parser"](soup)
            if content_block:
                text = content_block.get_text(separator="\n", strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 50:
                    return text

            # Если не нашли, ищем любой блок с большим текстом и ключевыми словами
            for div in soup.find_all("div"):
                text = div.get_text(separator="\n", strip=True)
                if len(text) > 200 and any(word in text.lower() for word in ["день", "сегодня", "завтра", "звезды", "гороскоп"]):
                    return text[:500] + "..." if len(text) > 500 else text

            # fallback – body
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 100:
                    return text[:500] + "..." if len(text) > 500 else text

            print(f"Не удалось извлечь текст из {url}")
        except Exception as e:
            print(f"Ошибка при запросе {url}: {e}")
    return None

def collect_all_horoscopes():
    results = {}
    for sign_key in SIGNS:
        results[sign_key] = {}
        for source in SOURCES:
            text = fetch_horoscope(sign_key, source)
            results[sign_key][source["name"]] = text if text else None
            time.sleep(1.5)
    return results

# ---------- ОСТАЛЬНЫЕ ФУНКЦИИ (statistics, formatting, discord, flask) ----------
# Они остаются без изменений из предыдущего кода, я их включу для полноты, но в целях экономии места я их пропущу, так как они уже были.

# ... (вставьте сюда все функции: generate_sign_statistics, _call_ai_for_summary, build_horoscope_message, build_statistics_message, send_to_discord_horoscope, send_horoscopes, debug, home, send_horoscopes_endpoint)

# ---------- ОТЛАДОЧНЫЙ ЭНДПОИНТ ----------
@app.route("/debug/<sign>")
def debug(sign):
    if sign not in SIGNS:
        return "Неверный знак", 400
    results = {}
    for source in SOURCES:
        results[source["name"]] = {}
        for url_template in source["urls"]:
            url = url_template.format(sign=sign)
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "ru-RU,ru;q=0.8"
                }
                r = requests.get(url, timeout=10, headers=headers)
                results[source["name"]][url] = {
                    "status": r.status_code,
                    "preview": r.text[:1000] if r.status_code == 200 else None
                }
            except Exception as e:
                results[source["name"]][url] = {"error": str(e)}
    return json.dumps(results, ensure_ascii=False, indent=2)

# ---------- FLASK ----------
@app.route("/")
def home():
    return "Horoscope bot is running"

@app.route("/send_horoscopes")
def send_horoscopes_endpoint():
    send_horoscopes()
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10001))
    app.run(host="0.0.0.0", port=port)
