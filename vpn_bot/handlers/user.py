import os
import re
import logging
from typing import Optional
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile, InputMediaPhoto, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import TARIFFS, PAYMENT_PHONE, ADMIN_ID, CLIENT_DIR, LOCAL_MODE
from database import async_session, User, Config, Subscription, Payment, Server, WithdrawalRequest
from keyboards.user_kb import (
    get_main_menu_kb, get_tariffs_kb, get_payment_kb, 
    get_back_kb, get_configs_kb, get_config_detail_kb,
    get_no_configs_kb, get_no_subscription_kb, get_subscription_kb, get_how_to_kb,
    get_welcome_kb, get_trial_activated_kb, get_after_config_kb,
    get_referral_menu_kb, get_referral_back_kb, get_withdrawal_cancel_kb
)
from states.user_states import PaymentStates, RegistrationStates, ConfigRequestStates, WithdrawalStates
from services.wireguard import WireGuardService
from services.wireguard_multi import WireGuardMultiService
from services.ocr import OCRService
from services.settings import is_password_required, is_channel_required, get_bot_password, is_phone_required, is_config_approval_required, get_setting, get_channel_name, get_max_configs
from keyboards.admin_kb import get_payment_review_kb, get_config_request_kb, get_check_subscription_kb
from utils import transliterate_ru_to_en

CHANNEL_USERNAME = "agdevpn"

logger = logging.getLogger(__name__)
router = Router()


async def delete_bot_messages(bot: Bot, chat_id: int, state: FSMContext):
    """Удаляет сохранённые сообщения бота"""
    data = await state.get_data()
    msg_ids = data.get("bot_messages", [])
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
    await state.update_data(bot_messages=[])


async def save_bot_message(state: FSMContext, message_id: int):
    """Сохраняет ID сообщения бота для последующего удаления"""
    data = await state.get_data()
    msg_ids = data.get("bot_messages", [])
    msg_ids.append(message_id)
    await state.update_data(bot_messages=msg_ids)


async def get_or_create_user(telegram_id: int, username: str, full_name: str, referrer_telegram_id: int = None) -> tuple:
    """Returns (user, is_new_user)"""
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            # Находим реферера если указан
            referrer_id = None
            if referrer_telegram_id and referrer_telegram_id != telegram_id:
                referrer_stmt = select(User).where(User.telegram_id == referrer_telegram_id)
                referrer_result = await session.execute(referrer_stmt)
                referrer = referrer_result.scalar_one_or_none()
                if referrer:
                    referrer_id = referrer.id
            
            user = User(
                telegram_id=telegram_id,
                username=username,
                full_name=full_name,
                referrer_id=referrer_id
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user, True
        
        return user, False


async def get_user_by_telegram_id(telegram_id: int) -> Optional[User]:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id).options(
            selectinload(User.configs),
            selectinload(User.subscriptions)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def check_has_subscription(telegram_id: int) -> bool:
    user = await get_user_by_telegram_id(telegram_id)
    if not user or not user.subscriptions:
        return False
    for sub in user.subscriptions:
        if sub.expires_at is None or sub.expires_at > datetime.utcnow():
            return True
    return False


async def get_user_how_to_seen(telegram_id: int) -> bool:
    async with async_session() as session:
        stmt = select(User.how_to_seen).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        value = result.scalar_one_or_none()
        return value if value is not None else False


async def set_user_how_to_seen(telegram_id: int) -> None:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.how_to_seen = True
            await session.commit()


async def get_channel_name() -> str:
    """Получить название канала из настроек"""
    from services.settings import get_setting
    return await get_setting("channel_name") or CHANNEL_USERNAME


async def check_channel_subscription(bot: Bot, user_id: int) -> bool:
    try:
        channel = await get_channel_name()
        member = await bot.get_chat_member(f"@{channel}", user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        return False


def get_phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)],
            [KeyboardButton(text="⏭ Пропустить")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


async def create_config_multi(config_name: str, user_id: int) -> tuple:
    """
    Создать конфиг с использованием мультисервера.
    Возвращает (success, config_data, server_id, error_msg)
    """
    async with async_session() as session:
        # Проверяем есть ли серверы в БД
        servers = await WireGuardMultiService.get_all_servers(session)
        
        if not servers:
            # Нет серверов - используем старый метод (локальный)
            success, config_data, msg = await WireGuardService.create_config(config_name)
            return success, config_data, None, msg
        
        # Используем мультисервер
        success, config_data, msg = await WireGuardMultiService.create_config(config_name, session)
        
        if success and config_data:
            return True, config_data, config_data.server_id, msg
        return False, None, None, msg


async def send_config_file(bot: Bot, chat_id: int, config_name: str, config_data, server_id, caption: str, reply_markup=None):
    """
    Отправить конфиг-файл пользователю.
    Поддерживает как локальный сервер, так и мультисервер.
    """
    import tempfile
    
    if LOCAL_MODE:
        await bot.send_message(
            chat_id,
            "🔧 [LOCAL_MODE] Конфиг будет отправлен на сервере",
            reply_markup=reply_markup
        )
        return
    
    if server_id and hasattr(config_data, 'config_content') and config_data.config_content:
        # Мультисервер — создаём временный файл из содержимого
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
            f.write(config_data.config_content)
            temp_path = f.name
        try:
            await bot.send_document(
                chat_id,
                FSInputFile(temp_path, filename=f"{config_name}.conf"),
                caption=caption,
                reply_markup=reply_markup
            )
        finally:
            os.unlink(temp_path)
    else:
        # Локальный сервер
        config_path = WireGuardService.get_config_file_path(config_name)
        if os.path.exists(config_path):
            await bot.send_document(
                chat_id,
                FSInputFile(config_path),
                caption=caption,
                reply_markup=reply_markup
            )
        else:
            await bot.send_message(
                chat_id,
                f"❌ Ошибка: конфиг не найден\n\nНапиши @agdelesha для помощи.",
                reply_markup=reply_markup
            )


@router.message(Command("about"))
async def cmd_about(message: Message):
    await message.answer(
        "🌐 Простой и незаметный 🥷🏻\n\n"
        "📩 Связь со мной: @agdelesha",
        parse_mode="Markdown"
    )


@router.message(Command("akak"))
async def cmd_akak(message: Message, bot: Bot):
    """Команда /akak — показать инструкцию"""
    import pathlib
    how_dir = pathlib.Path(__file__).parent.parent / "andhow"

    await message.answer(
        f"*{message.from_user.first_name}*, подключение занимает 1-2 минуты!\n\n"
        "📲 *Скачать приложение WireGuard:*\n"
        "— iPhone: https://apps.apple.com/app/id1441195209\n"
        "— Другие устройства: https://www.wireguard.com/install/\n\n"
        "💬 *Есть вопросы?* Просто напиши в чат — AI-помощник поможет!\n\n"
        "👇 Подробная инструкция ниже:",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

    # Отправляем каждую картинку отдельно
    for i in range(1, 5):
        img_path = how_dir / f"{i}.jpg"
        if img_path.exists():
            await bot.send_photo(message.from_user.id, FSInputFile(str(img_path)))
    
    # Отправляем гифку
    gif_path = how_dir / "5.gif"
    if gif_path.exists():
        await bot.send_animation(message.from_user.id, FSInputFile(str(gif_path)))
    
    # Показываем главное меню
    has_sub = await check_has_subscription(message.from_user.id)
    how_to_seen = await get_user_how_to_seen(message.from_user.id)
    await message.answer(
        "👆 Готово!",
        reply_markup=get_main_menu_kb(message.from_user.id, has_sub, how_to_seen)
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    # Удаляем предыдущие сообщения бота
    await delete_bot_messages(bot, message.chat.id, state)
    
    # Получаем ID бота для индивидуальных настроек
    bot_info = await bot.get_me()
    bot_id = bot_info.id
    
    # Проверяем реферальную ссылку
    referrer_telegram_id = None
    if message.text and len(message.text.split()) > 1:
        args = message.text.split()[1]
        if args.startswith("ref_"):
            try:
                referrer_telegram_id = int(args.replace("ref_", ""))
            except ValueError:
                pass
    
    user, is_new = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        referrer_telegram_id=referrer_telegram_id
    )
    
    # Уведомляем приглашённого о скидке
    if is_new and referrer_telegram_id and user.referrer_id:
        # Получаем имя реферера
        async with async_session() as session:
            stmt = select(User).where(User.telegram_id == referrer_telegram_id)
            result = await session.execute(stmt)
            referrer = result.scalar_one_or_none()
            referrer_name = f"@{referrer.username}" if referrer and referrer.username else "друг"
        
        await message.answer(
            f"🎉 *Тебя пригласил {referrer_name}!*\n\n"
            f"🎁 Ты получаешь *скидку 50%* на первую оплату подписки!\n\n"
            f"Оформи подписку и плати в 2 раза меньше 💰",
            parse_mode="Markdown"
        )
    
    if is_new:
        # Проверяем, нужен ли пароль (индивидуально для бота)
        if await is_password_required(bot_id):
            msg = await message.answer(
                f"👋 Привет, *{message.from_user.first_name}*!\n\n"
                "🔐 Для доступа к боту введите пароль:",
                parse_mode="Markdown"
            )
            await save_bot_message(state, msg.message_id)
            await state.set_state(RegistrationStates.waiting_for_password)
            return
        
        # Проверяем подписку на канал (индивидуально для бота)
        if await is_channel_required(bot_id):
            is_subscribed = await check_channel_subscription(bot, message.from_user.id)
            if not is_subscribed:
                channel = await get_channel_name(bot_id)
                msg = await message.answer(
                    f"👋 Привет, *{message.from_user.first_name}*!\n\n"
                    f"📢 Для использования бота подпишитесь на канал @{channel}:",
                    parse_mode="Markdown",
                    reply_markup=get_check_subscription_kb(channel)
                )
                await save_bot_message(state, msg.message_id)
                await state.update_data(after_subscription="registration", bot_id=bot_id)
                return
        
        # Проверяем, нужен ли запрос телефона (индивидуально для бота)
        if await is_phone_required(bot_id):
            msg = await message.answer(
                f"👋 Привет, *{message.from_user.first_name}*!\n\n"
                "Это бот для блокировки рекламы.\n\n"
                "📱 Пожалуйста, поделитесь номером телефона для связи:\n"
                "(или нажмите 'Пропустить')",
                parse_mode="Markdown",
                reply_markup=get_phone_keyboard()
            )
            await save_bot_message(state, msg.message_id)
            await state.set_state(RegistrationStates.waiting_for_phone)
            return
        
        # Телефон не требуется — показываем воронку
        msg = await message.answer(
            f"Привет! 👋\n"
            f"Я помогу тебе подключиться к сервису\n\n"
            f"💬 У меня есть встроенный AI-помощник — просто напиши любой вопрос в чат и я отвечу!\n\n"
            f"Выбери:",
            parse_mode="Markdown",
            reply_markup=get_welcome_kb(show_trial=True)
        )
        await save_bot_message(state, msg.message_id)
        return
    
    # Существующий пользователь
    has_sub = await check_has_subscription(message.from_user.id)
    
    if has_sub:
        # Есть подписка — главное меню
        how_to_seen = await get_user_how_to_seen(message.from_user.id)
        menu_text = (
            "Управление сервисом — кнопками ниже:\n\n"
            "📱 *Конфиги* — параметры подключения, QR-коды\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник на связи!"
        )
        msg = await message.answer(
            menu_text,
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(message.from_user.id, True, how_to_seen)
        )
    else:
        # Нет подписки — воронка
        user = await get_user_by_telegram_id(message.from_user.id)
        show_trial = not user.trial_used if user else True
        msg = await message.answer(
            f"Привет! 👋\n"
            f"Я помогу тебе подключиться к сервису\n\n"
            f"💬 У меня есть встроенный AI-помощник — просто напиши любой вопрос в чат и я отвечу!\n\n"
            f"Выбери:",
            parse_mode="Markdown",
            reply_markup=get_welcome_kb(show_trial=show_trial)
        )
    await save_bot_message(state, msg.message_id)


@router.message(RegistrationStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext, bot: Bot):
    # Удаляем предыдущие сообщения
    await delete_bot_messages(bot, message.chat.id, state)
    
    # Получаем ID бота для индивидуальных настроек
    bot_info = await bot.get_me()
    bot_id = bot_info.id
    
    entered_password = message.text.strip()
    correct_password = await get_bot_password(bot_id)
    
    if entered_password != correct_password:
        msg = await message.answer(
            "❌ Неверный пароль. Попробуйте ещё раз:",
            parse_mode="Markdown"
        )
        await save_bot_message(state, msg.message_id)
        return
    
    # Пароль верный, проверяем подписку на канал
    if await is_channel_required(bot_id):
        is_subscribed = await check_channel_subscription(bot, message.from_user.id)
        if not is_subscribed:
            channel = await get_channel_name(bot_id)
            msg = await message.answer(
                "✅ Пароль принят!\n\n"
                f"📢 Теперь подпишитесь на канал @{channel}:",
                parse_mode="Markdown",
                reply_markup=get_check_subscription_kb(channel)
            )
            await save_bot_message(state, msg.message_id)
            await state.update_data(after_subscription="registration", bot_id=bot_id)
            await state.set_state(None)
            return
    
    # Проверяем, нужен ли запрос телефона
    if await is_phone_required(bot_id):
        msg = await message.answer(
            "✅ Пароль принят!\n\n"
            "📱 Пожалуйста, поделитесь номером телефона для связи:\n"
            "(или нажмите 'Пропустить')",
            parse_mode="Markdown",
            reply_markup=get_phone_keyboard()
        )
        await save_bot_message(state, msg.message_id)
        await state.set_state(RegistrationStates.waiting_for_phone)
        return
    
    # Телефон не требуется — сразу в главное меню
    msg = await message.answer(
        "✅ Пароль принят!\n\n"
        "🛡️ Блокировщик рекламы, да и всего-то",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(message.from_user.id, False)
    )
    await save_bot_message(state, msg.message_id)
    await state.clear()


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    is_subscribed = await check_channel_subscription(bot, callback.from_user.id)
    
    if not is_subscribed:
        await callback.answer("❌ Ты не подписан на канал!", show_alert=True)
        return
    
    # Получаем ID бота для индивидуальных настроек
    bot_info = await bot.get_me()
    bot_id = bot_info.id
    
    data = await state.get_data()
    after_subscription = data.get("after_subscription")
    # Используем bot_id из state если есть, иначе текущий
    bot_id = data.get("bot_id", bot_id)
    
    if after_subscription == "registration":
        # Проверяем, нужен ли запрос телефона
        if await is_phone_required(bot_id):
            await callback.message.edit_text(
                "✅ Подписка подтверждена!\n\n"
                "📱 Пожалуйста, поделитесь номером телефона для связи:\n"
                "(или нажмите 'Пропустить')",
                parse_mode="Markdown"
            )
            msg = await callback.message.answer(
                "⬇️ Нажмите кнопку ниже:",
                reply_markup=get_phone_keyboard()
            )
            await save_bot_message(state, msg.message_id)
            await state.set_state(RegistrationStates.waiting_for_phone)
        else:
            # Телефон не требуется — сразу в главное меню
            await state.clear()
            await callback.message.edit_text(
                "✅ Подписка подтверждена!\n\n"
                "🛡️ Блокировщик рекламы, да и всего-то",
                parse_mode="Markdown",
                reply_markup=get_main_menu_kb(callback.from_user.id, False)
            )
    elif after_subscription == "extend":
        await state.clear()
        user = await get_user_by_telegram_id(callback.from_user.id)
        has_referral_discount = user and user.referrer_id and not user.first_payment_done
        await callback.message.edit_text(
            "✅ Подписка подтверждена!\n\n"
            "💳 *Продление подписки*\n\n"
            "Выбери тариф для продления.\n"
            "Дни будут добавлены к текущей подписке.",
            parse_mode="Markdown",
            reply_markup=get_tariffs_kb(show_trial=False, has_referral_discount=has_referral_discount)
        )
    elif after_subscription == "extra_config":
        await state.clear()
        await callback.message.edit_text(
            "✅ Подписка подтверждена!\n\n"
            "📱 *Запрос дополнительного конфига*\n\n"
            "Для какого устройства требуется конфиг?\n"
            "(например: iPhone, MacBook, Windows ПК)",
            parse_mode="Markdown"
        )
        await state.set_state(ConfigRequestStates.waiting_for_device)
    else:
        await state.clear()
        has_sub = await check_has_subscription(callback.from_user.id)
        await callback.message.edit_text(
            "✅ Подписка подтверждена!\n\n"
            "🛡️ Блокировщик рекламы, да и всего-то",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(callback.from_user.id, has_sub)
        )


@router.message(RegistrationStates.waiting_for_phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext, bot: Bot):
    # Удаляем предыдущие сообщения
    await delete_bot_messages(bot, message.chat.id, state)
    
    phone = message.contact.phone_number
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.phone = phone
            await session.commit()
    
    # Отправляем главное меню с удалением Reply клавиатуры
    msg = await message.answer(
        "🛡️ Блокировщик рекламы, да и всего-то",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    # Удаляем это сообщение и отправляем с inline-кнопками
    await bot.delete_message(message.chat.id, msg.message_id)
    msg2 = await message.answer(
        "🛡️ Блокировщик рекламы, да и всего-то",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(message.from_user.id, False)
    )
    await state.clear()
    await save_bot_message(state, msg2.message_id)


@router.message(RegistrationStates.waiting_for_phone, F.text == "⏭ Пропустить")
async def skip_phone(message: Message, state: FSMContext, bot: Bot):
    # Удаляем предыдущие сообщения
    await delete_bot_messages(bot, message.chat.id, state)
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.phone = "5553535"
            await session.commit()
    
    # Отправляем главное меню с удалением Reply клавиатуры
    msg = await message.answer(
        "🛡️ Блокировщик рекламы, да и всего-то",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    # Удаляем это сообщение и отправляем с inline-кнопками
    await bot.delete_message(message.chat.id, msg.message_id)
    msg2 = await message.answer(
        "🛡️ Блокировщик рекламы, да и всего-то",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(message.from_user.id, False)
    )
    await state.clear()
    await save_bot_message(state, msg2.message_id)


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    has_sub = await check_has_subscription(callback.from_user.id)
    
    if has_sub:
        how_to_seen = await get_user_how_to_seen(callback.from_user.id)
        menu_text = (
            "Управление сервисом — кнопками ниже:\n\n"
            "📱 *Конфиги* — параметры подключения, QR-коды\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник на связи!"
        )
        await callback.message.edit_text(
            menu_text,
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(callback.from_user.id, True, how_to_seen)
        )
    else:
        # Нет подписки — возвращаем к воронке
        user = await get_user_by_telegram_id(callback.from_user.id)
        show_trial = not user.trial_used if user else True
        await callback.message.edit_text(
            "Выбери:",
            parse_mode="Markdown",
            reply_markup=get_welcome_kb(show_trial=show_trial)
        )


@router.callback_query(F.data == "how_to")
async def how_to(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    import pathlib
    how_dir = pathlib.Path(__file__).parent.parent / "andhow"

    await bot.send_message(
        callback.from_user.id,
        (
            f"*{callback.from_user.first_name}*, подключение занимает 1-2 минуты!\n\n"
            "📲 *Скачать приложение WireGuard:*\n"
            "— iPhone: https://apps.apple.com/app/id1441195209\n"
            "— Другие устройства: https://www.wireguard.com/install/\n\n"
            "💬 *Есть вопросы?* Просто напиши в чат — AI-помощник поможет!\n\n"
            "👇 Подробная инструкция ниже:"
        ),
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

    # Отправляем каждую картинку отдельно (1.jpg, 2.jpg, 3.jpg, 4.jpg)
    for i in range(1, 5):
        img_path = how_dir / f"{i}.jpg"
        if img_path.exists():
            await bot.send_photo(callback.from_user.id, FSInputFile(str(img_path)))
    
    # Отправляем гифку отдельно (5.gif)
    gif_path = how_dir / "5.gif"
    if gif_path.exists():
        await bot.send_animation(callback.from_user.id, FSInputFile(str(gif_path)))
    
    # Отправляем сообщение с кнопкой "да понял я, понял"
    await bot.send_message(
        callback.from_user.id,
        "☝️ Всё понятно?",
        reply_markup=get_how_to_kb()
    )


@router.callback_query(F.data == "how_to_understood")
async def how_to_understood(callback: CallbackQuery, bot: Bot):
    await callback.answer("👍 Отлично!")
    await set_user_how_to_seen(callback.from_user.id)
    
    # Удаляем сообщение "Всё понятно?" с кнопкой
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    has_sub = await check_has_subscription(callback.from_user.id)
    
    if has_sub:
        # Отправляем последний созданный конфиг
        user = await get_user_by_telegram_id(callback.from_user.id)
        if user and user.configs and not LOCAL_MODE:
            # Берём последний конфиг (самый новый)
            config = user.configs[-1]
            config_path = WireGuardService.get_config_file_path(config.name)
            if os.path.exists(config_path):
                await bot.send_document(
                    callback.from_user.id,
                    FSInputFile(config_path),
                    caption="📄 Вот твой конфиг",
                    parse_mode=None
                )
        
        # Отправляем главное меню
        menu_text = (
            "Управление сервисом — кнопками ниже:\n\n"
            "📱 *Конфиги* — параметры подключения, QR-коды\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник на связи!"
        )
        await bot.send_message(
            callback.from_user.id,
            menu_text,
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(callback.from_user.id, True, True)
        )
    else:
        # Нет подписки — возвращаем к воронке
        user = await get_user_by_telegram_id(callback.from_user.id)
        show_trial = not user.trial_used if user else True
        await bot.send_message(
            callback.from_user.id,
            "Выбери:",
            parse_mode="Markdown",
            reply_markup=get_welcome_kb(show_trial=show_trial)
        )


# ===== АВТОВОРОНКА =====

@router.callback_query(F.data == "funnel_trial")
async def funnel_trial(callback: CallbackQuery):
    """Шаг 2 — пользователь выбрал пробный доступ"""
    await callback.answer()
    
    user = await get_user_by_telegram_id(callback.from_user.id)
    if user and user.trial_used:
        has_referral_discount = user.referrer_id and not user.first_payment_done
        await callback.message.edit_text(
            "❌ Ты уже использовал пробный период.\n\n"
            "Выбери тариф для продолжения:",
            parse_mode="Markdown",
            reply_markup=get_tariffs_kb(show_trial=False, has_referral_discount=has_referral_discount)
        )
        return
    
    await callback.message.edit_text(
        "Отлично 👍 пробный доступ активирован!\n\n"
        "Нажми кнопку «Получить»",
        parse_mode="Markdown",
        reply_markup=get_trial_activated_kb()
    )


@router.callback_query(F.data == "funnel_tariffs")
async def funnel_tariffs(callback: CallbackQuery):
    """Выбор тарифов из воронки"""
    await callback.answer()
    
    user = await get_user_by_telegram_id(callback.from_user.id)
    show_trial = not user.trial_used if user else True
    # Проверяем скидку для реферала
    has_referral_discount = user and user.referrer_id and not user.first_payment_done
    
    try:
        await callback.message.edit_text(
            "📋 *Выбери тарифный план:*",
            parse_mode="Markdown",
            reply_markup=get_tariffs_kb(show_trial=show_trial, has_referral_discount=has_referral_discount)
        )
    except Exception:
        await callback.message.delete()
        await callback.message.answer(
            "📋 *Выбери тарифный план:*",
            parse_mode="Markdown",
            reply_markup=get_tariffs_kb(show_trial=show_trial, has_referral_discount=has_referral_discount)
        )


@router.callback_query(F.data == "funnel_get_config")
async def funnel_get_config(callback: CallbackQuery, bot: Bot):
    """Шаг 3 — получение конфига после активации пробного периода"""
    await callback.answer()
    
    # Сообщение о создании конфига
    await callback.message.edit_text(
        "⏳ *Создаю конфиг...*\n\nПодожди пару секунд",
        parse_mode="Markdown"
    )
    
    user = await get_user_by_telegram_id(callback.from_user.id)
    
    # Активируем пробный период
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == callback.from_user.id)
        result = await session.execute(stmt)
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.trial_used = True
            
            # Создаём подписку на 3 дня
            trial_sub = Subscription(
                user_id=db_user.id,
                tariff_type="trial",
                days_total=3,
                expires_at=datetime.utcnow() + timedelta(days=3)
            )
            session.add(trial_sub)
            await session.commit()
    
    # Создаём конфиг (только username, без telegram_id)
    username = callback.from_user.username or f"user{callback.from_user.id}"
    config_name = username
    
    success, config_data, server_id, error_msg = await create_config_multi(config_name, callback.from_user.id)
    
    if not success:
        await callback.message.edit_text(
            f"❌ Ошибка создания конфига: {error_msg}\n\n"
            "Напиши @agdelesha для помощи.",
            parse_mode="Markdown"
        )
        return
    
    # Сохраняем конфиг в БД
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == callback.from_user.id)
        result = await session.execute(stmt)
        db_user = result.scalar_one_or_none()
        
        if db_user:
            new_config = Config(
                user_id=db_user.id,
                server_id=server_id,
                name=config_name,
                public_key=config_data.public_key,
                preshared_key=config_data.preshared_key,
                allowed_ips=config_data.allowed_ips,
                client_ip=config_data.client_ip,
                is_active=True
            )
            session.add(new_config)
            await session.commit()
    
    # Отправляем конфиг
    await send_config_file(
        bot, callback.from_user.id, config_name, config_data, server_id,
        caption="📄 Вот твой конфиг\n\nЧерез 3 дня пробный период закончится.",
        reply_markup=get_after_config_kb()
    )


@router.callback_query(F.data == "get_vpn")
async def get_vpn(callback: CallbackQuery):
    await callback.answer()
    user = await get_user_by_telegram_id(callback.from_user.id)
    show_trial = not user.trial_used if user else True
    has_referral_discount = user and user.referrer_id and not user.first_payment_done
    
    if has_referral_discount:
        tariff_text = (
            "📋 *Выбери тарифный план:*\n\n"
            "🎁 Пробный — 3 дня бесплатно (один раз)\n"
            "📅 30 дней — *100₽* вместо 200₽ (скидка 50%)\n"
            "📅 90 дней — *200₽* вместо 400₽ (скидка 50%)\n"
            "📅 180 дней — *300₽* вместо 600₽ (скидка 50%)"
        )
    else:
        tariff_text = (
            "📋 *Выбери тарифный план:*\n\n"
            "🎁 Пробный — 3 дня бесплатно (один раз)\n"
            "📅 30 дней — 200₽\n"
            "📅 90 дней — 400₽\n"
            "📅 180 дней — 600₽"
        )
    
    await callback.message.edit_text(
        tariff_text,
        parse_mode="Markdown",
        reply_markup=get_tariffs_kb(show_trial=show_trial, has_referral_discount=has_referral_discount)
    )


@router.callback_query(F.data == "extend_subscription")
async def extend_subscription(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    # Проверяем подписку на канал
    if await is_channel_required():
        is_subscribed = await check_channel_subscription(bot, callback.from_user.id)
        if not is_subscribed:
            channel = await get_channel_name()
            await callback.message.edit_text(
                f"📢 Для продления подписки необходимо подписаться на канал @{channel}:",
                parse_mode="Markdown",
                reply_markup=get_check_subscription_kb(channel)
            )
            await state.update_data(after_subscription="extend")
            return
    
    user = await get_user_by_telegram_id(callback.from_user.id)
    has_referral_discount = user and user.referrer_id and not user.first_payment_done
    
    await callback.message.edit_text(
        "💳 *Продление подписки*\n\n"
        "Выбери тариф для продления.\n"
        "Дни будут добавлены к текущей подписке.",
        parse_mode="Markdown",
        reply_markup=get_tariffs_kb(show_trial=False, has_referral_discount=has_referral_discount)
    )


@router.callback_query(F.data == "tariff_trial")
async def tariff_trial(callback: CallbackQuery, bot: Bot):
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == callback.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return
        
        if user.trial_used:
            await callback.answer("❌ Пробный период уже использован", show_alert=True)
            return
        
        await callback.message.edit_text("⏳ Создаю конфиг...")
        
        config_name = user.username if user.username else f"user{callback.from_user.id}"
        success, config_data, server_id, msg = await create_config_multi(config_name, callback.from_user.id)
        
        if not success:
            await callback.message.edit_text(
                f"❌ Ошибка создания конфига:\n{msg}",
                reply_markup=get_back_kb()
            )
            return
        
        config = Config(
            user_id=user.id,
            server_id=server_id,
            name=config_name,
            public_key=config_data.public_key,
            preshared_key=config_data.preshared_key,
            allowed_ips=config_data.allowed_ips,
            client_ip=config_data.client_ip,
            is_active=True
        )
        session.add(config)
        
        expires_at = datetime.utcnow() + timedelta(days=3)
        subscription = Subscription(
            user_id=user.id,
            tariff_type="trial",
            days_total=3,
            expires_at=expires_at,
            is_gift=False
        )
        session.add(subscription)
        
        user.trial_used = True
        await session.commit()
        
        await callback.message.edit_text(
            "✅ *Пробный период активирован!*\n\n"
            f"📅 Действует до: {expires_at.strftime('%d.%m.%Y')}\n\n"
            "Сейчас отправлю тебе конфиг.",
            parse_mode="Markdown"
        )
        
        await send_config_file(
            bot, callback.from_user.id, config_name, config_data, server_id,
            caption="📄 Твой WireGuard конфиг\n\n📷 Если нужен QR-код, его можно найти в кнопке \"Конфиги\""
        )
        
        how_to_seen = await get_user_how_to_seen(callback.from_user.id)
        menu_text = (
            "Управление сервисом — кнопками ниже:\n\n"
            "📱 *Конфиги* — параметры подключения, QR-коды\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник на связи!"
        )
        await bot.send_message(
            callback.from_user.id,
            menu_text,
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(callback.from_user.id, True, how_to_seen)
        )


@router.callback_query(F.data.startswith("tariff_"))
async def tariff_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tariff_key = callback.data.replace("tariff_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return
    
    tariff = TARIFFS[tariff_key]
    
    if tariff["price"] == 0:
        await callback.answer("Этот тариф недоступен для покупки", show_alert=True)
        return
    
    # Проверяем скидку 50% для рефералов (первая оплата)
    has_referral_discount = False
    discounted_price = tariff["price"]
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == callback.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user and user.referrer_id and not user.first_payment_done:
            has_referral_discount = True
            discounted_price = tariff["price"] // 2  # 50% скидка
    
    await state.update_data(selected_tariff=tariff_key, has_referral_discount=has_referral_discount)
    # Сразу устанавливаем состояние ожидания чека — можно отправить фото до нажатия кнопки
    await state.set_state(PaymentStates.waiting_for_receipt)
    
    if has_referral_discount:
        await callback.message.edit_text(
            f"💳 *Оплата тарифа: {tariff['name']}*\n\n"
            f"🎁 *Скидка 50% по реферальной программе!*\n"
            f"💰 Сумма: *{discounted_price}₽* (вместо {tariff['price']}₽)\n\n"
            f"📱 Переведите на номер:\n"
            f"`{PAYMENT_PHONE}`\n"
            f"(Сбербанк или Т-Банк)\n\n"
            f"После оплаты отправьте фото чека.",
            parse_mode="Markdown",
            reply_markup=get_payment_kb()
        )
    else:
        await callback.message.edit_text(
            f"💳 *Оплата тарифа: {tariff['name']}*\n\n"
            f"💰 Сумма: *{tariff['price']}₽*\n\n"
            f"📱 Переведите на номер:\n"
            f"`{PAYMENT_PHONE}`\n"
            f"(Сбербанк или Т-Банк)\n\n"
            f"После оплаты отправьте фото чека.",
            parse_mode="Markdown",
            reply_markup=get_payment_kb()
        )


@router.callback_query(F.data == "send_receipt")
async def send_receipt(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(PaymentStates.waiting_for_receipt)
    await callback.message.edit_text(
        "📸 *Отправьте фото чека об оплате*\n\n"
        "Просто отправьте фотографию в этот чат.",
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "cancel_payment")
async def cancel_payment(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    has_sub = await check_has_subscription(callback.from_user.id)
    await callback.message.edit_text(
        "❌ Оплата отменена\n\n"
        "🛡️ Блокировщик рекламы, да и всего-то",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(callback.from_user.id, has_sub)
    )


@router.message(PaymentStates.waiting_for_receipt, F.photo)
async def process_receipt(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    tariff_key = data.get("selected_tariff")
    has_referral_discount = data.get("has_referral_discount", False)
    
    if not tariff_key or tariff_key not in TARIFFS:
        await message.answer("❌ Ошибка: тариф не выбран. Начните сначала.")
        await state.clear()
        return
    
    tariff = TARIFFS[tariff_key]
    original_price = tariff["price"]
    # Если есть скидка 50% — ожидаем половину суммы
    expected_amount = original_price // 2 if has_referral_discount else original_price
    photo = message.photo[-1]
    
    await message.answer("⏳ Обрабатываю чек...")
    
    ocr_result = None
    ocr_text = "OCR недоступен"
    amount_matched = False
    
    try:
        file = await bot.get_file(photo.file_id)
        file_bytes = await bot.download_file(file.file_path)
        ocr_result = await OCRService.extract_amount(file_bytes.read())
        ocr_text = OCRService.format_ocr_result(ocr_result)
        
        if ocr_result and ocr_result.get("most_likely_amount") == expected_amount:
            amount_matched = True
    except Exception as e:
        logger.error(f"Ошибка OCR: {e}")
        ocr_text = "❌ Ошибка распознавания"
    
    user_id = None
    user_telegram_id = message.from_user.id
    user_username = message.from_user.username
    user_phone = None
    payment_id = None
    referrer_id = None
    referrer_telegram_id = None
    referrer_percent = 10.0
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id).options(
            selectinload(User.configs),
            selectinload(User.subscriptions),
            selectinload(User.referrer)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await message.answer("❌ Ошибка: пользователь не найден")
            return
        
        user_id = user.id
        user_phone = user.phone
        has_config = len(user.configs) > 0
        config_count = len(user.configs)
        
        # Сохраняем инфо о реферере для начисления бонуса
        if user.referrer:
            referrer_id = user.referrer.id
            referrer_telegram_id = user.referrer.telegram_id
            referrer_percent = user.referrer.referral_percent
        
        active_sub = None
        for sub in user.subscriptions:
            if sub.expires_at is None:
                active_sub = sub
                break
            if sub.expires_at > datetime.utcnow():
                if active_sub is None or sub.expires_at > active_sub.expires_at:
                    active_sub = sub
        
        payment = Payment(
            user_id=user.id,
            tariff_type=tariff_key,
            amount=expected_amount,  # Сохраняем фактическую сумму (со скидкой если есть)
            receipt_file_id=photo.file_id,
            ocr_result=ocr_result["raw_text"] if ocr_result else None,
            status="approved" if amount_matched else "pending",
            has_referral_discount=has_referral_discount
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        payment_id = payment.id
    
    await state.clear()
    
    user_info = f"@{user_username}" if user_username else message.from_user.full_name
    phone_info = f"📞 Телефон: `{user_phone}`" if user_phone and user_phone != "5553535" else "📞 Телефон: не указан"
    
    if amount_matched:
        days = tariff.get("days", 30)
        config_name = None
        config_created = False
        new_expires = None
        
        server_id = None
        if not has_config:
            config_name = user_username if user_username else f"user{user_telegram_id}"
            success, config_data, server_id, msg = await create_config_multi(config_name, user_telegram_id)
            if success:
                config_created = True
            else:
                logger.error(f"Ошибка создания конфига: {msg}")
        
        async with async_session() as session:
            if active_sub and active_sub.expires_at:
                stmt_sub = select(Subscription).where(Subscription.id == active_sub.id)
                result_sub = await session.execute(stmt_sub)
                sub = result_sub.scalar_one_or_none()
                if sub:
                    new_expires = sub.expires_at + timedelta(days=days)
                    sub.expires_at = new_expires
                    sub.notified_3_days = False
            else:
                new_expires = datetime.utcnow() + timedelta(days=days)
                subscription = Subscription(
                    user_id=user_id,
                    tariff_type=tariff_key,
                    days_total=days,
                    expires_at=new_expires,
                    is_gift=False
                )
                session.add(subscription)
            
            if config_created and config_data:
                config = Config(
                    user_id=user_id,
                    server_id=server_id,
                    name=config_name,
                    public_key=config_data.public_key,
                    preshared_key=config_data.preshared_key,
                    allowed_ips=config_data.allowed_ips,
                    client_ip=config_data.client_ip,
                    is_active=True
                )
                session.add(config)
            
            # Отмечаем первую оплату и начисляем бонус рефереру
            stmt_user = select(User).where(User.id == user_id)
            result_user = await session.execute(stmt_user)
            paying_user = result_user.scalar_one_or_none()
            if paying_user and not paying_user.first_payment_done:
                paying_user.first_payment_done = True
            
            # Начисляем бонус рефереру
            if referrer_id:
                stmt_referrer = select(User).where(User.id == referrer_id)
                result_referrer = await session.execute(stmt_referrer)
                referrer = result_referrer.scalar_one_or_none()
                if referrer:
                    bonus = expected_amount * (referrer_percent / 100)
                    referrer.referral_balance += bonus
                    # Уведомим реферера после коммита
            
            await session.commit()
        
        # Уведомляем реферера о начислении бонуса
        if referrer_telegram_id:
            bonus = expected_amount * (referrer_percent / 100)
            try:
                await bot.send_message(
                    referrer_telegram_id,
                    f"🎉 *Реферальный бонус!*\n\n"
                    f"Твой реферал оплатил подписку.\n"
                    f"💰 Тебе начислено: *{int(bonus)}₽*",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления реферера: {e}")
        
        await message.answer(
            f"✅ *Оплата подтверждена автоматически!*\n\n"
            f"📋 Тариф: {tariff['name']}\n"
            f"📅 Действует до: {new_expires.strftime('%d.%m.%Y')}\n",
            parse_mode="Markdown"
        )
        
        if config_created:
            await send_config_file(
                bot, user_telegram_id, config_name, config_data, server_id,
                caption="📄 Твой WireGuard конфиг\n\n📷 Если нужен QR-код, его можно найти в кнопке \"Конфиги\""
            )
        
        how_to_seen = await get_user_how_to_seen(user_telegram_id)
        menu_text = (
            "Управление сервисом — кнопками ниже:\n\n"
            "📱 *Конфиги* — параметры подключения, QR-коды\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник на связи!"
        )
        await message.answer(
            menu_text,
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(user_telegram_id, True, how_to_seen)
        )
        
        discount_info = "🎁 Скидка 50% (реферал)\n" if has_referral_discount else ""
        referral_info = f"👥 Реферер ID: {referrer_telegram_id}\n" if referrer_telegram_id else ""
        await bot.send_photo(
            ADMIN_ID,
            photo.file_id,
            caption=(
                f"✅ *Платёж подтверждён автоматически*\n\n"
                f"👤 Пользователь: {user_info}\n"
                f"🆔 ID: `{user_telegram_id}`\n"
                f"{phone_info}\n"
                f"📋 Тариф: {tariff['name']}\n"
                f"{discount_info}"
                f"💵 Сумма: {expected_amount}₽\n"
                f"{referral_info}\n"
                f"{ocr_text}"
            ),
            parse_mode="Markdown"
        )
    else:
        has_sub = await check_has_subscription(user_telegram_id)
        await message.answer(
            "✅ *Чек получен!*\n\n"
            "Сумма не распознана автоматически.\n"
            "Мы проверим его вручную и скоро напишем!",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(user_telegram_id, has_sub)
        )
        
        discount_info = "🎁 Скидка 50% (реферал)\n" if has_referral_discount else ""
        referral_info = f"👥 Реферер ID: {referrer_telegram_id}\n" if referrer_telegram_id else ""
        await bot.send_photo(
            ADMIN_ID,
            photo.file_id,
            caption=(
                f"💰 *Новый платёж (требует проверки)*\n\n"
                f"👤 Пользователь: {user_info}\n"
                f"🆔 ID: `{user_telegram_id}`\n"
                f"{phone_info}\n"
                f"📋 Тариф: {tariff['name']}\n"
                f"{discount_info}"
                f"💵 Ожидаемая сумма: {expected_amount}₽\n"
                f"{referral_info}\n"
                f"{ocr_text}"
            ),
            parse_mode="Markdown",
            reply_markup=get_payment_review_kb(payment_id)
        )


@router.message(PaymentStates.waiting_for_receipt, F.document)
async def process_receipt_document(message: Message, state: FSMContext, bot: Bot):
    """Обработка документов (PDF и др.) — отправляем админу на ручную проверку"""
    data = await state.get_data()
    tariff_key = data.get("selected_tariff")
    
    if not tariff_key or tariff_key not in TARIFFS:
        await message.answer("❌ Ошибка: тариф не выбран. Начните сначала.")
        await state.clear()
        return
    
    tariff = TARIFFS[tariff_key]
    document = message.document
    
    user_id = None
    user_telegram_id = message.from_user.id
    user_username = message.from_user.username
    user_phone = None
    payment_id = None
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await message.answer("❌ Ошибка: пользователь не найден")
            return
        
        user_id = user.id
        user_phone = user.phone
        
        # Создаём платёж со статусом pending (ручная проверка)
        payment = Payment(
            user_id=user.id,
            tariff_type=tariff_key,
            amount=tariff["price"],
            receipt_file_id=document.file_id,
            ocr_result=f"Документ: {document.file_name or 'без имени'}",
            status="pending"
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        payment_id = payment.id
    
    await state.clear()
    
    user_info = f"@{user_username}" if user_username else message.from_user.full_name
    phone_info = f"📞 Телефон: `{user_phone}`" if user_phone and user_phone != "5553535" else "📞 Телефон: не указан"
    
    has_sub = await check_has_subscription(user_telegram_id)
    await message.answer(
        "✅ *Документ получен!*\n\n"
        "Мы проверим его вручную и скоро напишем!",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(user_telegram_id, has_sub)
    )
    
    # Отправляем документ админу
    await bot.send_document(
        ADMIN_ID,
        document.file_id,
        caption=(
            f"📄 *Новый платёж (документ, требует проверки)*\n\n"
            f"👤 Пользователь: {user_info}\n"
            f"🆔 ID: `{user_telegram_id}`\n"
            f"{phone_info}\n"
            f"📋 Тариф: {tariff['name']}\n"
            f"💵 Сумма: {tariff['price']}₽\n\n"
            f"📎 Файл: {document.file_name or 'без имени'}"
        ),
        parse_mode="Markdown",
        reply_markup=get_payment_review_kb(payment_id)
    )


@router.callback_query(F.data == "my_configs")
async def my_configs(callback: CallbackQuery):
    await callback.answer()
    async with async_session() as session:
        stmt = select(User).where(
            User.telegram_id == callback.from_user.id
        ).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user or not user.configs:
            await callback.message.edit_text(
                "📭 У тебя пока нет конфигов.\n\n"
                "Нажми \"Получить конфиг\", чтобы начать.",
                reply_markup=get_no_configs_kb()
            )
            return
        
        await callback.message.edit_text(
            f"📱 *Твои конфиги ({len(user.configs)}):*\n\n"
            "🟢 — активен\n"
            "🔴 — отключен",
            parse_mode="Markdown",
            reply_markup=get_configs_kb(user.configs)
        )


@router.callback_query(F.data.startswith("config_") & ~F.data.startswith("config_request"))
async def config_detail(callback: CallbackQuery):
    await callback.answer()
    config_id = int(callback.data.replace("config_", ""))
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config or config.user.telegram_id != callback.from_user.id:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        # Получаем информацию о сервере
        server_deleted = False
        if config.server_id:
            server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
            if server:
                server_name = server.name
            else:
                server_name = "⚠️ Сервер удалён"
                server_deleted = True
        else:
            # server_id = None означает что сервер был удалён
            server_name = "⚠️ Сервер бессрочно выбыл из работы"
            server_deleted = True
        
        # Если сервер удалён - конфиг всегда отключен
        if server_deleted:
            status = "🔴 Отключен"
        else:
            status = "🟢 Активен" if config.is_active else "🔴 Отключен"
        
        traffic_text = ""
        if config.public_key and not server_deleted:
            traffic_stats = await WireGuardService.get_traffic_stats()
            if config.public_key in traffic_stats:
                stats = traffic_stats[config.public_key]
                received = WireGuardService.format_bytes(stats['received'])
                sent = WireGuardService.format_bytes(stats['sent'])
                total = WireGuardService.format_bytes(stats['received'] + stats['sent'])
                traffic_text = f"\n\n📊 *Трафик:*\n⬇️ Получено: {received}\n⬆️ Отправлено: {sent}\n📈 Всего: {total}"
        
        server_warning = ""
        if server_deleted:
            server_warning = "\n\n⚠️ *Этот конфиг больше не работает.*\nСервер бессрочно выбыл из работы.\nЗапроси новый конфиг."
        
        await callback.message.edit_text(
            f"📱 *Конфиг: {config.name}*\n\n"
            f"Статус: {status}\n"
            f"🌍 Сервер: {server_name}\n"
            f"IP: `{config.client_ip}`\n"
            f"Создан: {config.created_at.strftime('%d.%m.%Y')}"
            f"{traffic_text}"
            f"{server_warning}",
            parse_mode="Markdown",
            reply_markup=get_config_detail_kb(config.id, config.is_active, server_deleted)
        )


@router.callback_query(F.data.startswith("download_config_"))
async def download_config(callback: CallbackQuery, bot: Bot):
    config_id = int(callback.data.replace("download_config_", ""))
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config or config.user.telegram_id != callback.from_user.id:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        if LOCAL_MODE:
            await callback.answer("В локальном режиме файлы недоступны", show_alert=True)
            return
        
        config_path = WireGuardService.get_config_file_path(config.name)
        
        if os.path.exists(config_path):
            await bot.send_document(
                callback.from_user.id,
                FSInputFile(config_path),
                caption=f"📄 Конфиг: {config.name}",
                parse_mode=None
            )
            await callback.answer("✅ Конфиг отправлен")
        else:
            await callback.answer("❌ Файл конфига не найден", show_alert=True)


@router.callback_query(F.data.startswith("qr_config_"))
async def qr_config(callback: CallbackQuery, bot: Bot):
    config_id = int(callback.data.replace("qr_config_", ""))
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config or config.user.telegram_id != callback.from_user.id:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        if LOCAL_MODE:
            await callback.answer("В локальном режиме файлы недоступны", show_alert=True)
            return
        
        qr_path = WireGuardService.get_qr_file_path(config.name)
        
        if os.path.exists(qr_path):
            await bot.send_photo(
                callback.from_user.id,
                FSInputFile(qr_path),
                caption=f"📷 QR-код: {config.name}"
            )
            await callback.answer("✅ QR-код отправлен")
        else:
            await callback.answer("❌ QR-код не найден", show_alert=True)


@router.callback_query(F.data.startswith("user_delete_config_"))
async def user_delete_config(callback: CallbackQuery):
    """Запрос на удаление конфига пользователем"""
    await callback.answer()
    config_id = int(callback.data.replace("user_delete_config_", ""))
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config or config.user.telegram_id != callback.from_user.id:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        from keyboards.user_kb import get_user_config_delete_confirm_kb
        await callback.message.edit_text(
            f"🗑 *Удаление конфига*\n\n"
            f"Ты уверен, что хочешь удалить конфиг `{config.name}`?\n\n"
            f"⚠️ Это действие нельзя отменить!",
            parse_mode="Markdown",
            reply_markup=get_user_config_delete_confirm_kb(config_id)
        )


@router.callback_query(F.data.startswith("user_confirm_delete_config_"))
async def user_confirm_delete_config(callback: CallbackQuery):
    """Подтверждение удаления конфига пользователем"""
    await callback.answer()
    config_id = int(callback.data.replace("user_confirm_delete_config_", ""))
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config or config.user.telegram_id != callback.from_user.id:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        config_name = config.name
        server_id = config.server_id
        
        # Удаляем конфиг с сервера WireGuard (если сервер существует)
        if server_id:
            server = await WireGuardMultiService.get_server_by_id(session, server_id)
            if server:
                try:
                    await WireGuardMultiService.delete_config(server, config_name)
                except Exception as e:
                    logger.error(f"Ошибка удаления конфига с сервера: {e}")
        
        # Удаляем из БД
        await session.delete(config)
        await session.commit()
    
    await callback.message.edit_text(
        f"✅ Конфиг `{config_name}` удалён",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(callback.from_user.id, True)
    )


@router.callback_query(F.data == "my_subscription")
async def my_subscription(callback: CallbackQuery):
    await callback.answer()
    async with async_session() as session:
        stmt = select(User).where(
            User.telegram_id == callback.from_user.id
        ).options(
            selectinload(User.subscriptions),
            selectinload(User.configs)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user or not user.subscriptions:
            await callback.message.edit_text(
                "📭 У тебя нет активной подписки.\n\n"
                "Нажми \"Получить конфиг\", чтобы начать.",
                reply_markup=get_no_subscription_kb()
            )
            return
        
        active_sub = None
        for sub in user.subscriptions:
            if sub.expires_at is None:
                active_sub = sub
                break
            if sub.expires_at > datetime.utcnow():
                if active_sub is None or sub.expires_at > active_sub.expires_at:
                    active_sub = sub
        
        if not active_sub:
            await callback.message.edit_text(
                "❌ *Подписка истекла*\n\n"
                "Продли подписку для возобновления доступа.",
                parse_mode="Markdown",
                reply_markup=get_no_subscription_kb()
            )
            return
        
        if active_sub.expires_at is None:
            status_text = "♾ *Бессрочная подписка*"
            expires_text = "Не ограничена"
            days_left = 0
        else:
            days_left = (active_sub.expires_at - datetime.utcnow()).days
            status_text = f"✅ *Подписка активна*"
            expires_text = f"{active_sub.expires_at.strftime('%d.%m.%Y')} ({days_left} дн.)"
        
        gift_text = " 🎁" if active_sub.is_gift else ""
        
        total_received = 0
        total_sent = 0
        traffic_stats = await WireGuardService.get_traffic_stats()
        for config in user.configs:
            if config.public_key and config.public_key in traffic_stats:
                stats = traffic_stats[config.public_key]
                total_received += stats['received']
                total_sent += stats['sent']
        
        total_traffic = WireGuardService.format_bytes(total_received + total_sent)
        traffic_text = f"\n\n📊 *Общий трафик:* {total_traffic}" if (total_received + total_sent) > 0 else ""
        
        await callback.message.edit_text(
            f"{status_text}{gift_text}\n\n"
            f"📅 Действует до: {expires_text}\n"
            f"📱 Конфигов: {len(user.configs)}"
            f"{traffic_text}",
            parse_mode="Markdown",
            reply_markup=get_subscription_kb(has_active=True)
        )


@router.callback_query(F.data == "request_extra_config")
async def request_extra_config(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    # Проверяем подписку на канал
    if await is_channel_required():
        is_subscribed = await check_channel_subscription(bot, callback.from_user.id)
        if not is_subscribed:
            channel = await get_channel_name()
            await callback.message.edit_text(
                f"📢 Для запроса конфига необходимо подписаться на канал @{channel}:",
                parse_mode="Markdown",
                reply_markup=get_check_subscription_kb(channel)
            )
            await state.update_data(after_subscription="extra_config")
            return
    
    async with async_session() as session:
        stmt = select(User).where(
            User.telegram_id == callback.from_user.id
        ).options(selectinload(User.subscriptions), selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return
        
        has_active_sub = False
        for sub in user.subscriptions:
            if sub.expires_at is None or sub.expires_at > datetime.utcnow():
                has_active_sub = True
                break
        
        if not has_active_sub:
            await callback.answer("❌ Нужна активная подписка для запроса конфига", show_alert=True)
            return
        
        # Проверяем лимит конфигов
        current_configs = len(user.configs) if user.configs else 0
        
        # Сначала проверяем индивидуальный лимит пользователя
        if user.max_configs and user.max_configs > 0:
            max_limit = user.max_configs
        else:
            # Используем глобальный лимит
            global_limit = await get_setting("max_configs") or "0"
            max_limit = int(global_limit) if global_limit != "0" else 0
        
        if max_limit > 0 and current_configs >= max_limit:
            await callback.message.edit_text(
                f"❌ *Достигнут лимит конфигов*\n\n"
                f"У тебя уже {current_configs} конфигов (максимум: {max_limit}).\n\n"
                f"Напиши @agdelesha если нужно больше.",
                parse_mode="Markdown",
                reply_markup=get_main_menu_kb(callback.from_user.id, True)
            )
            return
    
    from keyboards.user_kb import get_device_input_cancel_kb
    await callback.message.edit_text(
        "📱 *Дополнительный конфиг*\n\n"
        "Введи название устройства:\n"
        "(например: iPhone, MacBook, Windows ПК)",
        parse_mode="Markdown",
        reply_markup=get_device_input_cancel_kb()
    )
    await state.set_state(ConfigRequestStates.waiting_for_device)


@router.message(ConfigRequestStates.waiting_for_device)
async def process_device_request(message: Message, state: FSMContext, bot: Bot):
    device_name = message.text
    
    async with async_session() as session:
        stmt = select(User).where(
            User.telegram_id == message.from_user.id
        ).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await message.answer("❌ Ошибка: пользователь не найден")
            await state.clear()
            return
        
        user_id = user.id
        user_phone = user.phone
        config_count = len(user.configs)
        config_names = [c.name for c in user.configs]
        username = user.username
        telegram_id = user.telegram_id
    
    await state.clear()
    
    # Проверяем, нужно ли подтверждение админа
    if await is_config_approval_required():
        # Отправляем запрос админу
        user_info = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
        phone_info = f"📞 Телефон: {user_phone}" if user_phone and user_phone != "5553535" else "📞 Телефон: не указан"
        configs_info = ", ".join(config_names) if config_names else "нет"
        
        await message.answer(
            "✅ Запрос отправлен!\n\n"
            "Скоро создадим конфиг и пришлём тебе.",
            reply_markup=get_main_menu_kb(message.from_user.id, True)
        )
        
        await bot.send_message(
            ADMIN_ID,
            f"📱 Запрос дополнительного конфига\n\n"
            f"👤 Пользователь: {user_info}\n"
            f"🆔 ID: {message.from_user.id}\n"
            f"{phone_info}\n"
            f"📱 Текущие конфиги ({config_count}): {configs_info}\n\n"
            f"🖥 Устройство: {device_name}",
            reply_markup=get_config_request_kb(user_id)
        )
    else:
        # Создаём конфиг автоматически
        # Название: никустройство (транслитерация + очистка от спецсимволов)
        base_name = username or f"user{telegram_id}"
        # Транслитерируем русские буквы в английские
        device_translit = transliterate_ru_to_en(device_name)
        clean_device = re.sub(r'[^\w]', '', device_translit)[:15]
        config_name = f"{base_name}{clean_device}"
        
        # Отправляем сообщение "подождите"
        wait_msg = await message.answer(
            "⏳ Создаю конфиг, подожди несколько секунд..."
        )
        
        success, config_data, server_id, msg = await create_config_multi(config_name, telegram_id)
        
        # Удаляем сообщение "подождите"
        try:
            await wait_msg.delete()
        except:
            pass
        
        if not success:
            await message.answer(
                f"❌ Ошибка создания конфига: {msg}\n\n"
                "Напиши @agdelesha для помощи.",
                reply_markup=get_main_menu_kb(message.from_user.id, True)
            )
            return
        
        # Сохраняем конфиг в БД
        async with async_session() as session:
            new_config = Config(
                user_id=user_id,
                server_id=server_id,
                name=config_name,
                public_key=config_data.public_key,
                preshared_key=config_data.preshared_key,
                allowed_ips=config_data.allowed_ips,
                client_ip=config_data.client_ip,
                is_active=True
            )
            session.add(new_config)
            await session.commit()
        
        # Отправляем конфиг пользователю
        await send_config_file(
            bot, message.from_user.id, config_name, config_data, server_id,
            caption=f"📄 Твой новый конфиг для {device_name}\n\n📷 QR-код можно найти в меню «Конфиги»"
        )
        
        await message.answer(
            "✅ Конфиг создан!",
            reply_markup=get_main_menu_kb(message.from_user.id, True)
        )


@router.callback_query(F.data == "cancel_device_input")
async def cancel_device_input(callback: CallbackQuery, state: FSMContext):
    """Отмена ввода названия устройства"""
    await callback.answer()
    await state.clear()
    has_sub = await check_has_subscription(callback.from_user.id)
    await callback.message.edit_text(
        "❌ Запрос отменён",
        reply_markup=get_main_menu_kb(callback.from_user.id, has_sub)
    )


@router.message(F.text)
async def handle_text_message(message: Message, state: FSMContext, bot: Bot):
    """Обработчик текстовых сообщений для AI ассистента"""
    from services.ai_assistant import get_ai_response, UserContext
    
    if not message.text or message.text.startswith('/'):
        return
    
    # Не перехватываем сообщения если пользователь в FSM-состоянии
    current_state = await state.get_state()
    if current_state is not None:
        return
    
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
        
        # Получаем контекст пользователя из БД
        user = await get_user_by_telegram_id(message.from_user.id)
        context = UserContext()
        
        if user:
            context.trial_used = user.trial_used
            context.configs_count = len(user.configs) if user.configs else 0
            
            # Проверяем активную подписку
            if user.subscriptions:
                for sub in user.subscriptions:
                    if sub.expires_at and sub.expires_at > datetime.utcnow():
                        context.has_subscription = True
                        context.days_left = (sub.expires_at - datetime.utcnow()).days
                        break
        
        # Передаём user_id и контекст для AI
        ai_response = await get_ai_response(
            message.text, 
            user_id=message.from_user.id,
            context=context
        )
        
        # AI всегда возвращает текст (fallback при ошибках)
        await message.answer(ai_response.text, parse_mode=None)
        
        # Обрабатываем действие от AI
        if ai_response.action:
            await handle_ai_action(message, state, bot, ai_response.action, context)
    except Exception as e:
        logger.error(f"Error in AI handler: {e}")
        await message.answer(
            "Извините, произошла ошибка. Попробуйте позже."
        )


async def handle_ai_action(message: Message, state: FSMContext, bot: Bot, action: str, context):
    """Обработка действий от AI"""
    from services.ai_assistant import UserContext
    
    # Получаем пользователя для проверки скидки
    user = await get_user_by_telegram_id(message.from_user.id)
    has_referral_discount = user and user.referrer_id and not user.first_payment_done
    
    if action == "activate_trial":
        if not context.trial_used:
            # Симулируем нажатие кнопки пробного периода
            await activate_trial_from_ai(message, bot)
        else:
            await message.answer("Пробный период уже был использован. Выбери тариф для продолжения:")
            await message.answer(
                "📋 Выбери тарифный план:",
                reply_markup=get_tariffs_kb(show_trial=False, has_referral_discount=has_referral_discount)
            )
    
    elif action == "show_tariffs":
        show_trial = not context.trial_used
        await message.answer(
            "📋 Выбери тарифный план:",
            reply_markup=get_tariffs_kb(show_trial=show_trial, has_referral_discount=has_referral_discount)
        )
    
    elif action == "show_configs":
        if context.has_subscription:
            if user and user.configs:
                await message.answer(
                    "📱 Твои конфиги:",
                    reply_markup=get_configs_kb(user.configs)
                )
            else:
                await message.answer("У тебя пока нет конфигов.")
        else:
            await message.answer("Сначала нужно оформить подписку, чтобы получить конфиг.")
    
    elif action == "show_subscription":
        if context.has_subscription:
            await message.answer(
                f"📊 Твоя подписка активна!\n"
                f"Осталось дней: {context.days_left}\n"
                f"Конфигов: {context.configs_count}",
                reply_markup=get_subscription_kb()
            )
        else:
            await message.answer(
                "У тебя нет активной подписки. Хочешь оформить?",
                reply_markup=get_tariffs_kb(show_trial=not context.trial_used, has_referral_discount=has_referral_discount)
            )
    
    elif action == "create_config":
        if context.has_subscription:
            # Просим ввести название устройства
            from keyboards.user_kb import get_device_input_cancel_kb
            await message.answer(
                "📱 *Дополнительный конфиг*\n\n"
                "Введи название устройства:\n"
                "(например: iPhone, MacBook, Windows ПК)",
                parse_mode="Markdown",
                reply_markup=get_device_input_cancel_kb()
            )
            await state.set_state(ConfigRequestStates.waiting_for_device)
        else:
            # Нет подписки — предлагаем trial или тарифы
            if not context.trial_used:
                await message.answer("Сначала нужна подписка. Хочешь попробовать 3 дня бесплатно?")
                await activate_trial_from_ai(message, bot)
            else:
                await message.answer(
                    "Для создания конфига нужна активная подписка. Выбери тариф:",
                    reply_markup=get_tariffs_kb(show_trial=False, has_referral_discount=has_referral_discount)
                )
    
    elif action == "show_referral":
        # Показываем реферальное меню
        await message.answer(
            "👥 *Реферальная программа*\n\n"
            "Приглашай друзей и зарабатывай!\n\n"
            "🎁 Твой друг получит скидку 50% на первую оплату\n"
            "💰 Ты получишь % от каждого его платежа\n\n"
            "Выбери действие:",
            parse_mode="Markdown",
            reply_markup=get_referral_menu_kb()
        )


async def activate_trial_from_ai(message: Message, bot: Bot):
    """Активация пробного периода через AI"""
    
    user = await get_user_by_telegram_id(message.from_user.id)
    
    if not user or user.trial_used:
        await message.answer("Пробный период уже использован.")
        return
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        result = await session.execute(stmt)
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            return
        
        db_user.trial_used = True
        
        # Создаём подписку на 3 дня
        trial_sub = Subscription(
            user_id=db_user.id,
            tariff_type="trial",
            days_total=3,
            expires_at=datetime.utcnow() + timedelta(days=3)
        )
        session.add(trial_sub)
        await session.commit()
    
    # Создаём конфиг
    username = message.from_user.username or f"user{message.from_user.id}"
    config_name = username
    
    success, config_data, server_id, error_msg = await create_config_multi(config_name, message.from_user.id)
    
    if not success:
        await message.answer(
            f"Пробный период активирован, но произошла ошибка создания конфига: {error_msg}\n"
            "Напиши @agdelesha для помощи."
        )
        return
    
    # Сохраняем конфиг в БД
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        result = await session.execute(stmt)
        db_user = result.scalar_one_or_none()
        
        if db_user:
            new_config = Config(
                user_id=db_user.id,
                server_id=server_id,
                name=config_name,
                public_key=config_data.public_key,
                preshared_key=config_data.preshared_key,
                allowed_ips=config_data.allowed_ips,
                client_ip=config_data.client_ip,
                is_active=True
            )
            session.add(new_config)
            await session.commit()
    
    # Отправляем конфиг
    await send_config_file(
        bot, message.from_user.id, config_name, config_data, server_id,
        caption="🎉 Пробный период активирован! Вот твой конфиг на 3 дня.\n\n"
                "Скачай WireGuard и импортируй этот файл."
    )


# ===== РЕФЕРАЛЬНАЯ ПРОГРАММА =====

@router.callback_query(F.data == "referral_menu")
async def referral_menu(callback: CallbackQuery, state: FSMContext):
    """Главное меню реферальной программы"""
    await callback.answer()
    await state.clear()
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == callback.from_user.id).options(
            selectinload(User.referrals).selectinload(User.payments)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return
        
        # Считаем статистику
        referral_count = len(user.referrals) if user.referrals else 0
        
        # Сумма оплат рефералов (только approved)
        total_referral_payments = 0
        for ref in (user.referrals or []):
            for payment in (ref.payments or []):
                if payment.status == "approved":
                    total_referral_payments += payment.amount
        
        balance = user.referral_balance
        percent = user.referral_percent
        
        has_balance = balance >= 1000
        
        await callback.message.edit_text(
            f"👥 *Реферальная программа*\n\n"
            f"📊 *Твоя статистика:*\n"
            f"├ Приглашено: {referral_count} чел.\n"
            f"├ Оплаты рефералов: {int(total_referral_payments)}₽\n"
            f"├ Твой %: {int(percent)}%\n"
            f"└ Накоплено: {int(balance)}₽\n\n"
            f"💡 Приглашай друзей и получай {int(percent)}% от их оплат!\n"
            f"🎁 Твои рефералы получают скидку 50% на первую оплату!",
            parse_mode="Markdown",
            reply_markup=get_referral_menu_kb(has_balance=has_balance)
        )


@router.callback_query(F.data == "referral_get_link")
async def referral_get_link(callback: CallbackQuery, bot: Bot):
    """Получить реферальную ссылку"""
    await callback.answer()
    
    # Получаем username бота
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    
    # Формируем ссылку с telegram_id пользователя
    referral_link = f"https://t.me/{bot_username}?start=ref_{callback.from_user.id}"
    
    await callback.message.edit_text(
        f"🔗 *Твоя реферальная ссылка:*\n\n"
        f"`{referral_link}`\n\n"
        f"📤 Отправь эту ссылку друзьям!\n"
        f"💰 Ты получишь % от каждой их оплаты.\n"
        f"🎁 Они получат скидку 50% на первую оплату!",
        parse_mode="Markdown",
        reply_markup=get_referral_back_kb()
    )
    
    # Отправляем ссылку отдельным сообщением для удобного копирования
    await callback.message.answer(referral_link)


@router.callback_query(F.data == "referral_withdraw")
async def referral_withdraw(callback: CallbackQuery, state: FSMContext):
    """Начать вывод средств"""
    await callback.answer()
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == callback.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return
        
        if user.referral_balance < 1000:
            await callback.answer(f"❌ Минимальная сумма вывода: 1000₽\nУ тебя: {int(user.referral_balance)}₽", show_alert=True)
            return
        
        # Сохраняем сумму для вывода
        await state.update_data(withdrawal_amount=user.referral_balance, prompt_msg_id=callback.message.message_id)
        await state.set_state(WithdrawalStates.waiting_for_bank)
        
        await callback.message.edit_text(
            f"💸 *Вывод средств*\n\n"
            f"Сумма к выводу: *{int(user.referral_balance)}₽*\n\n"
            f"📝 Введи название банка для перевода:\n"
            f"(например: Сбербанк, Тинькофф, Альфа-Банк)",
            parse_mode="Markdown",
            reply_markup=get_withdrawal_cancel_kb()
        )


@router.message(WithdrawalStates.waiting_for_bank)
async def process_withdrawal_bank(message: Message, state: FSMContext, bot: Bot):
    """Обработка ввода банка"""
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    
    bank = message.text.strip()
    if len(bank) < 2 or len(bank) > 100:
        await message.answer(
            "❌ Введи корректное название банка",
            reply_markup=get_withdrawal_cancel_kb()
        )
        return
    
    # Удаляем предыдущее сообщение
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    await state.update_data(bank=bank)
    
    # Проверяем, есть ли у пользователя телефон
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user and user.phone and user.phone != "5553535":
            # Телефон уже есть — используем его
            await state.update_data(phone=user.phone)
            await process_withdrawal_complete(message, state, bot)
            return
    
    # Запрашиваем телефон
    await state.set_state(WithdrawalStates.waiting_for_phone)
    msg = await message.answer(
        f"📱 *Введи номер телефона для перевода:*\n\n"
        f"Банк: {bank}",
        parse_mode="Markdown",
        reply_markup=get_withdrawal_cancel_kb()
    )
    await state.update_data(prompt_msg_id=msg.message_id)


@router.message(WithdrawalStates.waiting_for_phone)
async def process_withdrawal_phone(message: Message, state: FSMContext, bot: Bot):
    """Обработка ввода телефона"""
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    
    phone = message.text.strip()
    # Простая валидация телефона
    phone_clean = re.sub(r'[^\d+]', '', phone)
    if len(phone_clean) < 10:
        await message.answer(
            "❌ Введи корректный номер телефона",
            reply_markup=get_withdrawal_cancel_kb()
        )
        return
    
    # Удаляем предыдущее сообщение
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    await state.update_data(phone=phone_clean)
    await process_withdrawal_complete(message, state, bot)


async def process_withdrawal_complete(message: Message, state: FSMContext, bot: Bot):
    """Завершение создания заявки на вывод"""
    from keyboards.admin_kb import get_withdrawal_review_kb
    
    data = await state.get_data()
    amount = data.get("withdrawal_amount")
    bank = data.get("bank")
    phone = data.get("phone")
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await state.clear()
            await message.answer("❌ Ошибка: пользователь не найден")
            return
        
        if user.referral_balance < amount:
            await state.clear()
            await message.answer("❌ Недостаточно средств на балансе")
            return
        
        # Создаём заявку на вывод
        withdrawal = WithdrawalRequest(
            user_id=user.id,
            amount=amount,
            bank=bank,
            phone=phone,
            status="pending"
        )
        session.add(withdrawal)
        
        # Списываем средства с баланса
        user.referral_balance -= amount
        
        await session.commit()
        await session.refresh(withdrawal)
        
        withdrawal_id = withdrawal.id
        user_info = f"@{user.username}" if user.username else user.full_name
    
    await state.clear()
    
    # Уведомляем пользователя
    await message.answer(
        f"✅ *Заявка на вывод создана!*\n\n"
        f"💰 Сумма: {int(amount)}₽\n"
        f"🏦 Банк: {bank}\n"
        f"📱 Телефон: {phone}\n\n"
        f"⏳ Ожидай перевода. Обычно это занимает до 24 часов.",
        parse_mode="Markdown",
        reply_markup=get_referral_back_kb()
    )
    
    # Уведомляем админа
    await bot.send_message(
        ADMIN_ID,
        f"💸 *Новая заявка на вывод #{withdrawal_id}*\n\n"
        f"👤 Пользователь: {user_info}\n"
        f"🆔 ID: `{message.from_user.id}`\n"
        f"💰 Сумма: {int(amount)}₽\n"
        f"🏦 Банк: {bank}\n"
        f"📱 Телефон: `{phone}`",
        parse_mode="Markdown",
        reply_markup=get_withdrawal_review_kb(withdrawal_id)
    )
