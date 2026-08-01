import os
import time
import datetime
import requests
import json
from flask import Flask
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
        print(f"⚠️ Ошибка Supabase: {e}")

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

# ---------- ПОЛУЧЕНИЕ ПРОГНОЗОВ ЧЕРЕЗ API ----------
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

def fetch_from_api(sign_key):
    """Получает прогноз через бесплатный API (aztro)"""
    try:
        url = "https://aztro.sameerkumar.website/"
        params = {"sign": sign_key, "day": "today"}
        r = requests.post(url, data=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("description"):
                return data["description"]
    except Exception as e:
        print(f"API aztro ошибка: {e}")
    # Резервный API
    try:
        url = f"https://horoscope-api.vercel.app/api/v1/get-horoscope/daily?sign={sign_key}&day=today"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("data", {}).get("horoscope_data"):
                return data["data"]["horoscope_data"]
    except Exception as e:
        print(f"API horoscope-api ошибка: {e}")
    return None

def collect_all_horoscopes():
    results = {}
    for sign_key in SIGNS:
        print(f"📡 Запрос API для {SIGNS[sign_key]}...")
        text = fetch_from_api(sign_key)
        results[sign_key] = {"API": text}  # только один источник
        time.sleep(1.5)
    return results

# ---------- ГЕНЕРАЦИЯ СТАТИСТИКИ ЧЕРЕЗ ИИ ----------
SYSTEM_PROMPT = """Ты — астролог-консультант. ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ.
На основе прогноза для знака {sign} (на английском) дай краткий (2-3 предложения) анализ на русском:
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
        text = sources.get("API")
        if not text:
            stats[sign_key] = "Прогноз не получен."
            continue
        prompt = f"Знак: {sign_name}\nПрогноз (на английском): {text}\n\nПереведи на русский и дай краткий анализ."
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
    # Первое сообщение: прогнозы (оригинал на английском)
    parts1 = [f"🔮 **Астропрогнозы на {today}**\n"]
    for sign_key, sources in all_data.items():
        sign_name = SIGNS[sign_key]
        text = sources.get("API")
        if text:
            short = text[:300] + ("..." if len(text) > 300 else "")
            parts1.append(f"**{sign_name}**: {short}\n")
        else:
            parts1.append(f"**{sign_name}**: (не удалось получить)\n")
    msg1 = "\n".join(parts1)

    # Второе сообщение: статистика (перевод + анализ на русском)
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

    print("🔄 Сбор прогнозов через API...")
    all_data = collect_all_horoscopes()
    # Проверяем, есть ли хоть один текст
    has_data = any(sources.get("API") for sources in all_data.values())
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
    return "Horoscope bot is running (API version)"

@app.route("/send_horoscopes")
def send():
    send_horoscopes()
    return "OK", 200

@app.route("/debug/<sign>")
def debug(sign):
    if sign not in SIGNS:
        return "Неверный знак", 400
    text = fetch_from_api(sign)
    return json.dumps({
        "sign": sign,
        "name": SIGNS[sign],
        "horoscope": text
    }, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10001))
    app.run(host="0.0.0.0", port=port)
