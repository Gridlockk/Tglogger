from telethon import TelegramClient, events
from datetime import datetime, timedelta
import json, os, asyncio
import aiohttp

TIME_OFFSET = 2


# ---------- Настройки ----------
def load_credentials():
    """Загружает api_id и api_hash из файла config.txt"""
    config_file = "config.txt"

    if not os.path.exists(config_file):
        # Создаем файл с шаблоном, если его нет
        with open(config_file, "w", encoding="utf-8") as f:
            f.write("api_id=YOUR_API_ID\n")
            f.write("api_hash=YOUR_API_HASH\n")
        print(f"Создан файл {config_file}. Заполните его и перезапустите бот.")
        exit(1)

    config = {}
    with open(config_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()

    api_id = config.get("api_id")
    api_hash = config.get("api_hash")

    if not api_id or not api_hash or api_id == "YOUR_API_ID":
        print("Ошибка: Заполните api_id и api_hash в файле config.txt")
        exit(1)

    return int(api_id), api_hash


api_id, api_hash = load_credentials()
session_name = "session"

API_URL = "https://tgclientforlogger.mr-grids.workers.dev/"

# ID группы для уведомлений (по умолчанию - избранное, т.е. "me")
NOTIFY_GROUP_FILE = "data/notify_group.txt"


def get_notify_group():
    """Получает ID группы для уведомлений из файла или возвращает 'me' (избранное)"""
    if os.path.exists(NOTIFY_GROUP_FILE):
        with open(NOTIFY_GROUP_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip()
            if value.lower() == "me":
                return "me"
            try:
                return int(value)
            except:
                return "me"
    return "me"


def set_notify_group(chat_id):
    """Сохраняет ID группы для уведомлений в файл"""
    with open(NOTIFY_GROUP_FILE, "w", encoding="utf-8") as f:
        f.write(str(chat_id))


# TTL
TEXT_TTL_DAYS = 5
MEDIA_TTL_DAYS = 2

# Папки
BASE = "data"
MEDIA = f"{BASE}/media"
MSG = f"{BASE}/messages"
STATS_FILE = f"{BASE}/stats.json"

os.makedirs(MEDIA, exist_ok=True)
os.makedirs(MSG, exist_ok=True)


# ---------- Статистика ----------
def load_stats():
    """Загружает статистику из файла"""
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "messages_saved": 0,
        "media_saved": 0,
        "messages_deleted": 0,
        "last_reset": now_local()
    }


def save_stats(stats):
    """Сохраняет статистику в файл"""
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def increment_stat(key):
    """Увеличивает счетчик статистики"""
    stats = load_stats()
    stats[key] = stats.get(key, 0) + 1
    save_stats(stats)


def get_folder_size(folder_path):
    """Возвращает размер папки в байтах"""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if os.path.exists(filepath):
                total_size += os.path.getsize(filepath)
    return total_size


def format_size(bytes_size):
    """Форматирует размер в человекочитаемый вид"""
    for unit in ['Б', 'КБ', 'МБ', 'ГБ']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} ТБ"


# ---------- Клиент ----------
client = TelegramClient(session_name, api_id, api_hash)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_local():
    """Возвращает текущее локальное время как строку"""
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
        increment_stat("media_saved")
    elif msg.video:
        msg_type = "video"
        media_path = await msg.download_media(file=MEDIA)
        increment_stat("media_saved")
    elif msg.voice:
        msg_type = "voice"
        media_path = await msg.download_media(file=MEDIA)
        increment_stat("media_saved")
    elif msg.file:
        msg_type = "file"
        media_path = await msg.download_media(file=MEDIA)
        increment_stat("media_saved")

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

    increment_stat("messages_saved")


# ---------- Обработка удаления ----------
@client.on(events.MessageDeleted)
async def on_deleted(event):
    notify_group = get_notify_group()

    # Игнорируем сообщения из группы уведомлений, чтобы не рекурсить
    if hasattr(event, 'chat_id') and event.chat_id == notify_group:
        return

    # ПОЛНОЕ ЛОГИРОВАНИЕ события для теста
    event_log = {
        "event_type": "MessageDeleted",
        "deleted_ids": event.deleted_ids,
        "chat_id": getattr(event, 'chat_id', None),
        "original": str(event.original_update),
        "timestamp": now_local(),
        "peer": str(getattr(event, 'peer', None)),
        "channel_id": getattr(event, 'channel_id', None) if hasattr(event, 'channel_id') else None,
    }

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
            increment_stat("messages_deleted")
        else:
            # JSON нет — выводим ВСЮ информацию из события
            chat_info = f"Chat ID: {event_log['chat_id']}" if event_log['chat_id'] else "Chat ID: неизвестен"

            text = (
                f"❌ УДАЛЁННОЕ СООБЩЕНИЕ\n\n"
                f"🆔 Message ID: {msg_id}\n"
                f"💬 {chat_info}\n"
                f"🗑 Время удаления: {now_local()}\n"
                f"[Содержание локально отсутствует]\n\n"
                f"📋 DEBUG INFO:\n"
                f"```\n{json.dumps(event_log, ensure_ascii=False, indent=2)}\n```"
            )
            media_path = None

        # Отправляем уведомление в группу
        try:
            await client.send_message(notify_group, text)
            if media_path and os.path.exists(media_path):
                await client.send_file(notify_group, media_path)
        except Exception as e:
            print(f"Ошибка отправки уведомления: {e}")


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


# ---------- Чек сообщений ----------
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


# ---------- Ежедневная статистика ----------
async def daily_stats_report():
    """Отправляет ежедневный отчет в 00:00"""
    while True:
        now = datetime.now()
        # Вычисляем время до следующей полуночи
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        wait_seconds = (tomorrow - now).total_seconds()

        await asyncio.sleep(wait_seconds)

        # Отправляем статистику
        stats = load_stats()
        notify_group = get_notify_group()

        report = (
            f"📊 ЕЖЕДНЕВНАЯ СТАТИСТИКА\n\n"
            f"📝 Сохранено сообщений: {stats.get('messages_saved', 0)}\n"
            f"📎 Сохранено медиа: {stats.get('media_saved', 0)}\n"
            f"🗑 Удалено сообщений: {stats.get('messages_deleted', 0)}\n\n"
            f"🕐 Отчет за: {(datetime.now() - timedelta(days=1)).strftime('%d.%m.%Y')}"
        )

        try:
            await client.send_message(notify_group, report)
        except Exception as e:
            print(f"Ошибка отправки ежедневной статистики: {e}")

        # Сбрасываем счетчики
        stats["messages_saved"] = 0
        stats["media_saved"] = 0
        stats["messages_deleted"] = 0
        stats["last_reset"] = now_local()
        save_stats(stats)


# ---------- Команды ----------
@client.on(events.NewMessage(pattern=r'^\.help$', outgoing=True))
async def help_command(event):
    """Команда .help - показывает список всех команд"""
    help_text = (
        "📋 СПИСОК КОМАНД БОТА\n\n"
        "🔹 .help - показать это сообщение\n"
        "🔹 .p - проверка статуса бота (ping)\n"
        "🔹 .ch - показать размер сохраненных данных\n"
        "🔹 .d [дата] - удалить файлы старше даты\n"
        "   Формат: .d DD.MM или .d DD.MM.YYYY\n"
        "   Пример: .d 15.01 или .d 15.01.2024\n"
        "🔹 .delete [число] - удалить последние N своих сообщений\n"
        "   Пример: .delete 5\n"
        "🔹 .chatSet [ID] - установить чат для уведомлений\n"
        "   Без параметра - отправка в избранное\n"
        "   Пример: .chatSet -1001234567890\n\n"
        "💡 Автофункции:\n"
        "• Сообщения с точкой в конце - автопроверка орфографии\n"
        "• Автосохранение всех сообщений\n"
        "• Уведомления об удаленных сообщениях\n"
        "• Ежедневная статистика в 00:00"
    )
    await event.edit(help_text)


@client.on(events.NewMessage(pattern=r'^\.delete\s+(\d+)$', outgoing=True))
async def delete_messages_command(event):
    """Команда .delete [число] - удаляет последние N своих сообщений в текущем чате"""
    try:
        count = int(event.pattern_match.group(1))

        if count <= 0:
            await event.edit("❌ Число должно быть больше 0")
            return

        if count > 100:
            await event.edit("❌ Максимум можно удалить 100 сообщений за раз")
            return

        chat = await event.get_chat()

        # Получаем последние сообщения в чате
        messages_to_delete = []
        async for message in client.iter_messages(chat, limit=count + 1):  # +1 для команды
            if message.out:  # Только наши сообщения
                messages_to_delete.append(message.id)

        # Удаляем команду из списка
        if event.message.id in messages_to_delete:
            messages_to_delete.remove(event.message.id)

        # Ограничиваем количество
        messages_to_delete = messages_to_delete[:count]

        if not messages_to_delete:
            await event.edit("❌ Нет сообщений для удаления")
            return

        # Удаляем команду
        await event.delete()

        # Удаляем сообщения
        await client.delete_messages(chat, messages_to_delete)

    except Exception as e:
        await event.edit(f"❌ Ошибка: {e}")

@client.on(events.NewMessage(pattern=r'^\.chatSet(?:\s+(.+))?$', outgoing=True))
async def chatset_command(event):
    """Команда .chatSet - устанавливает чат для уведомлений о удаленных сообщениях"""
    try:
        param = event.pattern_match.group(1)

        if param:
            param = param.strip()
            # Если параметр - число, используем как chat_id
            try:
                chat_id = int(param)
                set_notify_group(chat_id)

                # Пробуем получить информацию о чате
                try:
                    chat = await client.get_entity(chat_id)
                    chat_name = getattr(chat, 'title', getattr(chat, 'first_name', f'Chat {chat_id}'))
                    response = f"✅ Чат для уведомлений установлен:\n📍 {chat_name} (ID: {chat_id})"
                except:
                    response = f"✅ Чат для уведомлений установлен:\n📍 ID: {chat_id}"

            except ValueError:
                # Если не число, пробуем как username
                try:
                    chat = await client.get_entity(param)
                    chat_id = chat.id
                    set_notify_group(chat_id)
                    chat_name = getattr(chat, 'title', getattr(chat, 'first_name', param))
                    response = f"✅ Чат для уведомлений установлен:\n📍 {chat_name} (ID: {chat_id})"
                except:
                    response = f"❌ Не удалось найти чат: {param}"
        else:
            # Без параметра - устанавливаем избранное
            set_notify_group("me")
            response = "✅ Уведомления будут отправляться в избранное"

        await event.edit(response)

    except Exception as e:
        await event.edit(f"❌ Ошибка: {e}")


@client.on(events.NewMessage(pattern=r'^\.ch$', outgoing=True))
async def check_size_command(event):
    """Команда .ch - показывает размер папки с сохраненками"""
    try:
        total_size = get_folder_size(BASE)
        msg_count = len([f for f in os.listdir(MSG) if f.endswith('.json')])
        media_count = len(os.listdir(MEDIA))

        response = (
            f"💾 РАЗМЕР СОХРАНЕНОК\n\n"
            f"📊 Общий размер: {format_size(total_size)}\n"
            f"📝 Сообщений: {msg_count}\n"
            f"📎 Медиафайлов: {media_count}\n"
            f"📁 Папка сообщений: {format_size(get_folder_size(MSG))}\n"
            f"🎬 Папка медиа: {format_size(get_folder_size(MEDIA))}"
        )

        await event.edit(response)
    except Exception as e:
        await event.edit(f"❌ Ошибка: {e}")


@client.on(events.NewMessage(pattern=r'^\.d\s+(.+)$', outgoing=True))
async def delete_old_command(event):
    """Команда .d [дата] - удаляет файлы старше указанной даты"""
    try:
        date_str = event.pattern_match.group(1).strip()

        # Парсим дату
        try:
            # Пробуем формат DD.MM или DD.MM.YYYY
            if len(date_str.split('.')) == 2:
                day, month = date_str.split('.')
                cutoff_date = datetime(datetime.now().year, int(month), int(day))
            else:
                day, month, year = date_str.split('.')
                cutoff_date = datetime(int(year), int(month), int(day))
        except:
            await event.edit("❌ Неверный формат даты. Используйте: .d DD.MM или .d DD.MM.YYYY")
            return

        # Проверка: дата не в будущем
        if cutoff_date > datetime.now():
            await event.edit("❌ Дата не может быть в будущем!")
            return

        deleted_msgs = 0
        deleted_media = 0

        # Удаляем сообщения
        for file in os.listdir(MSG):
            path = os.path.join(MSG, file)
            if os.path.isfile(path):
                file_time = datetime.fromtimestamp(os.path.getmtime(path))
                if file_time < cutoff_date:
                    os.remove(path)
                    deleted_msgs += 1

        # Удаляем медиа
        for file in os.listdir(MEDIA):
            path = os.path.join(MEDIA, file)
            if os.path.isfile(path):
                file_time = datetime.fromtimestamp(os.path.getmtime(path))
                if file_time < cutoff_date:
                    os.remove(path)
                    deleted_media += 1

        response = (
            f"🗑 ОЧИСТКА ЗАВЕРШЕНА\n\n"
            f"📅 Удалено до: {cutoff_date.strftime('%d.%m.%Y')}\n"
            f"📝 Сообщений удалено: {deleted_msgs}\n"
            f"📎 Медиафайлов удалено: {deleted_media}\n"
            f"✅ Всего удалено: {deleted_msgs + deleted_media}"
        )

        await event.edit(response)
    except Exception as e:
        await event.edit(f"❌ Ошибка: {e}")


@client.on(events.NewMessage(pattern=r'^\.p$', outgoing=True))
async def ping_command(event):
    """Команда .p - проверка что бот жив"""
    uptime_start = datetime.now() - timedelta(seconds=int(asyncio.get_event_loop().time()))
    stats = load_stats()
    notify_group = get_notify_group()

    # Определяем название чата уведомлений
    if notify_group == "me":
        notify_name = "Избранное"
    else:
        try:
            chat = await client.get_entity(notify_group)
            notify_name = getattr(chat, 'title', getattr(chat, 'first_name', f'ID: {notify_group}'))
        except:
            notify_name = f'ID: {notify_group}'

    response = (
        f"✅ БОТ АКТИВЕН\n\n"
        f"⏰ Время: {now_local()}\n"
        f"📊 Сохранено сегодня:\n"
        f"  📝 Сообщений: {stats.get('messages_saved', 0)}\n"
        f"  📎 Медиа: {stats.get('media_saved', 0)}\n"
        f"  🗑 Удалено: {stats.get('messages_deleted', 0)}\n"
        f"🔄 Последний сброс: {stats.get('last_reset', 'N/A')}\n"
        f"📢 Уведомления: {notify_name}"
    )

    await event.edit(response)


# ---------- Запуск ----------
async def main():
    asyncio.create_task(cleanup_ttl())  # TTL-очистка в фоне
    asyncio.create_task(daily_stats_report())  # Ежедневная статистика
    await client.start()
    print("Логгер запущен...")
    print(f"API ID: {api_id}")
    print(f"Команды: .help, .ch, .d [дата], .p, .delete [число], .chatSet [ID]")
    await client.run_until_disconnected()


asyncio.run(main())