from telethon import TelegramClient, events
from datetime import datetime, timedelta
import json, os, asyncio
import aiohttp

TIME_OFFSET = 2


# ---------- Настройки ----------
api_id =           # твой api_id
api_hash = ""    # твой api_hash
session_name = "session"


API_URL = "https://tgclientforlogger.mr-grids.workers.dev/"

# ID группы для уведомлений
NOTIFY_GROUP = -5140405534  # замените на свой ID или username

# TTL
TEXT_TTL_DAYS = 5
MEDIA_TTL_DAYS = 2

# Папки
BASE = "data"
MEDIA = f"{BASE}/media"
MSG = f"{BASE}/messages"

os.makedirs(MEDIA, exist_ok=True)
os.makedirs(MSG, exist_ok=True)

# ---------- Клиент ----------
client = TelegramClient(session_name, api_id, api_hash)

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def is_expired(file_time, ttl_days):
    return datetime.now() - file_time > timedelta(days=ttl_days)

# ---------- Сохраняем новые сообщения ----------
@client.on(events.NewMessage)
async def save_message(event):
    msg = event.message
    sender = await msg.get_sender()
    chat = await event.get_chat()

    msg_type = "text"
    media_path = None

    if msg.photo:
        msg_type = "photo"
        media_path = await msg.download_media(file=MEDIA)
    elif msg.video:
        msg_type = "video"
        media_path = await msg.download_media(file=MEDIA)
    elif msg.voice:
        msg_type = "voice"
        media_path = await msg.download_media(file=MEDIA)
    elif msg.file:
        msg_type = "file"
        media_path = await msg.download_media(file=MEDIA)

    data = {
        "chat_id": msg.chat_id,
        "chat_title": getattr(chat, "title", "ЛС"),
        "message_id": msg.id,
        "type": msg_type,
        "text": msg.text,
        "media": media_path,
         "sent_at": now_local(),
        "sender": {
            "id": msg.sender_id,
            "name": f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip(),
            "username": getattr(sender, "username", None)
        },
        "deleted_at": None
    }

    with open(f"{MSG}/{msg.chat_id}_{msg.id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)



def now_local():
    """Возвращает текущее локальное время как строку"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- Обработка удаления ----------
@client.on(events.MessageDeleted)
async def on_deleted(event):
    # Игнорируем сообщения из группы уведомлений, чтобы не рекурсить
    if event.chat_id == NOTIFY_GROUP:
        return

    for msg_id in event.deleted_ids:
        filename = None
        # Ищем локальный JSON по msg_id
        for file in os.listdir(MSG):
            if file.endswith(f"_{msg_id}.json"):
                filename = os.path.join(MSG, file)
                break

        text = ""
        media_path = None

        if filename and os.path.exists(filename):
            # JSON найден
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["deleted_at"] = now_local()
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            chat_name = data.get("chat_title") or f"[Chat ID {data.get('chat_id')}]"

            text = (
                f"❌ УДАЛЁННОЕ СООБЩЕНИЕ\n\n"
                f"👤 От: {data['sender']['name']} (@{data['sender']['username']})\n"
                f"💬 Чат: {chat_name}\n"
                f"🕒 Отправлено: {data['sent_at']}\n"
                f"🗑 Удалено: {data['deleted_at']}\n"
                f"📎 Тип: {data['type']}\n\n"
                f"{data['text'] or '[без текста]'}"
            )
            media_path = data.get("media")
        else:
            # JSON нет — минимальное уведомление
            text = (
                f"❌ УДАЛЁННОЕ СООБЩЕНИЕ\n\n"
                f"🆔 Message ID: {msg_id}\n"
                f"🗑 Время удаления: {now()}\n"
                f"[Содержание локально отсутствует]"
            )
            media_path = None

        # Отправляем уведомление в группу
        await client.send_message(NOTIFY_GROUP, text)
        if media_path and os.path.exists(media_path):
            await client.send_file(NOTIFY_GROUP, media_path)



def format_time_utc(dt):
    """Преобразует datetime из UTC в локальное (+TIME_OFFSET часов) и возвращает строку."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return (dt + timedelta(hours=TIME_OFFSET)).strftime("%Y-%m-%d %H:%M:%S")


# ---------- Очистка старых сообщений (TTL) ----------
async def cleanup_ttl():
    while True:
        # Тексты
        for file in os.listdir(MSG):
            path = os.path.join(MSG, file)
            if os.path.isfile(path):
                file_time = datetime.fromtimestamp(os.path.getmtime(path))
                if is_expired(file_time, TEXT_TTL_DAYS):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if data.get("type") != "text":
                            continue
                    except:
                        pass
                    os.remove(path)

        # Медиа
        for file in os.listdir(MEDIA):
            path = os.path.join(MEDIA, file)
            if os.path.isfile(path):
                file_time = datetime.fromtimestamp(os.path.getmtime(path))
                if is_expired(file_time, MEDIA_TTL_DAYS):
                    os.remove(path)

        await asyncio.sleep(3600)  # Проверять каждый час

#--------Чек сообщений------------
async def check_text(text: str) -> str:
    """Отправляем текст на API и получаем исправленный вариант"""
    async with aiohttp.ClientSession() as session:
        async with session.post(API_URL, json={"text": text}) as resp:
            if resp.status == 200:
                corrected = await resp.text()  # API возвращает plain text
                return corrected.strip()
            return text

@client.on(events.NewMessage())
async def spellcheck(event):
    msg = event.message

    # проверяем только свои сообщения
    if not msg.out:
        return

    # триггер — текст заканчивается на точку
    if msg.text and msg.text.endswith("."):
        await asyncio.sleep(0.5)  # Telegram должен "подтвердить" сообщение

        # убираем точку перед отправкой на проверку
        text_to_check = msg.text.rstrip(".")

        corrected = await check_text(text_to_check)
        if corrected != text_to_check:
            try:
                # редактируем исходное сообщение на исправленный текст
                await client.edit_message(msg.chat_id, msg.id, corrected)
            except Exception as e:
                print("Не удалось изменить сообщение:", e)


#------------------------------------------------
# ---------- Запуск ----------
async def main():
    asyncio.create_task(cleanup_ttl())  # TTL-очистка в фоне
    await client.start()
    print("Логгер запущен...")
    await client.run_until_disconnected()

asyncio.run(main())
