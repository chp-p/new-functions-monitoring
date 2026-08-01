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

# Бесплатный API для гороскопов (не требует ключа)
HOROSCOPE_API_URL = "https://horoscope-app-api.vercel.app/api/v1/get-horoscope/daily"

def fetch_horoscope_from_api(sign_key):
    """Получает прогноз через бесплатный API."""
    sign_name = SIGNS.get(sign_key, sign_key)
    try:
        # API ожидает знак на английском
        params = {"sign": sign_key, "day": "TODAY"}
        print(f"Запрос к API для {sign_name}...")
        r = requests.get(HOROSCOPE_API_URL, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data.get("success") and data.get("data"):
                horoscope_text = data["data"].get("horoscope_data", "")
                if horoscope_text:
                    print(f"✅ Получен прогноз для {sign_name}")
                    return horoscope_text
        print(f"❌ Ошибка API для {sign_name}: {r.status_code}")
        return None
    except Exception as e:
        print(f"❌ Исключение при запросе API для {sign_name}: {e}")
        return None

def collect_all_horoscopes():
    """Собирает прогнозы для всех знаков через API."""
    results = {}
    for sign_key in SIGNS:
        results[sign_key] = {}
        text = fetch_horoscope_from_api(sign_key)
        results[sign_key]["API"] = text if text else None
        time.sleep(1)  # пауза между запросами
    return results

# ---------- ГЕНЕРАЦИЯ СТАТИСТИКИ ПО ЗНАКАМ (через ИИ) ----------
# Промпт с явным указанием языка — РУССКИЙ
SYSTEM_PROMPT_SIGN = """Ты — астрологический консультант. Ты ОБЯЗАН отвечать только на РУССКОМ языке.
На основе прогнозов для знака {sign_name} дай краткий, ёмкий анализ (2–3 предложения) по пунктам:
- взаимоотношения с партнёром / окружающими;
- любовная тяга, романтические настроения;
- стоит ли активно контактировать с новыми людьми или лучше побыть в одиночестве.

ОТВЕЧАЙ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ. Без лишней воды, только по делу."""

def _call_ai_for_summary(payload):
    """Отправляет запрос к ИИ (OpenRouter -> GitHub -> Groq)."""
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
    """Генерирует анализ для каждого знака через ИИ."""
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
        user_prompt = f"Прогнозы для {sign_name}:\n\n{combined}\n\nДай краткий анализ на РУССКОМ языке."
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

    print("Начинаем сбор астропрогнозов через API...")
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

# ---------- ОТЛАДОЧНЫЙ ЭНДПОИНТ ----------
@app.route("/debug/<sign>")
def debug(sign):
    if sign not in SIGNS:
        return "Неверный знак", 400
    text = fetch_horoscope_from_api(sign)
    return json.dumps({
        "sign": sign,
        "name": SIGNS[sign],
        "horoscope": text
    }, ensure_ascii=False, indent=2)

# ---------- FLASK ----------
@app.route("/")
def home():
    return "Horoscope bot is running (API version)"

@app.route("/send_horoscopes")
def send_horoscopes_endpoint():
    send_horoscopes()
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10001))
    app.run(host="0.0.0.0", port=port)
