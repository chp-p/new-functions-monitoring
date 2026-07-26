import requests
import hashlib
import re
import feedparser
from flask import Flask

app = Flask(__name__)

# ====== НАСТРОЙКИ ======

WEBHOOKS = {
    "rust": "https://discord.com/api/webhooks/1530915163325075467/s0hhJdWkYb4eGEhfPF7GPYEpsFGTLXD5rjiwdFq8eMsHF8ndf_NRMbwaIvqkxOkCdjy4",
    "garrysmod": "https://discord.com/api/webhooks/1530915445224378398/3J8-YM7OjeqxaUJaL1LJobQD9O0KnZjj8h-PjP4IYTIjUQXkXRQwtOhJTisZ52jQUSEk",
    "unturned": "https://discord.com/api/webhooks/1530915598844825620/tMPKR9KZvEKCyGNyXDbQEmshF5xzs_MS5WbnTn6bwC6QkNBALY9sFLAZmeJP4SmuOPBN",
    "sbox": "https://discord.com/api/webhooks/1530916231870156903/Vn_-MQHI9kMk1qTpXYbE7z4qItcRQA9uaXJMGxZV_2ad-iai7g6YDPdq6JhzrQZWMowq"
}

RSS_FEEDS = {
    "rust": "https://rust.facepunch.com/rss",
    "garrysmod": "https://steamcommunity.com/app/4000/rss/",
    "unturned": "https://steamcommunity.com/app/304930/rss/",
    "sbox": "https://sbox.facepunch.com/rss/"
}

GAME_NAMES = {
    "rust": "Rust",
    "garrysmod": "Garry's Mod",
    "unturned": "Unturned",
    "sbox": "s&box"
}

# ====== КОНЕЦ НАСТРОЕК ======

seen_entries = {}


def get_entry_hash(entry):
    """Уникальный хеш для каждой записи"""
    content = entry.get("title", "") + entry.get("link", "")
    return hashlib.md5(content.encode()).hexdigest()


def get_emoji_for_update(title):
    """Подбирает эмодзи под тип обновления"""
    title_lower = title.lower()
    if "weapon" in title_lower or "оружие" in title_lower or "gun" in title_lower:
        return "🔫"
    elif "vehicle" in title_lower or "транспорт" in title_lower or "car" in title_lower:
        return "🚗"
    elif "building" in title_lower or "постройк" in title_lower or "base" in title_lower:
        return "🏗️"
    elif "fix" in title_lower or "исправлени" in title_lower or "hotfix" in title_lower or "bug" in title_lower:
        return "🛠️"
    elif "event" in title_lower or "событие" in title_lower or "ивент" in title_lower:
        return "🎉"
    elif "map" in title_lower or "карт" in title_lower or "world" in title_lower:
        return "🗺️"
    elif "ui" in title_lower or "интерфейс" in title_lower or "hud" in title_lower:
        return "🖥️"
    elif "skin" in title_lower or "скин" in title_lower or "cosmetic" in title_lower:
        return "🎨"
    elif "sound" in title_lower or "аудио" in title_lower or "music" in title_lower:
        return "🔊"
    elif "performance" in title_lower or "производительност" in title_lower or "optimiz" in title_lower:
        return "⚡"
    elif "api" in title_lower or "hook" in title_lower or "моддинг" in title_lower or "modding" in title_lower or "oxide" in title_lower or "carbon" in title_lower:
        return "🧩"
    elif "halloween" in title_lower or "хэллоуин" in title_lower:
        return "🎃"
    elif "christmas" in title_lower or "рождеств" in title_lower or "новым годом" in title_lower or "xmas" in title_lower:
        return "🎄"
    elif "summer" in title_lower or "лет" in title_lower:
        return "☀️"
    elif "winter" in title_lower or "зим" in title_lower:
        return "❄️"
    elif "new" in title_lower or "добавлен" in title_lower or "added" in title_lower:
        return "✨"
    elif "update" in title_lower or "обновление" in title_lower or "patch" in title_lower:
        return "📦"
    else:
        return "📦"


def extract_version(title):
    """Пытается вытащить номер версии из заголовка"""
    patterns = [
        r'(\d+\.\d+\.\d+\.\d+)',
        r'(\d+\.\d+\.\d+)',
        r'(\d+\.\d+)',
        r'[Uu]pdate\s*(\d+)',
        r'[Pp]atch\s*(\d+)',
        r'#(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            return match.group(0) if match.lastindex is None else match.group(1)
    return ""


def format_message(game, title, link, summary):
    """Форматирует красивое сообщение в Discord"""
    emoji = get_emoji_for_update(title)
    version = extract_version(title)
    game_name = GAME_NAMES.get(game, game)

    title_line = f"{emoji} Обновление: **{title}**"
    if version:
        title_line += f" — `{version}`"

    message = (
        f"## {title_line}\n\n"
        f"### 🧩 Новые функции для разработчиков/моддеров:\n"
        f"🔹 *Информация появится позже. Напиши /разбор для анализа.*\n\n"
        f"### 📦 Новые предметы:\n"
        f"🔹 Не добавлены (или ожидайте информацию)\n\n"
        f"### 🔫 Новое оружие:\n"
        f"🔹 Не добавлено\n\n"
        f"### 🗺️ Новая карта / монументы:\n"
        f"🔹 Без изменений\n\n"
        f"📎 [Полный патч-ноут]({link}) | 🎮 {game_name}"
    )
    return message


def send_to_discord(game, title, link, summary):
    """Отправляет сообщение в Discord через вебхук"""
    if game not in WEBHOOKS:
        return

    webhook_url = WEBHOOKS[game]
    content = format_message(game, title, link, summary)

    payload = {
        "content": content,
        "allowed_mentions": {"parse": []}
    }

    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code == 204:
            print(f"✅ Отправлено в {game}: {title}")
        else:
            print(f"⚠️ Ошибка {response.status_code} для {game}: {response.text}")
    except Exception as e:
        print(f"❌ Ошибка отправки в {game}: {e}")


def check_feeds():
    """Проверяет все RSS-ленты на новые записи"""
    for game, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                continue

            if game not in seen_entries:
                seen_entries[game] = set()

            new_count = 0
            for entry in feed.entries[:5]:
                h = get_entry_hash(entry)
                if h not in seen_entries[game]:
                    seen_entries[game].add(h)
                    send_to_discord(
                        game,
                        entry.get("title", "Без названия"),
                        entry.get("link", ""),
                        entry.get("summary", entry.get("description", ""))
                    )
                    new_count += 1

            if new_count > 0:
                print(f"📬 {game}: {new_count} новых постов")
        except Exception as e:
            print(f"❌ Ошибка проверки {game}: {e}")


@app.route("/")
def home():
    return "RSS Monitor running"


@app.route("/check")
def check():
    check_feeds()
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
