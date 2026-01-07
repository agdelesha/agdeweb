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
BOT_TOKEN = os.getenv("BOT_TOKEN", "8478281326:AAE-Z19m_1lXyFosuTTSMNm-qygN_LZUFrM")
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
    # Связывание серверов
    link_source_server = State()
    link_target_server = State()
    # Смена основного сервера
    confirm_set_main = State()


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
                "bot_running": True  # Сервис работает
            }
        ]
    }


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


def get_server_info_kb(ip: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_status_{ip}")],
        [InlineKeyboardButton(text="⭐ Сделать основным", callback_data=f"set_main_{ip}")],
        [InlineKeyboardButton(text="🛑 Остановить бота", callback_data=f"stop_bot_{ip}")],
        [InlineKeyboardButton(text="▶️ Запустить бота", callback_data=f"start_bot_{ip}")],
        [InlineKeyboardButton(text="🗑 Удалить сервер", callback_data=f"delete_server_{ip}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="servers_list")]
    ]
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
    await message.answer(
        "🤖 *Deploy Bot*\n\n"
        "Управление серверами и деплой VPN-бота.\n\n"
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
    await callback.message.edit_text(
        "🤖 *Deploy Bot*\n\n"
        "Управление серверами и деплой VPN-бота.\n\n"
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
            
            await asyncssh.scp(
                (conn, f"{VPN_BOT_PATH}/vpn_bot.db"),
                backup_file
            )
            await asyncssh.scp(
                (conn, f"{VPN_BOT_PATH}/vpn_bot.db"),
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
        reply_markup=get_server_info_kb(ip)
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
async def delete_server(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    ip = callback.data.replace("delete_server_", "")
    data = load_servers()
    
    data["servers"] = [s for s in data["servers"] if s["ip"] != ip]
    save_servers(data)
    
    await callback.answer("🗑 Сервер удалён", show_alert=True)
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
            
            # Проверяем наличие кода бота
            code_result = await conn.run(f"test -f {VPN_BOT_PATH}/bot.py && echo 'yes' || echo 'no'", check=False)
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
                reply_markup=get_server_info_kb(ip)
            )
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка подключения к {ip}\n\n{str(e)}",
            reply_markup=get_server_info_kb(ip)
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
            reply_markup=get_server_info_kb(ip)
        )
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=get_server_info_kb(ip)
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
                reply_markup=get_server_info_kb(ip)
            )
        else:
            await callback.message.edit_text(
                f"⚠️ Бот не запустился. Проверь логи.",
                reply_markup=get_server_info_kb(ip)
            )
    
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            reply_markup=get_server_info_kb(ip)
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
    
    data = await state.get_data()
    
    # Сохраняем сервер
    servers_data = load_servers()
    servers_data["servers"].append({
        "name": data["server_name"],
        "ip": data["server_ip"],
        "password": None if password == "-" else password,
        "is_main": False,
        "has_bot_code": False,
        "bot_running": False
    })
    save_servers(servers_data)
    
    await state.clear()
    await message.answer(
        f"✅ Сервер *{data['server_name']}* добавлен!\n\n"
        f"IP: `{data['server_ip']}`",
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
