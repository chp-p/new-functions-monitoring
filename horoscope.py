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
        # Проверим таблицу horoscope_sent
        try:
            supabase.table("horoscope_sent").select("*").limit(1).execute()
        except Exception:
            print("⚠️ Таблица horoscope_sent не найдена. Создайте её.")
        SUPABASE_ENABLED = True
        print("✅ Supabase подключен (horoscope).")
    except Exception as e:
        print(f"⚠️ Ошибка подключения к Supabase: {e}")
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
    # Файловый кеш
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

# ------------------------------------------------
# Улучшенные источники с гибкими парсерами
# ------------------------------------------------
SOURCES = [
    {
        "name": "7Дней.ру",
        "url_template": "https://www.7days.ru/horoscope/{sign}/",  # убрал /today/
        "parser": lambda soup: (
            soup.find("div", class_="horoscope-text") or
            soup.find("div", class_="article-text") or
            soup.find("div", class_="content") or
            soup.find("div", class_=re.compile(r"horoscope|text|content")) or
            soup.find("article")
        )
    },
    {
        "name": "Astroscope.ru",
        "url_template": "https://astroscope.ru/horoscope/{sign}/",
        "parser": lambda soup: (
            soup.find("div", class_="horoscope-text") or
            soup.find("div", class_="description") or
            soup.find("div", class_=re.compile(r"horoscope|text|content|description")) or
            soup.find("article")
        )
    },
    {
        "name": "ktv-ray.ru",
        "url_template": "https://ktv-ray.ru/horoscope/{sign}/",
        "parser": lambda soup: (
            soup.find("div", class_="horoscope-content") or
            soup.find("div", class_="entry-content") or
            soup.find("div", class_="text") or
            soup.find("div", class_=re.compile(r"horoscope|content|text|entry")) or
            soup.find("article")
        )
    }
]

def fetch_horoscope(sign_key, source):
    """Возвращает текст прогноза или None."""
    sign_name = SIGNS.get(sign_key, sign_key)
    url = source["url_template"].format(sign=sign_key)
    try:
        print(f"Загрузка {source['name']} для {sign_name}: {url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3"
        }
        r = requests.get(url, timeout=15, headers=headers)
        if r.status_code != 200:
            print(f"Ошибка {r.status_code} для {url}")
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # 1. Ищем по заданному парсеру (классы)
        content_block = source["parser"](soup)
        if content_block:
            text = content_block.get_text(separator="\n", strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 50:
                return text

        # 2. Если не нашли, ищем любой div с большим количеством текста
        for div in soup.find_all("div"):
            text = div.get_text(separator="\n", strip=True)
            if len(text) > 200:
                # Проверим, что текст похож на прогноз (содержит слова "день", "сегодня" и т.п.)
                if any(word in text.lower() for word in ["день", "сегодня", "завтра", "звезды", "гороскоп"]):
                    return text[:500] + "..." if len(text) > 500 else text

        # 3. Последний fallback – тело страницы
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n", strip=True)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 100:
                return text[:500] + "..." if len(text) > 500 else text

        return None
    except Exception as e:
        print(f"Ошибка парсинга {source['name']} для {sign_name}: {e}")
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

# ---------- ГЕНЕРАЦИЯ СТАТИСТИКИ ПО КАЖДОМУ ЗНАКУ ----------
SYSTEM_PROMPT_SIGN = """Ты — астрологический консультант. На основе прогнозов для знака {sign_name} дай краткий, ёмкий анализ (2–3 предложения) по следующим пунктам:
- взаимоотношения с партнёром / окружающими;
- любовная тяга, романтические настроения;
- стоит ли активно контактировать с новыми людьми или лучше побыть в одиночестве.

Ответ должен быть на русском языке, без лишней воды, только по делу."""

def _call_ai_for_summary(payload):
    if OPENROUTER_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
            p = payload.copy()
            p["model"] = OR_MODEL
            r = requests.post(OPENROUTER_URL, headers=headers, json=p, timeout=120)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            print(f"OpenRouter ошибка {r.status_code}")
        except Exception as e:
            print(f"OpenRouter исключение: {e}")

    if GITHUB_TOKEN:
        try:
            headers = {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"
            }
            p = payload.copy()
            p["model"] = GITHUB_MODEL
            r = requests.post(GITHUB_URL, headers=headers, json=p, timeout=120)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            print(f"GitHub ошибка {r.status_code}")
        except Exception as e:
            print(f"GitHub исключение: {e}")

    if GROQ_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            p = payload.copy()
            p["model"] = GROQ_MODEL
            r = requests.post(GROQ_URL, headers=headers, json=p, timeout=120)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            print(f"Groq ошибка {r.status_code}")
        except Exception as e:
            print(f"Groq исключение: {e}")

    return None

def generate_sign_statistics(all_data):
    stats = {}
    for sign_key, sources in all_data.items():
        sign_name = SIGNS[sign_key]
        texts = [text for text in sources.values() if text]
        if not texts:
            stats[sign_key] = "Недостаточно данных для анализа."
            continue
        combined = "\n".join(texts)
        if len(combined) > 3000:
            combined = combined[:3000] + "..."
        user_prompt = f"Прогнозы для {sign_name}:\n\n{combined}\n\nДай краткий анализ."
        payload = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_SIGN.format(sign_name=sign_name)},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 300,
            "temperature": 0.7
        }
        answer = _call_ai_for_summary(payload)
        stats[sign_key] = answer if answer else "Анализ не удался."
        time.sleep(1)
    return stats

# ---------- ФОРМИРОВАНИЕ СООБЩЕНИЙ ДЛЯ DISCORD ----------
def build_horoscope_message(all_data):
    today = datetime.date.today().strftime("%d %B %Y")
    header = f"🔮 **Астропрогнозы на {today}**\n\n"
    parts = [header]
    for sign_key, sources in all_data.items():
        sign_name = SIGNS[sign_key]
        sign_block = f"**{sign_name}**\n"
        for src, text in sources.items():
            if text:
                # Обрезаем до 300 символов
                short = text[:300] + ("..." if len(text) > 300 else "")
                sign_block += f"• *{src}:* {short}\n"
            else:
                sign_block += f"• *{src}:* (не удалось получить)\n"
        sign_block += "\n"
        parts.append(sign_block)
    full = "\n".join(parts)
    messages = []
    while len(full) > 2000:
        split_at = full.rfind("\n\n", 0, 2000)
        if split_at == -1:
            split_at = 2000
        messages.append(full[:split_at])
        full = full[split_at:].lstrip()
    if full:
        messages.append(full)
    return messages

def build_statistics_message(stats):
    today = datetime.date.today().strftime("%d %B %Y")
    header = f"📊 **Статистика и рекомендации на {today}**\n\n"
    parts = [header]
    for sign_key, analysis in stats.items():
        sign_name = SIGNS[sign_key]
        parts.append(f"**{sign_name}** — {analysis}\n")
    full = "\n".join(parts)
    messages = []
    while len(full) > 2000:
        split_at = full.rfind("\n\n", 0, 2000)
        if split_at == -1:
            split_at = 2000
        messages.append(full[:split_at])
        full = full[split_at:].lstrip()
    if full:
        messages.append(full)
    return messages

def send_to_discord_horoscope(messages):
    if not DISCORD_WEBHOOK_HOROSCOPE:
        print("DISCORD_WEBHOOK_HOROSCOPE не задан!")
        return
    for msg in messages:
        payload = {"content": msg, "allowed_mentions": {"parse": []}}
        try:
            r = requests.post(DISCORD_WEBHOOK_HOROSCOPE, json=payload)
            if r.status_code == 204:
                print("Сообщение отправлено в Discord")
            else:
                print(f"Ошибка Discord: {r.status_code} - {r.text}")
        except Exception as e:
            print(f"Ошибка отправки: {e}")
        time.sleep(1)

# ---------- ГЛАВНАЯ ФУНКЦИЯ ----------
def send_horoscopes():
    if is_horoscope_sent_today():
        print("Астропрогнозы на сегодня уже отправлены. Выход.")
        return

    print("Начинаем сбор астропрогнозов...")
    all_data = collect_all_horoscopes()
    if not all_data:
        print("Не удалось собрать прогнозы.")
        return

    print("Генерируем статистику по знакам...")
    stats = generate_sign_statistics(all_data)

    msg1 = build_horoscope_message(all_data)
    msg2 = build_statistics_message(stats)

    print("Отправляем прогнозы...")
    send_to_discord_horoscope(msg1)
    time.sleep(2)
    print("Отправляем статистику...")
    send_to_discord_horoscope(msg2)

    mark_horoscope_sent_today()
    print("Астропрогнозы и статистика отправлены!")

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
