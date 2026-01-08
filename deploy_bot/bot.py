"""
Бот-установщик VPN бота на новые серверы.
Позволяет развернуть VPN-бота из GitHub на любой сервер по SSH.
"""
import asyncio
import logging
import os
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import asyncssh

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Обязательно задать в .env или переменных окружения
ADMIN_IDS = [906888481]
GITHUB_REPO = "https://github.com/agdelesha/agdeweb.git"
REPO_PATH = "/root/agdeweb"  # Куда клонируется репозиторий
VPN_BOT_PATH = "/root/agdeweb/vpn_bot"  # Где находится bot.py
SERVERS_FILE = "/root/deploy_bot/servers.json"
DB_BACKUP_PATH = "/root/db_backup"

# Дефолтный .env для VPN-бота (без токена)
DEFAULT_ENV_TEMPLATE = """BOT_TOKEN={bot_token}
ADMIN_ID=906888481
CLIENT_DIR=/etc/wireguard/clients
WG_INTERFACE=wg0
ADD_SCRIPT=/usr/local/bin/wg-new-conf.sh
REMOVE_SCRIPT=/usr/local/bin/wg-remove-client.sh
"""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


class DeployStates(StatesGroup):
    # Деплой
    select_server = State()
    waiting_for_bot_token = State()
    confirm_deploy = State()
    # Добавление сервера
    add_server_name = State()
    add_server_ip = State()
    add_server_password = State()
    add_server_path = State()
    # Связывание серверов
    link_source_server = State()
    link_target_server = State()
    # Смена основного сервера
    confirm_set_main = State()
    # Смена токена бота
    change_bot_token = State()
    # Терминал
    terminal_mode = State()


# Настройки автобэкапа
AUTO_BACKUP_INTERVAL_HOURS = 6


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ============ Работа с серверами ============

def load_servers() -> dict:
    """Загрузить список серверов из JSON"""
    if os.path.exists(SERVERS_FILE):
        with open(SERVERS_FILE, 'r') as f:
            return json.load(f)
    # Дефолтные серверы
    return {
        "servers": [
            {
                "name": "Turkey (основной)",
                "ip": "83.217.9.75",
                "password": None,  # Используем SSH-ключ
                "is_main": True,
                "has_bot_code": True,  # Есть код бота на сервере
                "bot_running": True,  # Сервис работает
                "vpn_bot_path": "/root/vpn_bot"  # Путь к боту на сервере
            }
        ]
    }


def get_server_vpn_path(server: dict) -> str:
    """Получить путь к VPN-боту на сервере"""
    return server.get("vpn_bot_path", VPN_BOT_PATH)


def save_servers(data: dict):
    """Сохранить список серверов в JSON"""
    os.makedirs(os.path.dirname(SERVERS_FILE), exist_ok=True)
    with open(SERVERS_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_server_by_ip(ip: str) -> dict:
    """Найти сервер по IP"""
    data = load_servers()
    for server in data["servers"]:
        if server["ip"] == ip:
            return server
    return None


def get_main_server() -> dict:
    """Получить основной сервер"""
    data = load_servers()
    for server in data["servers"]:
        if server.get("is_main"):
            return server
    return data["servers"][0] if data["servers"] else None


def get_last_backup_info() -> str:
    """Получить информацию о последнем бэкапе БД"""
    latest_db = f"{DB_BACKUP_PATH}/vpn_bot_latest.db"
    if not os.path.exists(latest_db):
        return "❌ Нет бэкапа БД"
    
    try:
        stat = os.stat(latest_db)
        mtime = datetime.fromtimestamp(stat.st_mtime)
        size_mb = stat.st_size / (1024 * 1024)
        time_ago = datetime.now() - mtime
        
        if time_ago.days > 0:
            ago_str = f"{time_ago.days} дн. назад"
        elif time_ago.seconds >= 3600:
            ago_str = f"{time_ago.seconds // 3600} ч. назад"
        else:
            ago_str = f"{time_ago.seconds // 60} мин. назад"
        
        return f"✅ Бэкап БД: {mtime.strftime('%d.%m %H:%M')} ({ago_str}, {size_mb:.1f} MB)"
    except Exception:
        return "⚠️ Ошибка чтения бэкапа"


# ============ Клавиатуры ============

def get_main_menu_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🚀 Развернуть VPN-бота", callback_data="deploy_start")],
        [InlineKeyboardButton(text="🔄 Синхронизировать БД", callback_data="sync_db")],
        [InlineKeyboardButton(text="🖥 Управление серверами", callback_data="servers_menu")],
        [InlineKeyboardButton(text="🔗 Связать серверы (SSH)", callback_data="link_servers")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_servers_menu_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📋 Список серверов", callback_data="servers_list")],
        [InlineKeyboardButton(text="➕ Добавить сервер", callback_data="add_server")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_servers_list_kb(for_deploy: bool = False, for_link: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура со списком серверов"""
    data = load_servers()
    buttons = []
    
    for server in data["servers"]:
        # 🟢 = сервис работает, 🟡 = есть код но не запущен, ⚪ = нет кода
        if server.get("bot_running"):
            status = "🟢"
        elif server.get("has_bot_code"):
            status = "🟡"
        else:
            status = "⚪"
        main = " ⭐" if server.get("is_main") else ""
        text = f"{status} {server['name']}{main}"
        
        if for_deploy:
            callback = f"deploy_to_{server['ip']}"
        elif for_link:
            callback = f"link_select_{server['ip']}"
        else:
            callback = f"server_info_{server['ip']}"
        
        buttons.append([InlineKeyboardButton(text=text, callback_data=callback)])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="servers_menu" if not for_deploy else "main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_server_info_kb(ip: str, has_code: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_status_{ip}")],
        [InlineKeyboardButton(text="📊 Загрузка сервера", callback_data=f"server_stats_{ip}")],
        [InlineKeyboardButton(text="💻 Терминал", callback_data=f"terminal_{ip}")],
    ]
    
    if has_code:
        buttons.append([InlineKeyboardButton(text="📥 Обновить код (git pull)", callback_data=f"update_code_{ip}")])
        buttons.append([InlineKeyboardButton(text="🗄 Обновить БД", callback_data=f"push_db_{ip}")])
        buttons.append([InlineKeyboardButton(text="🔑 Сменить токен бота", callback_data=f"change_token_{ip}")])
        buttons.append([InlineKeyboardButton(text="🛑 Остановить бота", callback_data=f"stop_bot_{ip}")])
        buttons.append([InlineKeyboardButton(text="▶️ Запустить бота", callback_data=f"start_bot_{ip}")])
    
    buttons.append([InlineKeyboardButton(text="⭐ Сделать основным", callback_data=f"set_main_{ip}")])
    buttons.append([InlineKeyboardButton(text="🗑 Удалить сервер", callback_data=f"delete_server_{ip}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="servers_list")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")]
    ])


def get_confirm_deploy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить деплой", callback_data="confirm_deploy")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")]
    ])


# ============ Обработчики ============

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён")
        return
    
    await state.clear()
    backup_info = get_last_backup_info()
    await message.answer(
        "🤖 *Deploy Bot*\n\n"
        "Управление серверами и деплой VPN-бота.\n\n"
        f"📦 {backup_info}\n\n"
        "Выбери действие:",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb()
    )


@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.clear()
    await callback.answer()
    backup_info = get_last_backup_info()
    await callback.message.edit_text(
        "🤖 *Deploy Bot*\n\n"
        "Управление серверами и деплой VPN-бота.\n\n"
        f"📦 {backup_info}\n\n"
        "Выбери действие:",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb()
    )


# ============ Деплой VPN-бота ============

@dp.callback_query(F.data == "deploy_start")
async def deploy_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    await state.set_state(DeployStates.select_server)
    await callback.message.edit_text(
        "🚀 *Деплой VPN-бота*\n\n"
        "Выбери сервер для установки:",
        parse_mode="Markdown",
        reply_markup=get_servers_list_kb(for_deploy=True)
    )


@dp.callback_query(F.data.startswith("deploy_to_"))
async def deploy_select_server(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("deploy_to_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer()
    await state.update_data(deploy_server=server)
    await state.set_state(DeployStates.waiting_for_bot_token)
    
    await callback.message.edit_text(
        f"🚀 *Деплой на {server['name']}*\n\n"
        f"Введи токен бота (получи у @BotFather):",
        parse_mode="Markdown",
        reply_markup=get_cancel_kb()
    )


@dp.message(DeployStates.waiting_for_bot_token)
async def process_bot_token(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    token = message.text.strip()
    
    # Простая валидация токена
    if ":" not in token or len(token) < 40:
        await message.answer("❌ Неверный формат токена. Попробуй ещё раз:")
        return
    
    # Удаляем сообщение с токеном
    try:
        await message.delete()
    except:
        pass
    
    await state.update_data(bot_token=token)
    data = await state.get_data()
    server = data["deploy_server"]
    
    await state.set_state(DeployStates.confirm_deploy)
    await message.answer(
        f"📋 *Подтверди деплой*\n\n"
        f"🖥 Сервер: `{server['name']}` ({server['ip']})\n"
        f"🤖 Токен: `{token[:20]}...`\n"
        f"📦 Репозиторий: GitHub\n\n"
        f"Будет выполнено:\n"
        f"1. Установка Python, pip, git\n"
        f"2. Клонирование репозитория\n"
        f"3. Установка зависимостей\n"
        f"4. Копирование БД (если есть бэкап)\n"
        f"5. Настройка systemd\n"
        f"6. Запуск бота",
        parse_mode="Markdown",
        reply_markup=get_confirm_deploy_kb()
    )


@dp.callback_query(F.data == "confirm_deploy")
async def confirm_deploy(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    data = await state.get_data()
    server = data.get("deploy_server")
    bot_token = data.get("bot_token")
    
    if not server or not bot_token:
        await callback.message.edit_text("❌ Ошибка: данные потеряны", reply_markup=get_main_menu_kb())
        await state.clear()
        return
    
    status_msg = await callback.message.edit_text(
        f"🚀 *Деплой на {server['name']}*\n\n"
        "⏳ Подключение к серверу...",
        parse_mode="Markdown"
    )
    
    try:
        connect_kwargs = {
            "host": server["ip"],
            "username": "root",
            "known_hosts": None
        }
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            
            async def run_cmd(cmd: str, description: str) -> bool:
                await status_msg.edit_text(
                    f"🚀 *Деплой на {server['name']}*\n\n"
                    f"⏳ {description}...",
                    parse_mode="Markdown"
                )
                result = await conn.run(cmd, check=False)
                if result.exit_status != 0:
                    logger.error(f"Command failed: {cmd}\n{result.stderr}")
                return result.exit_status == 0
            
            # 1. Устанавливаем зависимости
            await run_cmd(
                "apt-get update && apt-get install -y python3 python3-pip python3-venv git",
                "Установка Python и Git"
            )
            
            # 2. Удаляем старую папку
            await run_cmd(f"rm -rf {REPO_PATH}", "Очистка")
            
            # 3. Клонируем репозиторий
            if not await run_cmd(f"git clone {GITHUB_REPO} {REPO_PATH}", "Клонирование репозитория"):
                raise Exception("Не удалось клонировать репозиторий")
            
            # 4. Создаём venv и устанавливаем зависимости (из папки vpn_bot)
            await run_cmd(
                f"cd {VPN_BOT_PATH} && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt",
                "Установка зависимостей"
            )
            
            # 5. Создаём .env
            env_content = DEFAULT_ENV_TEMPLATE.format(bot_token=bot_token)
            env_escaped = env_content.replace("'", "'\\''")
            await run_cmd(f"echo '{env_escaped}' > {VPN_BOT_PATH}/.env", "Создание .env")
            
            # 6. Копируем БД если есть бэкап
            latest_db = f"{DB_BACKUP_PATH}/vpn_bot_latest.db"
            if os.path.exists(latest_db):
                await status_msg.edit_text(
                    f"🚀 *Деплой на {server['name']}*\n\n"
                    "⏳ Копирование БД...",
                    parse_mode="Markdown"
                )
                await asyncssh.scp(latest_db, (conn, f"{VPN_BOT_PATH}/vpn_bot.db"))
                
                # Очищаем токены дополнительных ботов чтобы избежать конфликтов
                await run_cmd(
                    f"sqlite3 {VPN_BOT_PATH}/vpn_bot.db 'DELETE FROM bot_instances;'",
                    "Очистка токенов доп. ботов"
                )
            
            # 7. Создаём systemd сервис
            service_content = f"""[Unit]
Description=VPN Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={VPN_BOT_PATH}
ExecStart={VPN_BOT_PATH}/venv/bin/python {VPN_BOT_PATH}/bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""
            service_escaped = service_content.replace("'", "'\\''")
            await run_cmd(f"echo '{service_escaped}' > /etc/systemd/system/vpn-bot.service", "Создание сервиса")
            
            # 8. Запускаем
            await run_cmd("systemctl daemon-reload", "Перезагрузка systemd")
            await run_cmd("systemctl enable vpn-bot", "Включение автозапуска")
            await run_cmd("systemctl restart vpn-bot", "Запуск бота")
            
            # 9. Проверяем статус
            await asyncio.sleep(3)
            result = await conn.run("systemctl is-active vpn-bot", check=False)
            is_running = result.stdout.strip() == "active"
            
            # Обновляем статус сервера
            servers_data = load_servers()
            for s in servers_data["servers"]:
                if s["ip"] == server["ip"]:
                    s["has_bot_code"] = True
                    s["bot_running"] = is_running
            save_servers(servers_data)
            
            if is_running:
                await status_msg.edit_text(
                    f"✅ *Деплой завершён!*\n\n"
                    f"🖥 Сервер: {server['name']}\n"
                    f"🟢 Статус: работает\n\n"
                    f"VPN-бот успешно развёрнут!",
                    parse_mode="Markdown",
                    reply_markup=get_main_menu_kb()
                )
            else:
                logs = await conn.run(f"journalctl -u vpn-bot -n 10 --no-pager", check=False)
                await status_msg.edit_text(
                    f"⚠️ *Деплой завершён с ошибкой*\n\n"
                    f"🔴 Бот не запустился\n\n"
                    f"Логи:\n```\n{logs.stdout[:500]}\n```",
                    parse_mode="Markdown",
                    reply_markup=get_main_menu_kb()
                )
    
    except asyncssh.Error as e:
        await status_msg.edit_text(
            f"❌ *Ошибка подключения*\n\n"
            f"Сервер: {server['ip']}\n"
            f"Ошибка: {str(e)}\n\n"
            f"Проверь SSH-ключ или пароль.",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb()
        )
    except Exception as e:
        await status_msg.edit_text(
            f"❌ *Ошибка деплоя*\n\n{str(e)}",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb()
        )
    
    await state.clear()


# ============ Синхронизация БД ============

@dp.callback_query(F.data == "sync_db")
async def sync_db(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    main_server = get_main_server()
    
    if not main_server:
        await callback.message.edit_text(
            "❌ Основной сервер не найден",
            reply_markup=get_main_menu_kb()
        )
        return
    
    status_msg = await callback.message.edit_text(
        f"🔄 *Синхронизация БД*\n\n"
        f"⏳ Подключение к {main_server['name']}...",
        parse_mode="Markdown"
    )
    
    try:
        os.makedirs(DB_BACKUP_PATH, exist_ok=True)
        
        connect_kwargs = {
            "host": main_server["ip"],
            "username": "root",
            "known_hosts": None
        }
        if main_server.get("password"):
            connect_kwargs["password"] = main_server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"{DB_BACKUP_PATH}/vpn_bot_{timestamp}.db"
            latest_file = f"{DB_BACKUP_PATH}/vpn_bot_latest.db"
            
            # Используем путь из настроек сервера
            server_vpn_path = get_server_vpn_path(main_server)
            
            await asyncssh.scp(
                (conn, f"{server_vpn_path}/vpn_bot.db"),
                backup_file
            )
            await asyncssh.scp(
                (conn, f"{server_vpn_path}/vpn_bot.db"),
                latest_file
            )
            
            file_size = os.path.getsize(backup_file)
            size_mb = file_size / (1024 * 1024)
            
            await status_msg.edit_text(
                f"✅ *БД синхронизирована!*\n\n"
                f"📁 Файл: `vpn_bot_{timestamp}.db`\n"
                f"📊 Размер: {size_mb:.2f} MB\n\n"
                f"При деплое эта БД будет использована.",
                parse_mode="Markdown",
                reply_markup=get_main_menu_kb()
            )
    
    except Exception as e:
        await status_msg.edit_text(
            f"❌ *Ошибка синхронизации*\n\n{str(e)}",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb()
        )


# ============ Управление серверами ============

@dp.callback_query(F.data == "servers_menu")
async def servers_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await callback.message.edit_text(
        "🖥 *Управление серверами*",
        parse_mode="Markdown",
        reply_markup=get_servers_menu_kb()
    )


@dp.callback_query(F.data == "servers_list")
async def servers_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    data = load_servers()
    
    text = "📋 *Список серверов*\n\n"
    text += "🟢 работает | 🟡 есть код | ⚪ нет кода\n\n"
    for server in data["servers"]:
        if server.get("bot_running"):
            status = "🟢"
        elif server.get("has_bot_code"):
            status = "🟡"
        else:
            status = "⚪"
        main = " ⭐ (основной)" if server.get("is_main") else ""
        text += f"{status} *{server['name']}*{main}\n"
        text += f"   IP: `{server['ip']}`\n\n"
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_servers_list_kb()
    )


@dp.callback_query(F.data.startswith("server_info_"))
async def server_info(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("server_info_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer()
    if server.get("bot_running"):
        status = "🟢 Сервис работает"
    elif server.get("has_bot_code"):
        status = "🟡 Есть код, не запущен"
    else:
        status = "⚪ Нет кода бота"
    main = "⭐ Основной сервер" if server.get("is_main") else ""
    
    await callback.message.edit_text(
        f"🖥 *{server['name']}*\n\n"
        f"IP: `{server['ip']}`\n"
        f"Статус: {status}\n"
        f"{main}",
        parse_mode="Markdown",
        reply_markup=get_server_info_kb(ip, has_code=server.get("has_bot_code", False))
    )


@dp.callback_query(F.data.startswith("set_main_"))
async def set_main_server(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("set_main_", "")
    new_main = get_server_by_ip(ip)
    old_main = get_main_server()
    
    if not new_main:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    # Если старый основной сервер имеет работающий бот — спрашиваем об остановке
    if old_main and old_main["ip"] != ip and old_main.get("bot_running"):
        await callback.answer()
        await state.update_data(new_main_ip=ip, old_main_ip=old_main["ip"])
        await state.set_state(DeployStates.confirm_set_main)
        
        await callback.message.edit_text(
            f"⚠️ *Смена основного сервера*\n\n"
            f"Старый: *{old_main['name']}* ({old_main['ip']})\n"
            f"Новый: *{new_main['name']}* ({new_main['ip']})\n\n"
            f"На старом сервере работает VPN-бот.\n"
            f"Остановить его?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, остановить", callback_data="set_main_stop_old")],
                [InlineKeyboardButton(text="⏭ Нет, оставить работать", callback_data="set_main_keep_old")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="servers_list")]
            ])
        )
        return
    
    # Просто меняем основной сервер
    data = load_servers()
    for server in data["servers"]:
        server["is_main"] = (server["ip"] == ip)
    save_servers(data)
    
    await callback.answer("✅ Сервер назначен основным", show_alert=True)
    await servers_list(callback)


@dp.callback_query(F.data == "set_main_stop_old", DeployStates.confirm_set_main)
async def set_main_stop_old(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    data = await state.get_data()
    new_main_ip = data.get("new_main_ip")
    old_main_ip = data.get("old_main_ip")
    old_main = get_server_by_ip(old_main_ip)
    
    await callback.answer()
    status_msg = await callback.message.edit_text(
        "⏳ Останавливаю бота на старом сервере...",
        parse_mode="Markdown"
    )
    
    try:
        # Останавливаем бота на старом сервере
        connect_kwargs = {"host": old_main_ip, "username": "root", "known_hosts": None}
        if old_main and old_main.get("password"):
            connect_kwargs["password"] = old_main["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            await conn.run("systemctl stop vpn-bot", check=False)
        
        # Обновляем статус
        servers_data = load_servers()
        for server in servers_data["servers"]:
            if server["ip"] == old_main_ip:
                server["bot_running"] = False
            server["is_main"] = (server["ip"] == new_main_ip)
        save_servers(servers_data)
        
        await status_msg.edit_text(
            "✅ *Готово!*\n\n"
            "• Бот на старом сервере остановлен\n"
            "• Основной сервер изменён",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb()
        )
    except Exception as e:
        await status_msg.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=get_main_menu_kb()
        )
    
    await state.clear()


@dp.callback_query(F.data == "set_main_keep_old", DeployStates.confirm_set_main)
async def set_main_keep_old(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    data = await state.get_data()
    new_main_ip = data.get("new_main_ip")
    
    # Просто меняем основной сервер, бота не трогаем
    servers_data = load_servers()
    for server in servers_data["servers"]:
        server["is_main"] = (server["ip"] == new_main_ip)
    save_servers(servers_data)
    
    await callback.answer("✅ Сервер назначен основным", show_alert=True)
    await state.clear()
    await servers_list(callback)


@dp.callback_query(F.data.startswith("delete_server_"))
async def delete_server_confirm(callback: CallbackQuery):
    """Показать предупреждение перед удалением сервера"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("delete_server_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer()
    
    warning = ""
    if server.get("is_main"):
        warning = "\n\n⚠️ *ВНИМАНИЕ: Это ОСНОВНОЙ сервер!*"
    elif server.get("bot_running"):
        warning = "\n\n⚠️ На этом сервере работает бот!"
    
    await callback.message.edit_text(
        f"🗑 *Удаление сервера*\n\n"
        f"Ты уверен, что хочешь удалить сервер?\n\n"
        f"🖥 *{server['name']}*\n"
        f"IP: `{server['ip']}`"
        f"{warning}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚠️ Да, удалить", callback_data=f"confirm_delete_{ip}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"server_info_{ip}")]
        ])
    )


@dp.callback_query(F.data.startswith("confirm_delete_"))
async def delete_server_execute(callback: CallbackQuery):
    """Фактическое удаление сервера после подтверждения"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("confirm_delete_", "")
    server = get_server_by_ip(ip)
    server_name = server["name"] if server else ip
    
    data = load_servers()
    data["servers"] = [s for s in data["servers"] if s["ip"] != ip]
    save_servers(data)
    
    await callback.answer(f"🗑 Сервер {server_name} удалён", show_alert=True)
    await servers_list(callback)


@dp.callback_query(F.data.startswith("check_status_"))
async def check_server_status(callback: CallbackQuery):
    """Проверить статус VPN-бота на сервере"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("check_status_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer("Проверяю...")
    
    try:
        connect_kwargs = {"host": ip, "username": "root", "known_hosts": None}
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            # Проверяем статус vpn-bot сервиса
            result = await conn.run("systemctl is-active vpn-bot", check=False)
            is_active = result.stdout.strip() == "active"
            
            # Получаем uptime если работает
            uptime_info = ""
            if is_active:
                uptime_result = await conn.run("systemctl show vpn-bot --property=ActiveEnterTimestamp", check=False)
                uptime_info = uptime_result.stdout.strip().replace("ActiveEnterTimestamp=", "")
            
            # Проверяем наличие кода бота (используем путь из настроек сервера)
            server_vpn_path = get_server_vpn_path(server)
            code_result = await conn.run(f"test -f {server_vpn_path}/bot.py && echo 'yes' || echo 'no'", check=False)
            has_code = code_result.stdout.strip() == "yes"
            
            # Обновляем статус в JSON
            servers_data = load_servers()
            for s in servers_data["servers"]:
                if s["ip"] == ip:
                    s["has_bot_code"] = has_code
                    s["bot_running"] = is_active
            save_servers(servers_data)
            
            status_emoji = "🟢" if is_active else "🔴"
            code_emoji = "✅" if has_code else "❌"
            
            await callback.message.edit_text(
                f"🖥 *{server['name']}*\n\n"
                f"IP: `{ip}`\n"
                f"Код бота: {code_emoji}\n"
                f"Сервис: {status_emoji} {'работает' if is_active else 'остановлен'}\n"
                f"{f'Запущен: {uptime_info}' if uptime_info else ''}",
                parse_mode="Markdown",
                reply_markup=get_server_info_kb(ip, has_code=has_code)
            )
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка подключения к {ip}\n\n{str(e)}",
            reply_markup=get_server_info_kb(ip, has_code=server.get("has_bot_code", False))
        )


@dp.callback_query(F.data.startswith("stop_bot_"))
async def stop_bot_on_server(callback: CallbackQuery):
    """Остановить VPN-бота на сервере"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("stop_bot_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer("Останавливаю...")
    
    try:
        connect_kwargs = {"host": ip, "username": "root", "known_hosts": None}
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            await conn.run("systemctl stop vpn-bot", check=False)
        
        # Обновляем статус
        servers_data = load_servers()
        for s in servers_data["servers"]:
            if s["ip"] == ip:
                s["bot_running"] = False
        save_servers(servers_data)
        
        await callback.message.edit_text(
            f"🛑 Бот на *{server['name']}* остановлен",
            parse_mode="Markdown",
            reply_markup=get_server_info_kb(ip, has_code=True)
        )
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=get_server_info_kb(ip, has_code=server.get("has_bot_code", False))
        )


@dp.callback_query(F.data.startswith("start_bot_"))
async def start_bot_on_server(callback: CallbackQuery):
    """Запустить VPN-бота на сервере"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("start_bot_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer("Запускаю...")
    
    try:
        connect_kwargs = {"host": ip, "username": "root", "known_hosts": None}
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            await conn.run("systemctl start vpn-bot", check=False)
            await asyncio.sleep(2)
            
            # Проверяем статус
            result = await conn.run("systemctl is-active vpn-bot", check=False)
            is_active = result.stdout.strip() == "active"
        
        # Обновляем статус
        servers_data = load_servers()
        for s in servers_data["servers"]:
            if s["ip"] == ip:
                s["bot_running"] = is_active
        save_servers(servers_data)
        
        if is_active:
            await callback.message.edit_text(
                f"▶️ Бот на *{server['name']}* запущен",
                parse_mode="Markdown",
                reply_markup=get_server_info_kb(ip, has_code=True)
            )
        else:
            await callback.message.edit_text(
                f"⚠️ Бот не запустился. Проверь логи.",
                reply_markup=get_server_info_kb(ip, has_code=True)
            )
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=get_server_info_kb(ip, has_code=server.get("has_bot_code", False))
        )


@dp.callback_query(F.data.startswith("update_code_"))
async def update_code_on_server(callback: CallbackQuery):
    """Обновить код бота на сервере через git pull"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("update_code_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer("Обновляю код...")
    
    try:
        connect_kwargs = {"host": ip, "username": "root", "known_hosts": None}
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            # Останавливаем бота
            await conn.run("systemctl stop vpn-bot", check=False)
            
            # Обновляем код через git pull
            result = await conn.run(f"cd {REPO_PATH} && git pull", check=False)
            git_output = result.stdout.strip() if result.stdout else result.stderr.strip()
            
            # Запускаем бота
            await conn.run("systemctl start vpn-bot", check=False)
            await asyncio.sleep(2)
            
            # Проверяем статус
            status_result = await conn.run("systemctl is-active vpn-bot", check=False)
            is_active = status_result.stdout.strip() == "active"
        
        # Обновляем статус
        servers_data = load_servers()
        for s in servers_data["servers"]:
            if s["ip"] == ip:
                s["bot_running"] = is_active
        save_servers(servers_data)
        
        status_emoji = "🟢" if is_active else "🔴"
        
        await callback.message.edit_text(
            f"📥 *Обновление кода на {server['name']}*\n\n"
            f"```\n{git_output[:500]}\n```\n\n"
            f"Статус бота: {status_emoji} {'работает' if is_active else 'не запущен'}",
            parse_mode="Markdown",
            reply_markup=get_server_info_kb(ip, has_code=True)
        )
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка обновления: {str(e)}",
            reply_markup=get_server_info_kb(ip, has_code=True)
        )


@dp.callback_query(F.data.startswith("push_db_"))
async def push_db_to_server(callback: CallbackQuery):
    """Загрузить БД на сервер с резервного сервера (без токенов ботов)"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("push_db_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    # Проверяем есть ли сохранённая БД
    latest_db = f"{DB_BACKUP_PATH}/vpn_bot_latest.db"
    if not os.path.exists(latest_db):
        await callback.answer("❌ Нет сохранённой БД. Сначала синхронизируй БД с основного сервера.", show_alert=True)
        return
    
    await callback.answer("Обновляю БД...")
    
    status_msg = await callback.message.edit_text(
        f"🗄 *Обновление БД на {server['name']}*\n\n"
        f"⏳ Подготовка БД (очистка токенов)...",
        parse_mode="Markdown"
    )
    
    try:
        import shutil
        import tempfile
        
        # Создаём временную копию БД для очистки
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
            temp_db_path = tmp.name
        
        shutil.copy2(latest_db, temp_db_path)
        
        # Очищаем токены ботов и другие чувствительные данные
        import subprocess
        cleanup_commands = [
            "DELETE FROM bot_instances;",  # Токены дополнительных ботов
            "DELETE FROM bot_settings;",   # Настройки ботов
            "DELETE FROM log_channels;",   # Каналы логов
        ]
        
        for cmd in cleanup_commands:
            subprocess.run(
                ["sqlite3", temp_db_path, cmd],
                capture_output=True,
                check=False
            )
        
        await status_msg.edit_text(
            f"🗄 *Обновление БД на {server['name']}*\n\n"
            f"⏳ Загрузка БД на сервер...",
            parse_mode="Markdown"
        )
        
        connect_kwargs = {"host": ip, "username": "root", "known_hosts": None}
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        server_vpn_path = get_server_vpn_path(server)
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            # Останавливаем бота
            await conn.run("systemctl stop vpn-bot", check=False)
            
            # Загружаем очищенную БД
            await asyncssh.scp(temp_db_path, (conn, f"{server_vpn_path}/vpn_bot.db"))
            
            # Запускаем бота
            await conn.run("systemctl start vpn-bot", check=False)
            await asyncio.sleep(2)
            
            # Проверяем статус
            status_result = await conn.run("systemctl is-active vpn-bot", check=False)
            is_active = status_result.stdout.strip() == "active"
        
        # Удаляем временный файл
        os.unlink(temp_db_path)
        
        # Обновляем статус
        servers_data = load_servers()
        for s in servers_data["servers"]:
            if s["ip"] == ip:
                s["bot_running"] = is_active
        save_servers(servers_data)
        
        status_emoji = "🟢" if is_active else "🔴"
        
        await status_msg.edit_text(
            f"✅ *БД обновлена на {server['name']}*\n\n"
            f"📊 Загружены данные клиентов\n"
            f"🔒 Токены ботов очищены\n\n"
            f"Статус бота: {status_emoji} {'работает' if is_active else 'не запущен'}",
            parse_mode="Markdown",
            reply_markup=get_server_info_kb(ip, has_code=True)
        )
    
    except Exception as e:
        # Удаляем временный файл если он существует
        if 'temp_db_path' in locals() and os.path.exists(temp_db_path):
            os.unlink(temp_db_path)
        
        await status_msg.edit_text(
            f"❌ Ошибка обновления БД: {str(e)}",
            reply_markup=get_server_info_kb(ip, has_code=True)
        )


@dp.callback_query(F.data.startswith("server_stats_"))
async def server_stats(callback: CallbackQuery):
    """Показать статистику загрузки сервера"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("server_stats_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer("Загружаю статистику...")
    
    try:
        connect_kwargs = {"host": ip, "username": "root", "known_hosts": None}
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            # CPU загрузка
            cpu_result = await conn.run("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'", check=False)
            cpu_usage = cpu_result.stdout.strip() if cpu_result.stdout else "N/A"
            
            # Память
            mem_result = await conn.run("free -h | awk '/^Mem:/ {print $2, $3, $4}'", check=False)
            mem_parts = mem_result.stdout.strip().split() if mem_result.stdout else ["N/A", "N/A", "N/A"]
            mem_total = mem_parts[0] if len(mem_parts) > 0 else "N/A"
            mem_used = mem_parts[1] if len(mem_parts) > 1 else "N/A"
            mem_free = mem_parts[2] if len(mem_parts) > 2 else "N/A"
            
            # Диск
            disk_result = await conn.run("df -h / | awk 'NR==2 {print $2, $3, $4, $5}'", check=False)
            disk_parts = disk_result.stdout.strip().split() if disk_result.stdout else ["N/A", "N/A", "N/A", "N/A"]
            disk_total = disk_parts[0] if len(disk_parts) > 0 else "N/A"
            disk_used = disk_parts[1] if len(disk_parts) > 1 else "N/A"
            disk_free = disk_parts[2] if len(disk_parts) > 2 else "N/A"
            disk_percent = disk_parts[3] if len(disk_parts) > 3 else "N/A"
            
            # Uptime
            uptime_result = await conn.run("uptime -p", check=False)
            uptime = uptime_result.stdout.strip() if uptime_result.stdout else "N/A"
            
            # Load average
            load_result = await conn.run("cat /proc/loadavg | awk '{print $1, $2, $3}'", check=False)
            load_avg = load_result.stdout.strip() if load_result.stdout else "N/A"
        
        await callback.message.edit_text(
            f"📊 *Статистика сервера {server['name']}*\n\n"
            f"🖥 *CPU:* {cpu_usage}%\n"
            f"📈 *Load Average:* {load_avg}\n\n"
            f"💾 *Память:*\n"
            f"  • Всего: {mem_total}\n"
            f"  • Использовано: {mem_used}\n"
            f"  • Свободно: {mem_free}\n\n"
            f"💿 *Диск (/):*\n"
            f"  • Всего: {disk_total}\n"
            f"  • Использовано: {disk_used} ({disk_percent})\n"
            f"  • Свободно: {disk_free}\n\n"
            f"⏱ *Uptime:* {uptime}",
            parse_mode="Markdown",
            reply_markup=get_server_info_kb(ip, has_code=server.get("has_bot_code", False))
        )
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка получения статистики: {str(e)}",
            reply_markup=get_server_info_kb(ip, has_code=server.get("has_bot_code", False))
        )


# Словарь быстрых команд (короткий код -> полная команда)
QUICK_COMMANDS = {
    "status": "systemctl status vpn-bot",
    "logs": "journalctl -u vpn-bot -n 20 --no-pager",
    "restart": "systemctl restart vpn-bot",
    "start": "systemctl start vpn-bot",
    "stop": "systemctl stop vpn-bot",
    "wg": "wg show",
    "peers": "wg show wg0 | grep -c peer",
    "mem": "free -h",
    "disk": "df -h /",
    "files": "ls -la /root/vpn_bot/ 2>/dev/null || ls -la /root/agdeweb/vpn_bot/",
}


def get_terminal_kb(ip: str, show_commands: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура для режима терминала с быстрыми командами"""
    buttons = []
    
    if show_commands:
        # Быстрые команды для бота
        buttons.append([InlineKeyboardButton(text="📊 Статус бота", callback_data=f"tc_{ip}_status")])
        buttons.append([InlineKeyboardButton(text="📜 Логи бота", callback_data=f"tc_{ip}_logs")])
        buttons.append([InlineKeyboardButton(text="🔄 Перезапуск", callback_data=f"tc_{ip}_restart")])
        buttons.append([
            InlineKeyboardButton(text="▶️ Старт", callback_data=f"tc_{ip}_start"),
            InlineKeyboardButton(text="⏹ Стоп", callback_data=f"tc_{ip}_stop")
        ])
        # WireGuard команды
        buttons.append([
            InlineKeyboardButton(text="🔐 WG", callback_data=f"tc_{ip}_wg"),
            InlineKeyboardButton(text="👥 Пиры", callback_data=f"tc_{ip}_peers")
        ])
        # Система
        buttons.append([
            InlineKeyboardButton(text="💾 RAM", callback_data=f"tc_{ip}_mem"),
            InlineKeyboardButton(text="💿 Диск", callback_data=f"tc_{ip}_disk")
        ])
        buttons.append([InlineKeyboardButton(text="📁 Файлы", callback_data=f"tc_{ip}_files")])
    
    buttons.append([InlineKeyboardButton(text="❌ Выход", callback_data=f"texit_{ip}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(F.data.startswith("terminal_"))
async def terminal_start(callback: CallbackQuery, state: FSMContext):
    """Войти в режим терминала"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("terminal_", "")
    
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer()
    await state.update_data(terminal_ip=ip, terminal_server=server)
    await state.set_state(DeployStates.terminal_mode)
    
    await callback.message.edit_text(
        f"💻 *Терминал: {server['name']}*\n\n"
        f"Ты в режиме терминала. Все сообщения будут выполняться как команды на сервере.\n\n"
        f"⚠️ *Будь осторожен!* Команды выполняются от root.\n\n"
        f"Используй кнопки ниже или пиши команды вручную:",
        parse_mode="Markdown",
        reply_markup=get_terminal_kb(ip)
    )


@dp.callback_query(F.data.startswith("texit_"))
async def terminal_exit(callback: CallbackQuery, state: FSMContext):
    """Выйти из режима терминала"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("texit_", "")
    server = get_server_by_ip(ip)
    
    await state.clear()
    await callback.answer("Вышел из терминала")
    
    if server:
        await callback.message.edit_text(
            f"🖥 *{server['name']}*\n\n"
            f"IP: `{server['ip']}`\n"
            f"Выбери действие:",
            parse_mode="Markdown",
            reply_markup=get_server_info_kb(ip, has_code=server.get("has_bot_code", False))
        )
    else:
        await servers_list(callback)


def fix_command_case(command: str) -> str:
    """Исправить регистр известных команд (Systemctl -> systemctl)"""
    known_commands = [
        "systemctl", "journalctl", "wg", "ls", "cat", "df", "free", 
        "top", "htop", "ps", "grep", "tail", "head", "nano", "vim",
        "cd", "pwd", "mkdir", "rm", "cp", "mv", "chmod", "chown",
        "apt", "apt-get", "pip", "python", "python3", "git", "ssh",
        "scp", "rsync", "curl", "wget", "ping", "netstat", "ss",
        "iptables", "ufw", "service", "reboot", "shutdown"
    ]
    
    parts = command.split()
    if parts:
        first_word = parts[0].lower()
        if first_word in known_commands:
            parts[0] = first_word
            return " ".join(parts)
    return command


@dp.callback_query(F.data.startswith("tc_"))
async def terminal_quick_command(callback: CallbackQuery, state: FSMContext):
    """Выполнить быструю команду из кнопки"""
    if not is_admin(callback.from_user.id):
        return
    
    # Парсим: tc_IP_CODE
    data_parts = callback.data.split("_", 2)
    if len(data_parts) < 3:
        await callback.answer("Ошибка команды", show_alert=True)
        return
    
    ip = data_parts[1]
    cmd_code = data_parts[2]
    
    # Получаем полную команду из словаря
    command = QUICK_COMMANDS.get(cmd_code)
    if not command:
        await callback.answer("Неизвестная команда", show_alert=True)
        return
    
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer(f"Выполняю: {command[:30]}...")
    
    try:
        connect_kwargs = {"host": ip, "username": "root", "known_hosts": None}
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await asyncio.wait_for(
                conn.run(command, check=False),
                timeout=30
            )
            
            output = result.stdout if result.stdout else ""
            error = result.stderr if result.stderr else ""
            exit_code = result.exit_status
        
        # Формируем ответ
        response = f"💻 *Команда:* `{command}`\n"
        response += f"📤 *Exit code:* {exit_code}\n\n"
        
        if output:
            if len(output) > 3000:
                output = output[:3000] + "\n... (обрезано)"
            response += f"```\n{output}\n```"
        
        if error:
            if len(error) > 1000:
                error = error[:1000] + "\n... (обрезано)"
            response += f"\n⚠️ *Stderr:*\n```\n{error}\n```"
        
        if not output and not error:
            response += "_Команда выполнена без вывода_"
        
        await callback.message.edit_text(
            response,
            parse_mode="Markdown",
            reply_markup=get_terminal_kb(ip)
        )
    
    except asyncio.TimeoutError:
        await callback.message.edit_text(
            f"⏱ *Таймаут!* Команда выполняется дольше 30 секунд.",
            parse_mode="Markdown",
            reply_markup=get_terminal_kb(ip)
        )
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ *Ошибка:* {str(e)}",
            parse_mode="Markdown",
            reply_markup=get_terminal_kb(ip)
        )


@dp.message(DeployStates.terminal_mode)
async def terminal_execute(message: Message, state: FSMContext):
    """Выполнить команду в терминале"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    ip = data.get("terminal_ip")
    server = data.get("terminal_server")
    
    if not ip or not server:
        await message.answer("❌ Сессия терминала потеряна. Начни заново.")
        await state.clear()
        return
    
    command = message.text.strip()
    
    # Исправляем регистр команд (Systemctl -> systemctl)
    command = fix_command_case(command)
    
    # Защита от опасных команд
    dangerous_commands = ["rm -rf /", "mkfs", "dd if=", "> /dev/sd"]
    for dangerous in dangerous_commands:
        if dangerous in command:
            await message.answer(f"⛔ Команда заблокирована: `{dangerous}`", parse_mode="Markdown")
            return
    
    status_msg = await message.answer(f"⏳ Выполняю: `{command}`...", parse_mode="Markdown")
    
    try:
        connect_kwargs = {"host": ip, "username": "root", "known_hosts": None}
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await asyncio.wait_for(
                conn.run(command, check=False),
                timeout=30
            )
            
            output = result.stdout if result.stdout else ""
            error = result.stderr if result.stderr else ""
            exit_code = result.exit_status
        
        # Формируем ответ
        response = f"💻 *Команда:* `{command}`\n"
        response += f"📤 *Exit code:* {exit_code}\n\n"
        
        if output:
            # Ограничиваем вывод 3000 символами
            if len(output) > 3000:
                output = output[:3000] + "\n... (обрезано)"
            response += f"```\n{output}\n```"
        
        if error:
            if len(error) > 1000:
                error = error[:1000] + "\n... (обрезано)"
            response += f"\n⚠️ *Stderr:*\n```\n{error}\n```"
        
        if not output and not error:
            response += "_Команда выполнена без вывода_"
        
        await status_msg.edit_text(
            response,
            parse_mode="Markdown",
            reply_markup=get_terminal_kb(ip)
        )
    
    except asyncio.TimeoutError:
        await status_msg.edit_text(
            f"⏱ *Таймаут!* Команда выполняется дольше 30 секунд.\n\n"
            f"Команда: `{command}`",
            parse_mode="Markdown",
            reply_markup=get_terminal_kb(ip)
        )
    
    except Exception as e:
        await status_msg.edit_text(
            f"❌ *Ошибка:* {str(e)}\n\n"
            f"Команда: `{command}`",
            parse_mode="Markdown",
            reply_markup=get_terminal_kb(ip)
        )


@dp.callback_query(F.data.startswith("change_token_"))
async def change_token_start(callback: CallbackQuery, state: FSMContext):
    """Начать смену токена бота"""
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("change_token_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer()
    await state.update_data(change_token_ip=ip)
    await state.set_state(DeployStates.change_bot_token)
    
    await callback.message.edit_text(
        f"🔑 *Смена токена бота на {server['name']}*\n\n"
        f"Введи новый токен бота (получи у @BotFather):\n\n"
        f"_Формат: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz_",
        parse_mode="Markdown",
        reply_markup=get_cancel_kb()
    )


@dp.message(DeployStates.change_bot_token)
async def change_token_process(message: Message, state: FSMContext):
    """Обработка нового токена"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    ip = data.get("change_token_ip")
    server = get_server_by_ip(ip)
    
    if not server:
        await message.answer("Сервер не найден")
        await state.clear()
        return
    
    new_token = message.text.strip()
    
    # Проверяем формат токена
    if ":" not in new_token or len(new_token) < 40:
        await message.answer(
            "❌ Неверный формат токена.\n\n"
            "Токен должен выглядеть так:\n"
            "`1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`",
            parse_mode="Markdown",
            reply_markup=get_cancel_kb()
        )
        return
    
    status_msg = await message.answer("⏳ Меняю токен...")
    
    try:
        connect_kwargs = {"host": ip, "username": "root", "known_hosts": None}
        if server.get("password"):
            connect_kwargs["password"] = server["password"]
        
        async with asyncssh.connect(**connect_kwargs) as conn:
            # Останавливаем бота
            await conn.run("systemctl stop vpn-bot", check=False)
            
            # Читаем текущий .env
            result = await conn.run(f"cat {VPN_BOT_PATH}/.env", check=False)
            env_content = result.stdout if result.stdout else ""
            
            # Заменяем токен
            import re
            if "BOT_TOKEN=" in env_content:
                new_env = re.sub(r'BOT_TOKEN=.*', f'BOT_TOKEN={new_token}', env_content)
            else:
                new_env = f"BOT_TOKEN={new_token}\n" + env_content
            
            # Записываем новый .env
            escaped_env = new_env.replace("'", "'\\''")
            await conn.run(f"echo '{escaped_env}' > {VPN_BOT_PATH}/.env", check=False)
            
            # Запускаем бота
            await conn.run("systemctl start vpn-bot", check=False)
            await asyncio.sleep(3)
            
            # Проверяем статус
            status_result = await conn.run("systemctl is-active vpn-bot", check=False)
            is_active = status_result.stdout.strip() == "active"
        
        # Обновляем статус
        servers_data = load_servers()
        for s in servers_data["servers"]:
            if s["ip"] == ip:
                s["bot_running"] = is_active
        save_servers(servers_data)
        
        await state.clear()
        
        if is_active:
            await status_msg.edit_text(
                f"✅ *Токен успешно изменён!*\n\n"
                f"Сервер: {server['name']}\n"
                f"Статус: 🟢 Бот работает",
                parse_mode="Markdown",
                reply_markup=get_server_info_kb(ip, has_code=True)
            )
        else:
            await status_msg.edit_text(
                f"⚠️ *Токен изменён, но бот не запустился*\n\n"
                f"Проверь логи на сервере.",
                parse_mode="Markdown",
                reply_markup=get_server_info_kb(ip, has_code=True)
            )
    
    except Exception as e:
        await state.clear()
        await status_msg.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=get_server_info_kb(ip, has_code=True)
        )


@dp.callback_query(F.data == "add_server")
async def add_server_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    await state.set_state(DeployStates.add_server_name)
    await callback.message.edit_text(
        "➕ *Добавление сервера*\n\n"
        "Введи название сервера (например: Finland, Germany):",
        parse_mode="Markdown",
        reply_markup=get_cancel_kb()
    )


@dp.message(DeployStates.add_server_name)
async def add_server_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    await state.update_data(server_name=message.text.strip())
    await state.set_state(DeployStates.add_server_ip)
    await message.answer(
        "Введи IP-адрес сервера:",
        reply_markup=get_cancel_kb()
    )


@dp.message(DeployStates.add_server_ip)
async def add_server_ip(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    ip = message.text.strip()
    if len(ip.split(".")) != 4:
        await message.answer("❌ Неверный формат IP. Попробуй ещё раз:")
        return
    
    await state.update_data(server_ip=ip)
    await state.set_state(DeployStates.add_server_password)
    await message.answer(
        "Введи пароль root (или `-` если используешь SSH-ключ):",
        reply_markup=get_cancel_kb()
    )


@dp.message(DeployStates.add_server_password)
async def add_server_password(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    password = message.text.strip()
    
    # Удаляем сообщение с паролем
    try:
        await message.delete()
    except:
        pass
    
    await state.update_data(server_password=None if password == "-" else password)
    await state.set_state(DeployStates.add_server_path)
    await message.answer(
        "Введи путь к VPN-боту на сервере:\n\n"
        f"• `-` — стандартный путь (`{VPN_BOT_PATH}`)\n"
        "• `/root/vpn_bot` — для старых серверов\n"
        "• Или свой путь",
        parse_mode="Markdown",
        reply_markup=get_cancel_kb()
    )


@dp.message(DeployStates.add_server_path)
async def add_server_path(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    path = message.text.strip()
    data = await state.get_data()
    
    # Определяем путь
    if path == "-":
        vpn_path = None  # Будет использоваться дефолтный
    else:
        vpn_path = path
    
    # Сохраняем сервер
    servers_data = load_servers()
    new_server = {
        "name": data["server_name"],
        "ip": data["server_ip"],
        "password": data.get("server_password"),
        "is_main": False,
        "has_bot_code": False,
        "bot_running": False
    }
    if vpn_path:
        new_server["vpn_bot_path"] = vpn_path
    
    servers_data["servers"].append(new_server)
    save_servers(servers_data)
    
    await state.clear()
    path_info = vpn_path if vpn_path else VPN_BOT_PATH
    await message.answer(
        f"✅ Сервер *{data['server_name']}* добавлен!\n\n"
        f"IP: `{data['server_ip']}`\n"
        f"Путь: `{path_info}`",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb()
    )


# ============ Связывание серверов (SSH-ключи) ============

@dp.callback_query(F.data == "link_servers")
async def link_servers_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    await callback.message.edit_text(
        "🔗 *Связывание серверов*\n\n"
        "Эта функция настраивает SSH-ключи между серверами, "
        "чтобы они могли подключаться друг к другу без пароля.\n\n"
        "Выбери *исходный* сервер (откуда будет доступ):",
        parse_mode="Markdown",
        reply_markup=get_servers_list_kb(for_link=True)
    )
    await state.set_state(DeployStates.link_source_server)


@dp.callback_query(F.data.startswith("link_select_"), DeployStates.link_source_server)
async def link_select_source(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("link_select_", "")
    server = get_server_by_ip(ip)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer()
    await state.update_data(link_source=server)
    await state.set_state(DeployStates.link_target_server)
    
    await callback.message.edit_text(
        f"🔗 *Связывание серверов*\n\n"
        f"Исходный: *{server['name']}*\n\n"
        f"Выбери *целевой* сервер (куда будет доступ):",
        parse_mode="Markdown",
        reply_markup=get_servers_list_kb(for_link=True)
    )


@dp.callback_query(F.data.startswith("link_select_"), DeployStates.link_target_server)
async def link_select_target(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("link_select_", "")
    target = get_server_by_ip(ip)
    data = await state.get_data()
    source = data.get("link_source")
    
    if not target or not source:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    if source["ip"] == target["ip"]:
        await callback.answer("Нельзя связать сервер сам с собой", show_alert=True)
        return
    
    await callback.answer()
    
    status_msg = await callback.message.edit_text(
        f"🔗 *Связывание серверов*\n\n"
        f"⏳ Настройка SSH-ключей...\n"
        f"{source['name']} → {target['name']}",
        parse_mode="Markdown"
    )
    
    try:
        # Подключаемся к исходному серверу
        source_kwargs = {"host": source["ip"], "username": "root", "known_hosts": None}
        if source.get("password"):
            source_kwargs["password"] = source["password"]
        
        async with asyncssh.connect(**source_kwargs) as source_conn:
            # Генерируем ключ если нет
            await source_conn.run(
                "test -f ~/.ssh/id_rsa || ssh-keygen -t rsa -N '' -f ~/.ssh/id_rsa",
                check=False
            )
            
            # Получаем публичный ключ
            result = await source_conn.run("cat ~/.ssh/id_rsa.pub", check=False)
            pub_key = result.stdout.strip()
            
            if not pub_key:
                raise Exception("Не удалось получить публичный ключ")
        
        # Подключаемся к целевому серверу и добавляем ключ
        target_kwargs = {"host": target["ip"], "username": "root", "known_hosts": None}
        if target.get("password"):
            target_kwargs["password"] = target["password"]
        
        async with asyncssh.connect(**target_kwargs) as target_conn:
            # Добавляем ключ в authorized_keys
            await target_conn.run(
                f"mkdir -p ~/.ssh && echo '{pub_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys",
                check=False
            )
        
        await status_msg.edit_text(
            f"✅ *Серверы связаны!*\n\n"
            f"*{source['name']}* теперь может подключаться к *{target['name']}* без пароля.\n\n"
            f"SSH-ключ добавлен.",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb()
        )
    
    except Exception as e:
        await status_msg.edit_text(
            f"❌ *Ошибка связывания*\n\n{str(e)}",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb()
        )
    
    await state.clear()


# ============ Помощь ============

@dp.callback_query(F.data == "help")
async def help_info(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    await callback.message.edit_text(
        "ℹ️ *Помощь*\n\n"
        "*🚀 Развернуть VPN-бота*\n"
        "Установить VPN-бота на выбранный сервер. "
        "Нужен только токен бота.\n\n"
        "*🔄 Синхронизировать БД*\n"
        "Скопировать БД с основного сервера. "
        "При деплое эта БД будет использована.\n\n"
        "*🖥 Управление серверами*\n"
        "Добавить/удалить серверы, назначить основной.\n\n"
        "*🔗 Связать серверы*\n"
        "Настроить SSH-ключи между серверами для "
        "подключения без пароля.\n\n"
        "*Легенда:*\n"
        "🟢 — VPN-бот работает\n"
        "⚪ — VPN-бот не установлен\n"
        "⭐ — Основной сервер",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb()
    )


async def auto_backup_db():
    """Автоматическая синхронизация БД каждые N часов"""
    while True:
        await asyncio.sleep(AUTO_BACKUP_INTERVAL_HOURS * 3600)
        
        main_server = get_main_server()
        if not main_server:
            logger.warning("Автобэкап: основной сервер не найден")
            continue
        
        try:
            os.makedirs(DB_BACKUP_PATH, exist_ok=True)
            
            connect_kwargs = {
                "host": main_server["ip"],
                "username": "root",
                "known_hosts": None
            }
            if main_server.get("password"):
                connect_kwargs["password"] = main_server["password"]
            
            async with asyncssh.connect(**connect_kwargs) as conn:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_file = f"{DB_BACKUP_PATH}/vpn_bot_{timestamp}.db"
                latest_file = f"{DB_BACKUP_PATH}/vpn_bot_latest.db"
                
                await asyncssh.scp(
                    (conn, f"{VPN_BOT_PATH}/vpn_bot.db"),
                    backup_file
                )
                await asyncssh.scp(
                    (conn, f"{VPN_BOT_PATH}/vpn_bot.db"),
                    latest_file
                )
                
                file_size = os.path.getsize(backup_file)
                logger.info(f"Автобэкап БД: {backup_file} ({file_size} bytes)")
                
                # Удаляем старые бэкапы (оставляем последние 10)
                import glob
                backups = sorted(glob.glob(f"{DB_BACKUP_PATH}/vpn_bot_*.db"))
                backups = [b for b in backups if "latest" not in b]
                if len(backups) > 10:
                    for old_backup in backups[:-10]:
                        os.remove(old_backup)
                        logger.info(f"Удалён старый бэкап: {old_backup}")
        
        except Exception as e:
            logger.error(f"Ошибка автобэкапа: {e}")


async def main():
    # Создаём файл серверов если нет
    if not os.path.exists(SERVERS_FILE):
        save_servers(load_servers())
    
    # Запускаем автобэкап в фоне
    asyncio.create_task(auto_backup_db())
    
    logger.info(f"Deploy Bot запущен (автобэкап каждые {AUTO_BACKUP_INTERVAL_HOURS}ч)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
