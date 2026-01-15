"""
AGDE Deploy Bot - Бот для корпоративных клиентов
Позволяет клиентам устанавливать WG, AWG, V2Ray и VPN бота на свои серверы
"""
import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from database import init_db, get_session, User, Server
from keyboards.user_kb import (
    get_phone_kb, get_main_menu_kb, get_server_menu_kb,
    get_servers_list_kb, get_cancel_kb, get_confirm_kb, get_back_to_server_kb
)
from services.installer import ServerInstaller

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_ADMIN_ID = 906888481  # Главный фиксированный админ

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


class UserStates(StatesGroup):
    waiting_phone = State()
    waiting_server_ip = State()
    waiting_server_password = State()
    waiting_bot_token = State()


# ============ Вспомогательные функции ============

def get_user_by_telegram_id(telegram_id: int) -> User:
    """Получить пользователя по Telegram ID"""
    session = get_session()
    user = session.query(User).filter(User.telegram_id == telegram_id).first()
    session.close()
    return user


def create_user(telegram_id: int, username: str, first_name: str, last_name: str, phone: str) -> User:
    """Создать нового пользователя"""
    session = get_session()
    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        phone=phone
    )
    session.add(user)
    session.commit()
    user_id = user.id
    session.close()
    return get_user_by_telegram_id(telegram_id)


def get_server_by_id(server_id: int) -> Server:
    """Получить сервер по ID"""
    session = get_session()
    server = session.query(Server).filter(Server.id == server_id).first()
    session.close()
    return server


def get_user_servers(telegram_id: int) -> list:
    """Получить серверы пользователя"""
    session = get_session()
    user = session.query(User).filter(User.telegram_id == telegram_id).first()
    if user:
        servers = list(user.servers)
    else:
        servers = []
    session.close()
    return servers


def add_server(user_telegram_id: int, ip: str, password: str) -> Server:
    """Добавить сервер пользователю"""
    session = get_session()
    user = session.query(User).filter(User.telegram_id == user_telegram_id).first()
    if not user:
        session.close()
        return None
    
    server = Server(
        user_id=user.id,
        ip=ip,
        password=password
    )
    session.add(server)
    session.commit()
    server_id = server.id
    session.close()
    return get_server_by_id(server_id)


def update_server_status(server_id: int, **kwargs):
    """Обновить статус установок на сервере"""
    session = get_session()
    server = session.query(Server).filter(Server.id == server_id).first()
    if server:
        for key, value in kwargs.items():
            if hasattr(server, key):
                setattr(server, key, value)
        session.commit()
    session.close()


def delete_server_by_id(server_id: int):
    """Удалить сервер"""
    session = get_session()
    server = session.query(Server).filter(Server.id == server_id).first()
    if server:
        session.delete(server)
        session.commit()
    session.close()


# ============ Приветствие и регистрация ============

WELCOME_TEXT = """
🚀 *Добро пожаловать в AGDE Deploy Bot!*

Этот бот создан для корпоративных клиентов и позволяет:

🔐 *Установить WireGuard* — классический VPN протокол
🛡️ *Установить AmneziaWG* — защищённый от блокировок VPN
🚀 *Установить V2Ray/Xray* — продвинутый прокси с маскировкой
🤖 *Развернуть VPN-бота* — полноценный Telegram бот для управления VPN

*Как это работает:*
1. Вы регистрируетесь, отправив номер телефона
2. Добавляете свой сервер (IP + пароль root)
3. Выбираете что установить
4. Бот автоматически всё настроит!

При установке VPN-бота — *вы становитесь его администратором*.

Для начала работы отправьте свой номер телефона 👇
"""


def get_all_users() -> list:
    """Получить всех пользователей"""
    session = get_session()
    users = session.query(User).all()
    session.close()
    return users


def get_admin_menu_kb():
    """Клавиатура админа"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="🖥 Все серверы", callback_data="admin_servers")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработка команды /start"""
    await state.clear()
    
    # Проверка на главного админа
    if message.from_user.id == MAIN_ADMIN_ID:
        users = get_all_users()
        session = get_session()
        servers_count = session.query(Server).count()
        session.close()
        
        users_text = ""
        if users:
            for u in users:
                servers = get_user_servers(u.telegram_id)
                users_text += f"\n• {u.first_name} (@{u.username or 'нет'})\n"
                users_text += f"  📱 {u.phone}\n"
                users_text += f"  🖥 Серверов: {len(servers)}\n"
        else:
            users_text = "\nПока нет зарегистрированных клиентов."
        
        await message.answer(
            f"👑 *Админ-панель AGDE Deploy Bot*\n\n"
            f"📊 *Статистика:*\n"
            f"👥 Пользователей: {len(users)}\n"
            f"🖥 Серверов: {servers_count}\n\n"
            f"*Клиенты:*{users_text}",
            parse_mode="Markdown",
            reply_markup=get_admin_menu_kb()
        )
        return
    
    user = get_user_by_telegram_id(message.from_user.id)
    
    if user:
        # Пользователь уже зарегистрирован
        servers = get_user_servers(message.from_user.id)
        if servers:
            server = servers[0]  # Показываем первый сервер
            installed = []
            if server.wg_installed:
                installed.append("✅ WireGuard")
            if server.awg_installed:
                installed.append("✅ AmneziaWG")
            if server.v2ray_installed:
                installed.append("✅ V2Ray")
            if server.vpn_bot_installed:
                installed.append("✅ VPN Bot")
            
            installed_text = "\n".join(installed) if installed else "Ничего не установлено"
            
            await message.answer(
                f"👋 С возвращением, *{user.first_name}*!\n\n"
                f"📱 Телефон: `{user.phone}`\n"
                f"🖥 Серверов: {len(servers)}\n\n"
                f"*Установлено на {server.ip}:*\n{installed_text}",
                parse_mode="Markdown",
                reply_markup=get_main_menu_kb(server)
            )
        else:
            await message.answer(
                f"👋 С возвращением, *{user.first_name}*!\n\n"
                f"У вас пока нет серверов. Добавьте первый сервер!",
                parse_mode="Markdown",
                reply_markup=get_main_menu_kb()
            )
    else:
        # Новый пользователь - показываем приветствие и просим телефон
        await message.answer(
            WELCOME_TEXT,
            parse_mode="Markdown",
            reply_markup=get_phone_kb()
        )
        await state.set_state(UserStates.waiting_phone)


@dp.message(UserStates.waiting_phone, F.contact)
async def process_phone(message: Message, state: FSMContext):
    """Обработка полученного номера телефона"""
    phone = message.contact.phone_number
    
    # Создаём пользователя
    user = create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        phone=phone
    )
    
    # Уведомляем главного админа
    try:
        await bot.send_message(
            MAIN_ADMIN_ID,
            f"🆕 *Новый клиент зарегистрирован!*\n\n"
            f"👤 {user.first_name} {user.last_name or ''}\n"
            f"📱 {phone}\n"
            f"🆔 @{user.username or 'нет username'}\n"
            f"ID: `{user.telegram_id}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")
    
    await state.clear()
    await message.answer(
        f"✅ *Регистрация завершена!*\n\n"
        f"Добро пожаловать, *{user.first_name}*!\n\n"
        f"Теперь добавьте свой сервер для установки VPN.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await message.answer(
        "Выберите действие:",
        reply_markup=get_main_menu_kb()
    )


@dp.message(UserStates.waiting_phone)
async def process_phone_text(message: Message, state: FSMContext):
    """Если пользователь отправил текст вместо контакта"""
    await message.answer(
        "❌ Пожалуйста, используйте кнопку ниже для отправки номера телефона.",
        reply_markup=get_phone_kb()
    )


# ============ Добавление сервера ============

@dp.callback_query(F.data == "add_server")
async def add_server_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления сервера"""
    user = get_user_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.answer("Сначала зарегистрируйтесь!", show_alert=True)
        return
    
    await callback.answer()
    await state.set_state(UserStates.waiting_server_ip)
    await callback.message.edit_text(
        "🖥 *Добавление сервера*\n\n"
        "Введите IP-адрес вашего сервера:\n\n"
        "_Например: 123.45.67.89_",
        parse_mode="Markdown",
        reply_markup=get_cancel_kb()
    )


@dp.message(UserStates.waiting_server_ip)
async def process_server_ip(message: Message, state: FSMContext):
    """Обработка IP сервера"""
    ip = message.text.strip()
    
    # Простая валидация IP
    parts = ip.split('.')
    if len(parts) != 4:
        await message.answer(
            "❌ Неверный формат IP-адреса. Попробуйте ещё раз:\n\n"
            "_Например: 123.45.67.89_",
            parse_mode="Markdown",
            reply_markup=get_cancel_kb()
        )
        return
    
    try:
        for part in parts:
            num = int(part)
            if num < 0 or num > 255:
                raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Неверный формат IP-адреса. Попробуйте ещё раз:\n\n"
            "_Например: 123.45.67.89_",
            parse_mode="Markdown",
            reply_markup=get_cancel_kb()
        )
        return
    
    await state.update_data(server_ip=ip)
    await state.set_state(UserStates.waiting_server_password)
    await message.answer(
        f"✅ IP: `{ip}`\n\n"
        "Теперь введите пароль root пользователя:",
        parse_mode="Markdown"
    )


@dp.message(UserStates.waiting_server_password)
async def process_server_password(message: Message, state: FSMContext):
    """Обработка пароля сервера"""
    password = message.text.strip()
    
    # Удаляем сообщение с паролем для безопасности
    try:
        await message.delete()
    except:
        pass
    
    data = await state.get_data()
    ip = data.get("server_ip")
    
    # Проверяем подключение к серверу
    status_msg = await message.answer("⏳ Проверка подключения к серверу...")
    
    installer = ServerInstaller(ip, password)
    connected = await installer.connect()
    
    if not connected:
        await status_msg.edit_text(
            f"❌ *Не удалось подключиться к серверу*\n\n"
            f"IP: `{ip}`\n\n"
            f"Проверьте:\n"
            f"• Правильность IP-адреса\n"
            f"• Правильность пароля\n"
            f"• Доступность сервера по SSH (порт 22)\n\n"
            f"Попробуйте ввести пароль ещё раз:",
            parse_mode="Markdown",
            reply_markup=get_cancel_kb()
        )
        return
    
    # Проверяем что уже установлено
    wg_installed = await installer.check_wg_installed()
    awg_installed = await installer.check_awg_installed()
    v2ray_installed = await installer.check_v2ray_installed()
    vpn_bot_installed = await installer.check_vpn_bot_installed()
    
    await installer.disconnect()
    
    # Сохраняем сервер
    server = add_server(message.from_user.id, ip, password)
    
    if not server:
        await status_msg.edit_text(
            "❌ Ошибка сохранения сервера. Попробуйте позже.",
            reply_markup=get_main_menu_kb()
        )
        await state.clear()
        return
    
    # Обновляем статусы
    update_server_status(
        server.id,
        wg_installed=wg_installed,
        awg_installed=awg_installed,
        v2ray_installed=v2ray_installed,
        vpn_bot_installed=vpn_bot_installed
    )
    
    # Получаем обновлённый сервер
    server = get_server_by_id(server.id)
    
    await state.clear()
    
    # Формируем сообщение об установленных компонентах
    installed = []
    if wg_installed:
        installed.append("✅ WireGuard")
    if awg_installed:
        installed.append("✅ AmneziaWG")
    if v2ray_installed:
        installed.append("✅ V2Ray")
    if vpn_bot_installed:
        installed.append("✅ VPN Bot")
    
    installed_text = "\n".join(installed) if installed else "Ничего не установлено"
    
    await status_msg.edit_text(
        f"✅ *Сервер успешно добавлен!*\n\n"
        f"🖥 IP: `{ip}`\n\n"
        f"*Обнаружено на сервере:*\n{installed_text}\n\n"
        f"Выберите что установить:",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(server)
    )


# ============ Список серверов ============

@dp.callback_query(F.data == "my_servers")
async def my_servers(callback: CallbackQuery):
    """Показать список серверов пользователя"""
    servers = get_user_servers(callback.from_user.id)
    
    await callback.answer()
    
    if not servers:
        await callback.message.edit_text(
            "📋 *Мои серверы*\n\n"
            "У вас пока нет серверов.\n"
            "Добавьте первый сервер!",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb()
        )
        return
    
    await callback.message.edit_text(
        f"📋 *Мои серверы* ({len(servers)})\n\n"
        "🔐 WG | 🛡️ AWG | 🚀 V2Ray | 🤖 Bot\n"
        "⚪ — не установлено",
        parse_mode="Markdown",
        reply_markup=get_servers_list_kb(servers)
    )


@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery, state: FSMContext):
    """Главное меню"""
    await state.clear()
    await callback.answer()
    
    servers = get_user_servers(callback.from_user.id)
    server = servers[0] if servers else None
    
    await callback.message.edit_text(
        "🏠 *Главное меню*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(server)
    )


@dp.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    """Отмена действия"""
    await state.clear()
    await callback.answer("Отменено")
    
    servers = get_user_servers(callback.from_user.id)
    server = servers[0] if servers else None
    
    await callback.message.edit_text(
        "🏠 *Главное меню*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(server)
    )


# ============ Управление сервером ============

@dp.callback_query(F.data.startswith("server_"))
async def server_info(callback: CallbackQuery):
    """Информация о сервере"""
    server_id = int(callback.data.replace("server_", ""))
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer()
    
    # Статусы
    statuses = []
    if server.wg_installed:
        statuses.append("✅ WireGuard установлен")
    if server.awg_installed:
        statuses.append("✅ AmneziaWG установлен")
    if server.v2ray_installed:
        statuses.append("✅ V2Ray установлен")
    if server.vpn_bot_installed:
        statuses.append("✅ VPN Bot установлен")
    
    status_text = "\n".join(statuses) if statuses else "⚪ Ничего не установлено"
    
    await callback.message.edit_text(
        f"🖥 *Сервер {server.ip}*\n\n"
        f"*Статус установок:*\n{status_text}",
        parse_mode="Markdown",
        reply_markup=get_server_menu_kb(server)
    )


@dp.callback_query(F.data.startswith("check_status_"))
async def check_server_status(callback: CallbackQuery):
    """Проверить статус сервера"""
    server_id = int(callback.data.replace("check_status_", ""))
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer("Проверяю...")
    status_msg = await callback.message.edit_text(
        f"⏳ Проверка сервера {server.ip}...",
        parse_mode="Markdown"
    )
    
    installer = ServerInstaller(server.ip, server.password)
    connected = await installer.connect()
    
    if not connected:
        await status_msg.edit_text(
            f"❌ *Не удалось подключиться к серверу*\n\n"
            f"IP: `{server.ip}`",
            parse_mode="Markdown",
            reply_markup=get_back_to_server_kb(server_id)
        )
        return
    
    wg = await installer.check_wg_installed()
    awg = await installer.check_awg_installed()
    v2ray = await installer.check_v2ray_installed()
    vpn_bot = await installer.check_vpn_bot_installed()
    
    await installer.disconnect()
    
    # Обновляем в БД
    update_server_status(
        server_id,
        wg_installed=wg,
        awg_installed=awg,
        v2ray_installed=v2ray,
        vpn_bot_installed=vpn_bot
    )
    
    server = get_server_by_id(server_id)
    
    statuses = []
    statuses.append(f"{'✅' if wg else '❌'} WireGuard")
    statuses.append(f"{'✅' if awg else '❌'} AmneziaWG")
    statuses.append(f"{'✅' if v2ray else '❌'} V2Ray")
    statuses.append(f"{'✅' if vpn_bot else '❌'} VPN Bot")
    
    await status_msg.edit_text(
        f"🖥 *Статус сервера {server.ip}*\n\n"
        + "\n".join(statuses),
        parse_mode="Markdown",
        reply_markup=get_server_menu_kb(server)
    )


@dp.callback_query(F.data.startswith("delete_server_"))
async def delete_server_confirm(callback: CallbackQuery):
    """Подтверждение удаления сервера"""
    server_id = int(callback.data.replace("delete_server_", ""))
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer()
    await callback.message.edit_text(
        f"🗑 *Удаление сервера*\n\n"
        f"Вы уверены, что хотите удалить сервер?\n\n"
        f"IP: `{server.ip}`\n\n"
        f"⚠️ Это действие нельзя отменить!",
        parse_mode="Markdown",
        reply_markup=get_confirm_kb("delete", server_id)
    )


@dp.callback_query(F.data.startswith("confirm_delete_"))
async def delete_server_execute(callback: CallbackQuery):
    """Удаление сервера"""
    server_id = int(callback.data.replace("confirm_delete_", ""))
    server = get_server_by_id(server_id)
    
    if server:
        delete_server_by_id(server_id)
        await callback.answer("Сервер удалён", show_alert=True)
    else:
        await callback.answer("Сервер не найден", show_alert=True)
    
    servers = get_user_servers(callback.from_user.id)
    server = servers[0] if servers else None
    
    await callback.message.edit_text(
        "🏠 *Главное меню*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(server)
    )


# ============ Установка компонентов ============

@dp.callback_query(F.data.startswith("install_wg_"))
async def install_wg(callback: CallbackQuery):
    """Установка WireGuard"""
    server_id = int(callback.data.replace("install_wg_", ""))
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    if server.wg_installed:
        await callback.answer("WireGuard уже установлен!", show_alert=True)
        return
    
    await callback.answer()
    status_msg = await callback.message.edit_text(
        f"🔐 *Установка WireGuard*\n\n"
        f"Сервер: `{server.ip}`\n\n"
        f"⏳ Подключение...",
        parse_mode="Markdown"
    )
    
    installer = ServerInstaller(server.ip, server.password)
    connected = await installer.connect()
    
    if not connected:
        await status_msg.edit_text(
            f"❌ Не удалось подключиться к серверу",
            reply_markup=get_back_to_server_kb(server_id)
        )
        return
    
    async def progress(step):
        try:
            await status_msg.edit_text(
                f"🔐 *Установка WireGuard*\n\n"
                f"Сервер: `{server.ip}`\n\n"
                f"⏳ {step}",
                parse_mode="Markdown"
            )
        except:
            pass
    
    success, message = await installer.install_wireguard(progress)
    await installer.disconnect()
    
    if success:
        update_server_status(server_id, wg_installed=True)
        server = get_server_by_id(server_id)
        await status_msg.edit_text(
            f"✅ *WireGuard успешно установлен!*\n\n"
            f"Сервер: `{server.ip}`",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(server)
        )
    else:
        await status_msg.edit_text(
            f"❌ *Ошибка установки WireGuard*\n\n{message}",
            parse_mode="Markdown",
            reply_markup=get_back_to_server_kb(server_id)
        )


@dp.callback_query(F.data.startswith("install_awg_"))
async def install_awg(callback: CallbackQuery):
    """Установка AmneziaWG"""
    server_id = int(callback.data.replace("install_awg_", ""))
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    if server.awg_installed:
        await callback.answer("AmneziaWG уже установлен!", show_alert=True)
        return
    
    await callback.answer()
    status_msg = await callback.message.edit_text(
        f"🛡️ *Установка AmneziaWG*\n\n"
        f"Сервер: `{server.ip}`\n\n"
        f"⏳ Подключение...",
        parse_mode="Markdown"
    )
    
    installer = ServerInstaller(server.ip, server.password)
    connected = await installer.connect()
    
    if not connected:
        await status_msg.edit_text(
            f"❌ Не удалось подключиться к серверу",
            reply_markup=get_back_to_server_kb(server_id)
        )
        return
    
    async def progress(step):
        try:
            await status_msg.edit_text(
                f"🛡️ *Установка AmneziaWG*\n\n"
                f"Сервер: `{server.ip}`\n\n"
                f"⏳ {step}",
                parse_mode="Markdown"
            )
        except:
            pass
    
    success, message = await installer.install_amneziawg(progress)
    await installer.disconnect()
    
    if success:
        update_server_status(server_id, awg_installed=True)
        server = get_server_by_id(server_id)
        await status_msg.edit_text(
            f"✅ *AmneziaWG успешно установлен!*\n\n"
            f"Сервер: `{server.ip}`",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(server)
        )
    else:
        await status_msg.edit_text(
            f"❌ *Ошибка установки AmneziaWG*\n\n{message}",
            parse_mode="Markdown",
            reply_markup=get_back_to_server_kb(server_id)
        )


@dp.callback_query(F.data.startswith("install_v2ray_"))
async def install_v2ray(callback: CallbackQuery):
    """Установка V2Ray"""
    server_id = int(callback.data.replace("install_v2ray_", ""))
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    if server.v2ray_installed:
        await callback.answer("V2Ray уже установлен!", show_alert=True)
        return
    
    await callback.answer()
    status_msg = await callback.message.edit_text(
        f"🚀 *Установка V2Ray/Xray*\n\n"
        f"Сервер: `{server.ip}`\n\n"
        f"⏳ Подключение...",
        parse_mode="Markdown"
    )
    
    installer = ServerInstaller(server.ip, server.password)
    connected = await installer.connect()
    
    if not connected:
        await status_msg.edit_text(
            f"❌ Не удалось подключиться к серверу",
            reply_markup=get_back_to_server_kb(server_id)
        )
        return
    
    async def progress(step):
        try:
            await status_msg.edit_text(
                f"🚀 *Установка V2Ray/Xray*\n\n"
                f"Сервер: `{server.ip}`\n\n"
                f"⏳ {step}",
                parse_mode="Markdown"
            )
        except:
            pass
    
    success, message = await installer.install_v2ray(progress)
    await installer.disconnect()
    
    if success:
        update_server_status(server_id, v2ray_installed=True)
        server = get_server_by_id(server_id)
        await status_msg.edit_text(
            f"✅ *V2Ray/Xray успешно установлен!*\n\n"
            f"Сервер: `{server.ip}`",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(server)
        )
    else:
        await status_msg.edit_text(
            f"❌ *Ошибка установки V2Ray*\n\n{message}",
            parse_mode="Markdown",
            reply_markup=get_back_to_server_kb(server_id)
        )


# ============ Деплой VPN бота ============

@dp.callback_query(F.data.startswith("deploy_bot_"))
async def deploy_bot_start(callback: CallbackQuery, state: FSMContext):
    """Начало деплоя VPN бота"""
    server_id = int(callback.data.replace("deploy_bot_", ""))
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    if server.vpn_bot_installed:
        await callback.answer("VPN Bot уже установлен!", show_alert=True)
        return
    
    await callback.answer()
    await state.update_data(deploy_server_id=server_id)
    await state.set_state(UserStates.waiting_bot_token)
    
    await callback.message.edit_text(
        f"🤖 *Деплой VPN бота*\n\n"
        f"Сервер: `{server.ip}`\n\n"
        f"Для установки бота нужен токен от @BotFather.\n\n"
        f"1. Откройте @BotFather в Telegram\n"
        f"2. Создайте нового бота командой /newbot\n"
        f"3. Скопируйте полученный токен\n"
        f"4. Отправьте токен сюда\n\n"
        f"_Токен выглядит примерно так:_\n"
        f"`1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`",
        parse_mode="Markdown",
        reply_markup=get_cancel_kb()
    )


@dp.message(UserStates.waiting_bot_token)
async def process_bot_token(message: Message, state: FSMContext):
    """Обработка токена бота"""
    token = message.text.strip()
    
    # Валидация токена
    if ":" not in token or len(token) < 40:
        await message.answer(
            "❌ Неверный формат токена.\n\n"
            "Токен должен выглядеть примерно так:\n"
            "`1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`\n\n"
            "Попробуйте ещё раз:",
            parse_mode="Markdown"
        )
        return
    
    # Удаляем сообщение с токеном
    try:
        await message.delete()
    except:
        pass
    
    data = await state.get_data()
    
    # Проверяем, это "install_all" или просто деплой бота
    install_all_server_id = data.get("install_all_server_id")
    if install_all_server_id:
        server = get_server_by_id(install_all_server_id)
        if not server:
            await message.answer("Ошибка: сервер не найден")
            await state.clear()
            return
        await state.clear()
        
        # Создаём сообщение для редактирования
        status_msg = await message.answer(
            f"📦 *Установка всех компонентов*\n\n"
            f"Сервер: `{server.ip}`\n\n"
            f"⏳ Начинаем установку...",
            parse_mode="Markdown"
        )
        await run_install_all(status_msg, server, message.from_user.id, token)
        return
    
    server_id = data.get("deploy_server_id")
    server = get_server_by_id(server_id)
    
    if not server:
        await message.answer("Ошибка: сервер не найден")
        await state.clear()
        return
    
    await state.clear()
    
    status_msg = await message.answer(
        f"🤖 *Деплой VPN бота*\n\n"
        f"Сервер: `{server.ip}`\n\n"
        f"⏳ Подключение...",
        parse_mode="Markdown"
    )
    
    installer = ServerInstaller(server.ip, server.password)
    connected = await installer.connect()
    
    if not connected:
        await status_msg.edit_text(
            f"❌ Не удалось подключиться к серверу",
            reply_markup=get_back_to_server_kb(server_id)
        )
        return
    
    async def progress(step):
        try:
            await status_msg.edit_text(
                f"🤖 *Деплой VPN бота*\n\n"
                f"Сервер: `{server.ip}`\n\n"
                f"⏳ {step}",
                parse_mode="Markdown"
            )
        except:
            pass
    
    # Клиент становится админом своего бота
    success, result_message = await installer.deploy_vpn_bot(
        client_telegram_id=message.from_user.id,
        bot_token=token,
        progress_callback=progress
    )
    await installer.disconnect()
    
    if success:
        update_server_status(server_id, vpn_bot_installed=True)
        server = get_server_by_id(server_id)
        await status_msg.edit_text(
            f"✅ *VPN бот успешно установлен!*\n\n"
            f"Сервер: `{server.ip}`\n\n"
            f"🎉 *Вы назначены администратором бота!*\n\n"
            f"Теперь вы можете управлять VPN через своего бота.",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(server)
        )
        
        # Уведомляем главного админа
        try:
            user = get_user_by_telegram_id(message.from_user.id)
            await bot.send_message(
                MAIN_ADMIN_ID,
                f"🤖 *Клиент развернул VPN бота!*\n\n"
                f"👤 {user.first_name} (@{user.username})\n"
                f"🖥 Сервер: `{server.ip}`",
                parse_mode="Markdown"
            )
        except:
            pass
    else:
        await status_msg.edit_text(
            f"❌ *Ошибка деплоя VPN бота*\n\n{result_message}",
            parse_mode="Markdown",
            reply_markup=get_back_to_server_kb(server_id)
        )


# ============ Установить всё ============

@dp.callback_query(F.data.startswith("install_all_"))
async def install_all(callback: CallbackQuery, state: FSMContext):
    """Установка всех компонентов: WG + AWG + V2Ray + Bot"""
    server_id = int(callback.data.replace("install_all_", ""))
    server = get_server_by_id(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer()
    
    # Запрашиваем токен бота если бот ещё не установлен
    if not server.vpn_bot_installed:
        await state.update_data(install_all_server_id=server_id)
        await state.set_state(UserStates.waiting_bot_token)
        await callback.message.edit_text(
            f"📦 *Установка всех компонентов*\n\n"
            f"Сервер: `{server.ip}`\n\n"
            f"Будет установлено:\n"
            f"• WireGuard\n"
            f"• AmneziaWG\n"
            f"• V2Ray/Xray\n"
            f"• VPN Telegram бот\n\n"
            f"⏱ Время установки: ~5 минут\n\n"
            f"Для установки бота нужен токен от @BotFather.\n"
            f"Отправьте токен:",
            parse_mode="Markdown",
            reply_markup=get_cancel_kb()
        )
        return
    
    # Если бот уже установлен, устанавливаем только остальное
    await run_install_all(callback.message, server, callback.from_user.id, None)


async def run_install_all(status_msg, server, user_id: int, bot_token: str = None):
    """Выполнение установки всех компонентов"""
    await status_msg.edit_text(
        f"📦 *Установка всех компонентов*\n\n"
        f"Сервер: `{server.ip}`\n\n"
        f"⏳ Подключение к серверу...\n\n"
        f"_Это займёт около 5 минут. Пожалуйста, подождите._",
        parse_mode="Markdown"
    )
    
    installer = ServerInstaller(server.ip, server.password)
    connected = await installer.connect()
    
    if not connected:
        await status_msg.edit_text(
            f"❌ Не удалось подключиться к серверу `{server.ip}`",
            parse_mode="Markdown",
            reply_markup=get_back_to_server_kb(server.id)
        )
        return
    
    results = []
    
    async def update_status(current_step: str):
        try:
            completed = "\n".join(results) if results else ""
            text = f"📦 *Установка всех компонентов*\n\nСервер: `{server.ip}`\n\n"
            if completed:
                text += f"{completed}\n"
            text += f"⏳ {current_step}"
            await status_msg.edit_text(text, parse_mode="Markdown")
        except:
            pass
    
    # Отслеживаем что установлено для передачи в deploy_vpn_bot
    wg_ok = server.wg_installed
    awg_ok = server.awg_installed
    v2ray_ok = server.v2ray_installed
    
    # 1. WireGuard
    if not server.wg_installed:
        await update_status("Установка WireGuard...")
        success, msg = await installer.install_wireguard()
        if success:
            update_server_status(server.id, wg_installed=True)
            results.append("✅ WireGuard установлен")
            wg_ok = True
        else:
            results.append(f"❌ WireGuard: {msg[:100]}")
    
    # 2. AmneziaWG
    if not server.awg_installed:
        await update_status("Установка AmneziaWG...")
        success, msg = await installer.install_amneziawg()
        if success:
            update_server_status(server.id, awg_installed=True)
            results.append("✅ AmneziaWG установлен")
            awg_ok = True
        else:
            results.append(f"❌ AmneziaWG: ошибка")
    
    # 3. V2Ray
    if not server.v2ray_installed:
        await update_status("Установка V2Ray/Xray...")
        success, msg = await installer.install_v2ray()
        if success:
            update_server_status(server.id, v2ray_installed=True)
            results.append("✅ V2Ray установлен")
            v2ray_ok = True
        else:
            results.append(f"❌ V2Ray: ошибка")
    
    # 4. VPN Bot
    if not server.vpn_bot_installed and bot_token:
        await update_status("Установка VPN бота...")
        success, msg = await installer.deploy_vpn_bot(
            client_telegram_id=user_id,
            bot_token=bot_token,
            wg_installed=wg_ok,
            awg_installed=awg_ok,
            v2ray_installed=v2ray_ok
        )
        if success:
            update_server_status(server.id, vpn_bot_installed=True)
            results.append("✅ VPN бот установлен")
        else:
            results.append(f"❌ VPN бот: {msg[:50]}")
    
    await installer.disconnect()
    
    # Получаем обновлённый сервер
    server = get_server_by_id(server.id)
    
    completed = "\n".join(results)
    await status_msg.edit_text(
        f"🎉 *Установка завершена!*\n\n"
        f"Сервер: `{server.ip}`\n\n"
        f"{completed}\n\n"
        f"Теперь вы можете управлять VPN через своего бота!",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(server)
    )
    
    # Уведомляем главного админа
    try:
        user = get_user_by_telegram_id(user_id)
        await bot.send_message(
            MAIN_ADMIN_ID,
            f"📦 *Клиент установил все компоненты!*\n\n"
            f"👤 {user.first_name} (@{user.username})\n"
            f"🖥 Сервер: `{server.ip}`\n\n"
            f"{completed}",
            parse_mode="Markdown"
        )
    except:
        pass


# ============ Команды админа ============

@dp.callback_query(F.data == "admin_users")
async def admin_users_list(callback: CallbackQuery):
    """Список пользователей для админа"""
    if callback.from_user.id != MAIN_ADMIN_ID:
        return
    
    await callback.answer()
    users = get_all_users()
    
    if not users:
        await callback.message.edit_text(
            "👥 *Список пользователей*\n\nПока нет зарегистрированных клиентов.",
            parse_mode="Markdown",
            reply_markup=get_admin_menu_kb()
        )
        return
    
    text = "👥 *Список пользователей:*\n\n"
    for u in users:
        servers = get_user_servers(u.telegram_id)
        text += f"• *{u.first_name}* (@{u.username or 'нет'})\n"
        text += f"  📱 {u.phone}\n"
        text += f"  🆔 `{u.telegram_id}`\n"
        text += f"  🖥 Серверов: {len(servers)}\n"
        if servers:
            for s in servers:
                icons = []
                if s.wg_installed: icons.append("🔐")
                if s.awg_installed: icons.append("🛡️")
                if s.v2ray_installed: icons.append("🚀")
                if s.vpn_bot_installed: icons.append("🤖")
                icons_str = " ".join(icons) if icons else "⚪"
                text += f"    └ {s.ip} {icons_str}\n"
        text += "\n"
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_admin_menu_kb()
    )


@dp.callback_query(F.data == "admin_servers")
async def admin_servers_list(callback: CallbackQuery):
    """Все серверы для админа"""
    if callback.from_user.id != MAIN_ADMIN_ID:
        return
    
    await callback.answer()
    session = get_session()
    servers = session.query(Server).all()
    session.close()
    
    if not servers:
        await callback.message.edit_text(
            "🖥 *Все серверы*\n\nПока нет добавленных серверов.",
            parse_mode="Markdown",
            reply_markup=get_admin_menu_kb()
        )
        return
    
    text = "🖥 *Все серверы:*\n\n"
    for s in servers:
        user = get_user_by_telegram_id(s.owner.telegram_id) if s.owner else None
        owner_name = user.first_name if user else "Неизвестно"
        icons = []
        if s.wg_installed: icons.append("🔐")
        if s.awg_installed: icons.append("🛡️")
        if s.v2ray_installed: icons.append("🚀")
        if s.vpn_bot_installed: icons.append("🤖")
        icons_str = " ".join(icons) if icons else "⚪"
        text += f"• `{s.ip}` {icons_str}\n"
        text += f"  👤 {owner_name}\n\n"
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_admin_menu_kb()
    )


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    """Статистика для админа"""
    if callback.from_user.id != MAIN_ADMIN_ID:
        return
    
    await callback.answer()
    users = get_all_users()
    session = get_session()
    servers = session.query(Server).all()
    session.close()
    
    wg_count = sum(1 for s in servers if s.wg_installed)
    awg_count = sum(1 for s in servers if s.awg_installed)
    v2ray_count = sum(1 for s in servers if s.v2ray_installed)
    bot_count = sum(1 for s in servers if s.vpn_bot_installed)
    
    await callback.message.edit_text(
        f"📊 *Статистика AGDE Deploy Bot*\n\n"
        f"👥 Пользователей: {len(users)}\n"
        f"🖥 Серверов: {len(servers)}\n\n"
        f"*Установки:*\n"
        f"🔐 WireGuard: {wg_count}\n"
        f"🛡️ AmneziaWG: {awg_count}\n"
        f"🚀 V2Ray: {v2ray_count}\n"
        f"🤖 VPN Bot: {bot_count}",
        parse_mode="Markdown",
        reply_markup=get_admin_menu_kb()
    )


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    """Админ-панель (только для главного админа)"""
    if message.from_user.id != MAIN_ADMIN_ID:
        return
    
    users = get_all_users()
    session = get_session()
    servers_count = session.query(Server).count()
    session.close()
    
    users_text = ""
    if users:
        for u in users:
            servers = get_user_servers(u.telegram_id)
            users_text += f"\n• {u.first_name} (@{u.username or 'нет'})\n"
            users_text += f"  📱 {u.phone}\n"
            users_text += f"  🖥 Серверов: {len(servers)}\n"
    else:
        users_text = "\nПока нет зарегистрированных клиентов."
    
    await message.answer(
        f"👑 *Админ-панель AGDE Deploy Bot*\n\n"
        f"📊 *Статистика:*\n"
        f"👥 Пользователей: {len(users)}\n"
        f"🖥 Серверов: {servers_count}\n\n"
        f"*Клиенты:*{users_text}",
        parse_mode="Markdown",
        reply_markup=get_admin_menu_kb()
    )


@dp.message(Command("users"))
async def list_users(message: Message):
    """Список пользователей (только для главного админа)"""
    if message.from_user.id != MAIN_ADMIN_ID:
        return
    
    session = get_session()
    users = session.query(User).all()
    session.close()
    
    if not users:
        await message.answer("Пользователей пока нет")
        return
    
    text = "👥 *Пользователи:*\n\n"
    for user in users:
        servers = get_user_servers(user.telegram_id)
        text += f"• {user.first_name} (@{user.username})\n"
        text += f"  📱 {user.phone}\n"
        text += f"  🖥 Серверов: {len(servers)}\n\n"
    
    await message.answer(text, parse_mode="Markdown")


# ============ Запуск бота ============

async def main():
    """Запуск бота"""
    init_db()
    logger.info("Database initialized")
    logger.info("Starting AGDE Deploy Bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
