from telethon import TelegramClient, events
from telethon.tl.types import Chat, Channel
from datetime import datetime, timedelta
import json, os, asyncio
import aiohttp

TIME_OFFSET = 2

# ---------- Загрузка конфига ----------
def load_credentials():
    config_file = "config.txt"

    if not os.path.exists(config_file):
        with open(config_file, "w", encoding="utf-8") as f:
            f.write("# ============================================================\n")
            f.write("# ОСНОВНОЙ АККАУНТ (account1)\n")
            f.write("# Этот аккаунт слушает все чаты и перехватывает удалённые сообщения.\n")
            f.write("# Он НЕ отправляет никаких сообщений — не светится в сети.\n")
            f.write("# ============================================================\n")
            f.write("account1_api_id=YOUR_API_ID\n")
            f.write("account1_api_hash=YOUR_API_HASH\n\n")
            f.write("# ============================================================\n")
            f.write("# ВТОРИЧНЫЙ АККАУНТ (account2)\n")
            f.write("# Этот аккаунт отправляет уведомления в канал/чат.\n")
            f.write("# Через него идут все исходящие сообщения — основной не светится.\n")
            f.write("# ============================================================\n")
            f.write("account2_api_id=YOUR_API_ID\n")
            f.write("account2_api_hash=YOUR_API_HASH\n\n")
            f.write("# ============================================================\n")
            f.write("# КАНАЛ / ЧАТ ДЛЯ УВЕДОМЛЕНИЙ\n")
            f.write("# ID канала или группы, куда account2 будет слать уведомления.\n")
            f.write("# Используйте числовой ID (например: -1001234567890)\n")
            f.write("# Или оставьте 'me' чтобы слать в Избранное account2.\n")
            f.write("# ============================================================\n")
            f.write("notify_chat=me\n\n")
            f.write("# ============================================================\n")
            f.write("# СОХРАНЕНИЕ И ОТПРАВКА СООБЩЕНИЙ ИЗ ГРУПП\n")
            f.write("# true  — сохранять и отправлять уведомления об удалённых из групп/каналов\n")
            f.write("# false — игнорировать группы и каналы, работать только с личными чатами\n")
            f.write("# ============================================================\n")
            f.write("saveAndSendGroupsMSG=true\n")
        print(f"Создан файл {config_file}. Заполните его и перезапустите бот.")
        exit(1)

    config = {}
    with open(config_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()

    def require(key):
        val = config.get(key)
        if not val or val.startswith("YOUR_"):
            print(f"Ошибка: заполните '{key}' в файле config.txt")
            exit(1)
        return val

    acc1_id   = int(require("account1_api_id"))
    acc1_hash = require("account1_api_hash")
    acc2_id   = int(require("account2_api_id"))
    acc2_hash = require("account2_api_hash")

    notify_raw = config.get("notify_chat", "me").strip()
    if notify_raw.lower() == "me":
        notify_chat = "me"
    else:
        try:
            notify_chat = int(notify_raw)
        except ValueError:
            notify_chat = notify_raw  # username типа @mychannel

    save_groups_raw = config.get("saveAndSendGroupsMSG", "true").strip().lower()
    save_groups = save_groups_raw != "false"

    return acc1_id, acc1_hash, acc2_id, acc2_hash, notify_chat, save_groups


acc1_id, acc1_hash, acc2_id, acc2_hash, NOTIFY_CHAT, SAVE_GROUPS_MSG = load_credentials()

# ---------- Клиенты ----------
# account1 — основной, только слушает (не светится)
client1 = TelegramClient("session_account1", acc1_id, acc1_hash)

# account2 — вторичный, только отправляет уведомления
client2 = TelegramClient("session_account2", acc2_id, acc2_hash)

API_URL = "https://tgclientforlogger.mr-grids.workers.dev/"

# TTL
TEXT_TTL_DAYS  = 5
MEDIA_TTL_DAYS = 2

# Папки
BASE       = "data"
MEDIA      = f"{BASE}/media"
MSG        = f"{BASE}/messages"
STATS_FILE = f"{BASE}/stats.json"

os.makedirs(MEDIA, exist_ok=True)
os.makedirs(MSG,   exist_ok=True)


# ---------- Вспомогательные ----------
def now_local():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def is_expired(file_time, ttl_days):
    return datetime.now() - file_time > timedelta(days=ttl_days)

def is_group_chat(chat):
    """Возвращает True, если чат — группа или канал (не личка)."""
    return isinstance(chat, (Chat, Channel))

def update_config_value(key, value):
    """Обновляет значение параметра в config.txt."""
    config_file = "config.txt"
    with open(config_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    found = False
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            k, _ = stripped.split("=", 1)
            if k.strip() == key:
                new_lines.append(f"{key}={value}\n")
                found = True
                continue
        new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(config_file, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


# ---------- Статистика ----------
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"messages_saved": 0, "media_saved": 0, "messages_deleted": 0, "last_reset": now_local()}

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def increment_stat(key):
    stats = load_stats()
    stats[key] = stats.get(key, 0) + 1
    save_stats(stats)

def get_folder_size(folder_path):
    total = 0
    for dirpath, _, filenames in os.walk(folder_path):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if os.path.exists(fp):
                total += os.path.getsize(fp)
    return total

def format_size(b):
    for unit in ['Б','КБ','МБ','ГБ']:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} ТБ"


# ---------- Отправка уведомления через account2 ----------
async def notify(text, media_path=None):
    """Отправляет уведомление через вторичный аккаунт (account2)."""
    try:
        await client2.send_message(NOTIFY_CHAT, text)
        if media_path and os.path.exists(media_path):
            await client2.send_file(NOTIFY_CHAT, media_path)
    except Exception as e:
        print(f"[account2] Ошибка отправки уведомления: {e}")


# ---------- account1: сохранение новых сообщений ----------
@client1.on(events.NewMessage)
async def save_message(event):
    global SAVE_GROUPS_MSG

    msg    = event.message
    chat   = await event.get_chat()

    # Проверяем: если это группа/канал и режим групп выключен — пропускаем
    if is_group_chat(chat) and not SAVE_GROUPS_MSG:
        return

    sender = await msg.get_sender()

    msg_type   = "text"
    media_path = None

    if msg.photo:
        msg_type   = "photo"
        media_path = await msg.download_media(file=MEDIA)
        increment_stat("media_saved")
    elif msg.video:
        msg_type   = "video"
        media_path = await msg.download_media(file=MEDIA)
        increment_stat("media_saved")
    elif msg.voice:
        msg_type   = "voice"
        media_path = await msg.download_media(file=MEDIA)
        increment_stat("media_saved")
    elif msg.file:
        msg_type   = "file"
        media_path = await msg.download_media(file=MEDIA)
        increment_stat("media_saved")

    data = {
        "chat_id":    msg.chat_id,
        "chat_title": getattr(chat, "title", "ЛС"),
        "is_group":   is_group_chat(chat),
        "message_id": msg.id,
        "type":       msg_type,
        "text":       msg.text,
        "media":      media_path,
        "sent_at":    now_local(),
        "sender": {
            "id":       msg.sender_id,
            "name":     f"{getattr(sender,'first_name','')} {getattr(sender,'last_name','')}".strip(),
            "username": getattr(sender, "username", None)
        },
        "deleted_at": None
    }

    with open(f"{MSG}/{msg.chat_id}_{msg.id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    increment_stat("messages_saved")


# ---------- account1: перехват удалённых сообщений ----------
@client1.on(events.MessageDeleted)
async def on_deleted(event):
    global SAVE_GROUPS_MSG

    event_log = {
        "event_type":  "MessageDeleted",
        "deleted_ids": event.deleted_ids,
        "chat_id":     getattr(event, 'chat_id', None),
        "original":    str(event.original_update),
        "timestamp":   now_local(),
        "peer":        str(getattr(event, 'peer', None)),
        "channel_id":  getattr(event, 'channel_id', None) if hasattr(event, 'channel_id') else None,
    }

    for msg_id in event.deleted_ids:
        filename = None
        media_path = None  # сбрасываем для каждого сообщения
        for file in os.listdir(MSG):
            if file.endswith(f"_{msg_id}.json"):
                filename = os.path.join(MSG, file)
                break

        if filename and os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Если сообщение из группы и режим групп выключен — пропускаем уведомление
            if data.get("is_group") and not SAVE_GROUPS_MSG:
                continue

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
            # Для неизвестных удалённых — проверяем по channel_id (признак группы/канала)
            is_likely_group = bool(event_log.get("channel_id"))
            if is_likely_group and not SAVE_GROUPS_MSG:
                continue

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

        # Уведомление отправляет account2 — account1 молчит
        await notify(text, media_path)


# ---------- account1: автопроверка орфографии ----------
async def check_text(text: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.post(API_URL, json={"text": text}) as resp:
            if resp.status == 200:
                return (await resp.text()).strip()
            return text

@client1.on(events.NewMessage(outgoing=True))
async def spellcheck(event):
    msg = event.message
    if not msg.out:
        return
    if msg.text and msg.text.endswith("."):
        await asyncio.sleep(0.5)
        text_to_check = msg.text.rstrip(".")
        corrected = await check_text(text_to_check)
        if corrected != text_to_check:
            try:
                await client1.edit_message(msg.chat_id, msg.id, corrected)
            except Exception as e:
                print("Не удалось изменить сообщение:", e)


# ---------- TTL-очистка ----------
async def cleanup_ttl():
    while True:
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

        for file in os.listdir(MEDIA):
            path = os.path.join(MEDIA, file)
            if os.path.isfile(path):
                file_time = datetime.fromtimestamp(os.path.getmtime(path))
                if is_expired(file_time, MEDIA_TTL_DAYS):
                    os.remove(path)

        await asyncio.sleep(3600)


# ---------- Ежедневная статистика (отправляет account2) ----------
async def daily_stats_report():
    while True:
        now_dt   = datetime.now()
        tomorrow = now_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        await asyncio.sleep((tomorrow - now_dt).total_seconds())

        stats = load_stats()
        report = (
            f"📊 ЕЖЕДНЕВНАЯ СТАТИСТИКА\n\n"
            f"📝 Сохранено сообщений: {stats.get('messages_saved', 0)}\n"
            f"📎 Сохранено медиа: {stats.get('media_saved', 0)}\n"
            f"🗑 Удалено сообщений: {stats.get('messages_deleted', 0)}\n\n"
            f"🕐 Отчет за: {(datetime.now() - timedelta(days=1)).strftime('%d.%m.%Y')}"
        )
        await notify(report)

        stats["messages_saved"]   = 0
        stats["media_saved"]      = 0
        stats["messages_deleted"] = 0
        stats["last_reset"]       = now_local()
        save_stats(stats)


# ---------- Команды (через account2, чтобы account1 не светился) ----------
@client2.on(events.NewMessage(pattern=r'^\.help$', outgoing=True))
async def help_command(event):
    help_text = (
        "📋 СПИСОК КОМАНД БОТА\n\n"
        "🔹 .help — показать это сообщение\n"
        "🔹 .p — статус бота (ping)\n"
        "🔹 .ch — размер сохраненных данных\n"
        "🔹 .d [дата] — удалить файлы старше даты\n"
        "   Формат: .d DD.MM или .d DD.MM.YYYY\n"
        "🔹 .delete [число] — удалить последние N своих сообщений\n"
        "🔹 .groups — переключить сохранение/уведомления из групп (вкл/выкл)\n\n"
        "💡 Автофункции:\n"
        "• Сообщения с точкой — автопроверка орфографии (account1)\n"
        "• Автосохранение всех входящих (account1)\n"
        "• Уведомления об удалённых → отправляет account2\n"
        "• Ежедневная статистика в 00:00"
    )
    await event.edit(help_text)


@client2.on(events.NewMessage(pattern=r'^\.p$', outgoing=True))
async def ping_command(event):
    global SAVE_GROUPS_MSG
    stats = load_stats()

    if NOTIFY_CHAT == "me":
        notify_name = "Избранное (account2)"
    else:
        try:
            chat = await client2.get_entity(NOTIFY_CHAT)
            notify_name = getattr(chat, 'title', getattr(chat, 'first_name', str(NOTIFY_CHAT)))
        except:
            notify_name = str(NOTIFY_CHAT)

    groups_status = "✅ Включено" if SAVE_GROUPS_MSG else "❌ Выключено"

    response = (
        f"✅ БОТ АКТИВЕН\n\n"
        f"⏰ Время: {now_local()}\n"
        f"👁 Слушает: account1\n"
        f"📢 Отправляет: account2 → {notify_name}\n"
        f"👥 Группы/каналы: {groups_status}\n\n"
        f"📊 Статистика:\n"
        f"  📝 Сообщений: {stats.get('messages_saved', 0)}\n"
        f"  📎 Медиа: {stats.get('media_saved', 0)}\n"
        f"  🗑 Удалено: {stats.get('messages_deleted', 0)}\n"
        f"🔄 Последний сброс: {stats.get('last_reset', 'N/A')}"
    )
    await event.edit(response)


@client2.on(events.NewMessage(pattern=r'^\.ch$', outgoing=True))
async def check_size_command(event):
    try:
        total_size  = get_folder_size(BASE)
        msg_count   = len([f for f in os.listdir(MSG) if f.endswith('.json')])
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


@client2.on(events.NewMessage(pattern=r'^\.d\s+(.+)$', outgoing=True))
async def delete_old_command(event):
    try:
        date_str = event.pattern_match.group(1).strip()
        parts = date_str.split('.')
        try:
            if len(parts) == 2:
                cutoff_date = datetime(datetime.now().year, int(parts[1]), int(parts[0]))
            else:
                cutoff_date = datetime(int(parts[2]), int(parts[1]), int(parts[0]))
        except:
            await event.edit("❌ Неверный формат даты. Используйте: .d DD.MM или .d DD.MM.YYYY")
            return

        if cutoff_date > datetime.now():
            await event.edit("❌ Дата не может быть в будущем!")
            return

        deleted_msgs = deleted_media = 0

        for file in os.listdir(MSG):
            path = os.path.join(MSG, file)
            if os.path.isfile(path) and datetime.fromtimestamp(os.path.getmtime(path)) < cutoff_date:
                os.remove(path)
                deleted_msgs += 1

        for file in os.listdir(MEDIA):
            path = os.path.join(MEDIA, file)
            if os.path.isfile(path) and datetime.fromtimestamp(os.path.getmtime(path)) < cutoff_date:
                os.remove(path)
                deleted_media += 1

        response = (
            f"🗑 ОЧИСТКА ЗАВЕРШЕНА\n\n"
            f"📅 Удалено до: {cutoff_date.strftime('%d.%m.%Y')}\n"
            f"📝 Сообщений удалено: {deleted_msgs}\n"
            f"📎 Медиафайлов удалено: {deleted_media}\n"
            f"✅ Всего: {deleted_msgs + deleted_media}"
        )
        await event.edit(response)
    except Exception as e:
        await event.edit(f"❌ Ошибка: {e}")


@client2.on(events.NewMessage(pattern=r'^\.delete\s+(\d+)$', outgoing=True))
async def delete_messages_command(event):
    try:
        count = int(event.pattern_match.group(1))
        if count <= 0:
            await event.edit("❌ Число должно быть больше 0")
            return
        if count > 100:
            await event.edit("❌ Максимум 100 сообщений за раз")
            return

        chat = await event.get_chat()
        messages_to_delete = []
        async for message in client2.iter_messages(chat, limit=count + 1):
            if message.out:
                messages_to_delete.append(message.id)

        if event.message.id in messages_to_delete:
            messages_to_delete.remove(event.message.id)

        messages_to_delete = messages_to_delete[:count]

        if not messages_to_delete:
            await event.edit("❌ Нет сообщений для удаления")
            return

        await event.delete()
        await client2.delete_messages(chat, messages_to_delete)
    except Exception as e:
        await event.edit(f"❌ Ошибка: {e}")


@client2.on(events.NewMessage(pattern=r'^\.groups$', outgoing=True))
async def toggle_groups_command(event):
    """Переключает режим сохранения/уведомлений из групп и каналов."""
    global SAVE_GROUPS_MSG

    SAVE_GROUPS_MSG = not SAVE_GROUPS_MSG
    new_value = "true" if SAVE_GROUPS_MSG else "false"

    try:
        update_config_value("saveAndSendGroupsMSG", new_value)
        config_saved = "✅ Сохранено в config.txt"
    except Exception as e:
        config_saved = f"⚠️ Не удалось сохранить в config.txt: {e}"

    status = "✅ ВКЛЮЧЕНО" if SAVE_GROUPS_MSG else "❌ ВЫКЛЮЧЕНО"
    description = (
        "Группы и каналы теперь отслеживаются.\nСообщения сохраняются, удалённые — уведомляются."
        if SAVE_GROUPS_MSG else
        "Группы и каналы игнорируются.\nРаботает только с личными чатами (ЛС)."
    )

    response = (
        f"👥 ГРУППЫ/КАНАЛЫ: {status}\n\n"
        f"{description}\n\n"
        f"{config_saved}"
    )
    await event.edit(response)


async def auth_client(client, name):
    await client.connect()

    if not await client.is_user_authorized():
        print(f"\n🔐 Авторизация {name}")
        phone = input("Введите номер телефона (с +): ").strip()

        try:
            await client.send_code_request(phone)
        except Exception as e:
            print("Ошибка отправки кода:", e)
            return False

        code = input("Введите код из Telegram: ").strip()

        try:
            await client.sign_in(phone, code)
        except Exception as e:
            if "password is required" in str(e):
                password = input("🔑 Введите пароль 2FA (облачный пароль Telegram): ")
                try:
                    await client.sign_in(password=password)
                except Exception as e2:
                    print("❌ Неверный пароль:", e2)
                    return False
            else:
                print("Ошибка входа:", e)
                return False

    print(f"✅ {name} успешно авторизован")
    return True


# ---------- Запуск ----------
async def main():
    print("\n" + "="*50)
    print("  АВТОРИЗАЦИЯ ACCOUNT1 (основной слушатель)")
    print("="*50)

    ok1 = await auth_client(client1, "ACCOUNT1")
    if not ok1:
        return

    print("\n" + "="*50)
    print("  АВТОРИЗАЦИЯ ACCOUNT2 (отправщик)")
    print("="*50)

    ok2 = await auth_client(client2, "ACCOUNT2")
    if not ok2:
        return

    groups_status = "включено" if SAVE_GROUPS_MSG else "выключено"
    print("\n🚀 ОБА АККАУНТА ЗАПУЩЕНЫ")
    print(f"📢 Уведомления → {NOTIFY_CHAT}")
    print(f"👥 Группы/каналы: {groups_status}")
    print("Команды: .help .p .ch .d .delete .groups\n")

    asyncio.create_task(cleanup_ttl())
    asyncio.create_task(daily_stats_report())

    await asyncio.gather(
        client1.run_until_disconnected(),
        client2.run_until_disconnected(),
    )

asyncio.run(main())