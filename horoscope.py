import os
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
        print("✅ Supabase подключен.")
    except Exception as e:
        print(f"⚠️ Ошибка Supabase: {e} (используем файловый кеш)")
        SUPABASE_ENABLED = False

# ---------- КЕШ ----------
def is_horoscope_sent_today():
    today = datetime.date.today().isoformat()
    if SUPABASE_ENABLED and supabase:
        try:
            resp = supabase.table("horoscope_sent").select("*").eq("date", today).execute()
            return len(resp.data) > 0
        except:
            pass
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
            print(f"✅ Записано в Supabase: {today}")
            return
        except:
            pass
    try:
        cache_file = "/tmp/horoscope_sent.txt"
        if not os.access("/tmp", os.W_OK):
            cache_file = "horoscope_sent.txt"
        with open(cache_file, "a") as f:
            f.write(f"{today}\n")
        print(f"✅ Записано в файловый кеш: {today}")
    except:
        pass

# ---------- ПАРСИНГ ----------
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

# Сопоставление для русских названий в URL
SIGN_RU = {k: v.lower() for k, v in SIGNS.items()}  # "taurus": "телец"

# Три надёжных источника
SOURCES = [
    {
        "name": "ignio.com",
        "url": "https://www.ignio.com/r/daily/{sign}.html",
        "parser": lambda soup: (
            soup.find("div", class_="horoscope-content") or
            soup.find("div", class_="daily-horoscope") or
            soup.find("div", class_="content") or
            soup.find("article")
        )
    },
    {
        "name": "astro.ru",
        "url": "https://astro.ru/horoscope/{sign}/today/",
        "parser": lambda soup: (
            soup.find("div", class_="horoscope-text") or
            soup.find("div", class_="content") or
            soup.find("article")
        )
    },
    {
        "name": "1001goroskop",
        "url": "https://1001goroskop.ru/daily/{sign_ru}.html",
        "parser": lambda soup: (
            soup.find("div", class_="text") or
            soup.find("div", class_="content") or
            soup.find("article")
        )
    }
]

def fetch_horoscope(sign_key):
    """Парсит прогнозы со всех источников и возвращает словарь {source_name: text}"""
    results = {}
    for source in SOURCES:
        # Подставляем нужный формат знака
        if "sign_ru" in source["url"]:
            sign_part = SIGN_RU[sign_key]
        else:
            sign_part = sign_key
        url = source["url"].format(sign=sign_part, sign_ru=sign_part)
        try:
            print(f"🔍 Пробуем {source['name']} для {SIGNS[sign_key]}: {url}")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "ru-RU,ru;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
            }
            r = requests.get(url, timeout=15, headers=headers)
            if r.status_code != 200:
                print(f"   ❌ HTTP {r.status_code}")
                results[source["name"]] = None
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # Удаляем мусор
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            # Ищем блок по парсеру
            block = source["parser"](soup)
            text = None
            if block:
                text = block.get_text(separator="\n", strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                text = re.sub(r'Подпишись.*?\.', '', text, flags=re.IGNORECASE)
                text = re.sub(r'Реклама.*?\.', '', text, flags=re.IGNORECASE)
                if len(text) > 50:
                    print(f"   ✅ Найден текст ({len(text)} симв.)")
                    results[source["name"]] = text
                    continue
            # Fallback: ищем любой div с большим текстом и ключевыми словами
            for div in soup.find_all("div"):
                txt = div.get_text(separator="\n", strip=True)
                if len(txt) > 200 and any(w in txt.lower() for w in ["сегодня", "день", "звезды", "удачи", "совет"]):
                    txt = re.sub(r'\s+', ' ', txt).strip()
                    if len(txt) > 50:
                        print(f"   ✅ Fallback текст ({len(txt)} симв.)")
                        results[source["name"]] = txt[:500] + "..." if len(txt) > 500 else txt
                        break
            if source["name"] not in results:
                results[source["name"]] = None
                print("   ❌ Не удалось извлечь текст")
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
            results[source["name"]] = None
        time.sleep(1)  # пауза между источниками
    return results

def collect_all_horoscopes():
    results = {}
    for sign_key in SIGNS:
        print(f"\n📡 Обработка знака {SIGNS[sign_key]}...")
        results[sign_key] = fetch_horoscope(sign_key)
        time.sleep(2)  # пауза между знаками
    return results

# ---------- ГЕНЕРАЦИЯ СТАТИСТИКИ ЧЕРЕЗ ИИ ----------
SYSTEM_PROMPT = """Ты — астролог-консультант. ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ.
На основе прогнозов для знака {sign} дай краткий (2-3 предложения) анализ:
- отношения с партнёром / окружающими
- любовная энергетика
- стоит ли сегодня активно общаться или лучше побыть в одиночестве
Без воды, только суть."""

def call_ai(prompt):
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 300,
        "temperature": 0.7
    }
    if OPENROUTER_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
            p = payload.copy()
            p["model"] = OR_MODEL
            r = requests.post(OPENROUTER_URL, headers=headers, json=p, timeout=120)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
        except:
            pass
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
        except:
            pass
    if GROQ_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            p = payload.copy()
            p["model"] = GROQ_MODEL
            r = requests.post(GROQ_URL, headers=headers, json=p, timeout=120)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
        except:
            pass
    return None

def generate_statistics(all_data):
    stats = {}
    for sign_key, sources in all_data.items():
        sign_name = SIGNS[sign_key]
        texts = [t for t in sources.values() if t]
        if not texts:
            stats[sign_key] = "Недостаточно данных."
            continue
        combined = "\n".join(texts)
        if len(combined) > 3000:
            combined = combined[:3000] + "..."
        prompt = f"Знак: {sign_name}\nПрогнозы:\n{combined}\n\nДай краткий анализ на русском."
        answer = call_ai(prompt)
        stats[sign_key] = answer if answer else "Анализ не удался."
        time.sleep(1)
    return stats

# ---------- СООБЩЕНИЯ В DISCORD ----------
def split_text(text):
    chunks = []
    while len(text) > 2000:
        split_at = text.rfind("\n", 0, 2000)
        if split_at == -1:
            split_at = 2000
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    chunks.append(text)
    return chunks

def build_messages(all_data, stats):
    today = datetime.date.today().strftime("%d %B %Y")
    parts1 = [f"🔮 **Астропрогнозы на {today}**\n"]
    for sign_key, sources in all_data.items():
        sign_name = SIGNS[sign_key]
        block = f"**{sign_name}**\n"
        for src, text in sources.items():
            if text:
                short = text[:300] + ("..." if len(text) > 300 else "")
                block += f"• *{src}:* {short}\n"
            else:
                block += f"• *{src}:* (не удалось)\n"
        block += "\n"
        parts1.append(block)
    msg1 = "\n".join(parts1)

    parts2 = [f"📊 **Статистика и рекомендации на {today}**\n"]
    for sign_key, analysis in stats.items():
        sign_name = SIGNS[sign_key]
        parts2.append(f"**{sign_name}** — {analysis}\n")
    msg2 = "\n".join(parts2)

    return split_text(msg1), split_text(msg2)

def send_to_discord(messages):
    if not DISCORD_WEBHOOK_HOROSCOPE:
        print("❌ WEBHOOK не задан!")
        return
    for msg in messages:
        payload = {"content": msg, "allowed_mentions": {"parse": []}}
        try:
            r = requests.post(DISCORD_WEBHOOK_HOROSCOPE, json=payload)
            if r.status_code == 204:
                print("✅ Сообщение отправлено")
            else:
                print(f"❌ Ошибка {r.status_code}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
        time.sleep(1)

# ---------- ОСНОВНАЯ ФУНКЦИЯ ----------
def send_horoscopes():
    if is_horoscope_sent_today():
        print("⏳ Сегодня уже отправлено. Выход.")
        return

    print("🔄 Сбор прогнозов...")
    all_data = collect_all_horoscopes()
    # Проверяем, есть ли хоть один текст
    has_data = any(any(t for t in sources.values()) for sources in all_data.values())
    if not has_data:
        print("❌ Нет данных. Отправка отменена.")
        return

    print("🧠 Генерация статистики через ИИ...")
    stats = generate_statistics(all_data)

    msg1_chunks, msg2_chunks = build_messages(all_data, stats)

    print("📨 Отправка в Discord...")
    send_to_discord(msg1_chunks)
    time.sleep(2)
    send_to_discord(msg2_chunks)

    mark_horoscope_sent_today()
    print("✅ Готово!")

# ---------- FLASK ----------
@app.route("/")
def home():
    return "Horoscope bot is running (final)"

@app.route("/send_horoscopes")
def send():
    send_horoscopes()
    return "OK", 200

@app.route("/debug/<sign>")
def debug(sign):
    if sign not in SIGNS:
        return "Неверный знак", 400
    results = fetch_horoscope(sign)
    return json.dumps({
        "sign": sign,
        "name": SIGNS[sign],
        "sources": results
    }, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10001))
    app.run(host="0.0.0.0", port=port)
