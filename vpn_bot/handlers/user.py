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
from database import async_session, User, Config, Subscription, Payment
from keyboards.user_kb import (
    get_main_menu_kb, get_tariffs_kb, get_payment_kb, 
    get_back_kb, get_configs_kb, get_config_detail_kb,
    get_no_configs_kb, get_no_subscription_kb, get_subscription_kb, get_how_to_kb,
    get_welcome_kb, get_trial_activated_kb, get_after_config_kb
)
from states.user_states import PaymentStates, RegistrationStates, ConfigRequestStates
from services.wireguard import WireGuardService
from services.ocr import OCRService
from services.settings import is_password_required, is_channel_required, get_bot_password, is_phone_required, is_config_approval_required
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


async def get_or_create_user(telegram_id: int, username: str, full_name: str) -> tuple:
    """Returns (user, is_new_user)"""
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                full_name=full_name
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


async def check_channel_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
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


@router.message(Command("about"))
async def cmd_about(message: Message):
    await message.answer(
        "🌐 Простой и незаметный 🥷🏻\n\n"
        "📩 Связь со мной: @agdelesha",
        parse_mode="Markdown"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    # Удаляем предыдущие сообщения бота
    await delete_bot_messages(bot, message.chat.id, state)
    
    user, is_new = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name
    )
    
    if is_new:
        # Проверяем, нужен ли пароль
        if await is_password_required():
            msg = await message.answer(
                f"👋 Привет, *{message.from_user.first_name}*!\n\n"
                "🔐 Для доступа к боту введите пароль:",
                parse_mode="Markdown"
            )
            await save_bot_message(state, msg.message_id)
            await state.set_state(RegistrationStates.waiting_for_password)
            return
        
        # Проверяем подписку на канал
        if await is_channel_required():
            is_subscribed = await check_channel_subscription(bot, message.from_user.id)
            if not is_subscribed:
                msg = await message.answer(
                    f"👋 Привет, *{message.from_user.first_name}*!\n\n"
                    "📢 Для использования бота подпишитесь на наш канал:",
                    parse_mode="Markdown",
                    reply_markup=get_check_subscription_kb()
                )
                await save_bot_message(state, msg.message_id)
                await state.update_data(after_subscription="registration")
                return
        
        # Проверяем, нужен ли запрос телефона
        if await is_phone_required():
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
            f"Я помогу тебе подключить VPN\n\n"
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
            "Всё управление VPN — кнопками ниже:\n\n"
            "📱 *Конфиги* — информация о подключении, QR-коды и доп. конфигурации\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник всегда на связи!"
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
            f"Я помогу тебе подключить VPN\n\n"
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
    
    entered_password = message.text.strip()
    correct_password = await get_bot_password()
    
    if entered_password != correct_password:
        msg = await message.answer(
            "❌ Неверный пароль. Попробуйте ещё раз:",
            parse_mode="Markdown"
        )
        await save_bot_message(state, msg.message_id)
        return
    
    # Пароль верный, проверяем подписку на канал
    if await is_channel_required():
        is_subscribed = await check_channel_subscription(bot, message.from_user.id)
        if not is_subscribed:
            msg = await message.answer(
                "✅ Пароль принят!\n\n"
                "📢 Теперь подпишитесь на наш канал:",
                parse_mode="Markdown",
                reply_markup=get_check_subscription_kb()
            )
            await save_bot_message(state, msg.message_id)
            await state.update_data(after_subscription="registration")
            await state.set_state(None)
            return
    
    # Проверяем, нужен ли запрос телефона
    if await is_phone_required():
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
    
    data = await state.get_data()
    after_subscription = data.get("after_subscription")
    
    if after_subscription == "registration":
        # Проверяем, нужен ли запрос телефона
        if await is_phone_required():
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
        await callback.message.edit_text(
            "✅ Подписка подтверждена!\n\n"
            "💳 *Продление подписки*\n\n"
            "Выбери тариф для продления.\n"
            "Дни будут добавлены к текущей подписке.",
            parse_mode="Markdown",
            reply_markup=get_tariffs_kb(show_trial=False)
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
            "Всё управление VPN — кнопками ниже:\n\n"
            "📱 *Конфиги* — информация о подключении, QR-коды и доп. конфигурации\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник всегда на связи!"
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
            f"*{callback.from_user.first_name}*, всё просто!\n\n"
            "📲 *Скачать WireGuard:*\n"
            "— iPhone: https://apps.apple.com/app/id1441195209\n"
            "— Другие устройства: https://www.wireguard.com/install/\n\n"
            "💬 *Есть вопросы?* Просто напиши в чат — AI-помощник всегда поможет!\n\n"
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
            "Всё управление VPN — кнопками ниже:\n\n"
            "📱 *Конфиги* — информация о подключении, QR-коды и доп. конфигурации\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник всегда на связи!"
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
        await callback.message.edit_text(
            "❌ Ты уже использовал пробный период.\n\n"
            "Выбери тариф для продолжения:",
            parse_mode="Markdown",
            reply_markup=get_tariffs_kb(show_trial=False)
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
    
    await callback.message.edit_text(
        "📋 *Выбери тарифный план:*",
        parse_mode="Markdown",
        reply_markup=get_tariffs_kb(show_trial=show_trial)
    )


@router.callback_query(F.data == "funnel_get_config")
async def funnel_get_config(callback: CallbackQuery, bot: Bot):
    """Шаг 3 — получение конфига после активации пробного периода"""
    await callback.answer()
    
    user = await get_user_by_telegram_id(callback.from_user.id)
    
    # Активируем пробный период
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == callback.from_user.id)
        result = await session.execute(stmt)
        db_user = result.scalar_one_or_none()
        
        if db_user:
            db_user.trial_used = True
            
            # Создаём подписку на 7 дней
            trial_sub = Subscription(
                user_id=db_user.id,
                tariff_type="trial",
                days_total=7,
                expires_at=datetime.utcnow() + timedelta(days=7)
            )
            session.add(trial_sub)
            await session.commit()
    
    # Создаём конфиг (только username, без telegram_id)
    username = callback.from_user.username or f"user{callback.from_user.id}"
    config_name = username
    
    success, config_data, error_msg = await WireGuardService.create_config(config_name)
    
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
                name=config_name,
                public_key=config_data.public_key,
                preshared_key=config_data.preshared_key,
                allowed_ips=config_data.allowed_ips,
                client_ip=config_data.client_ip,
                is_active=True
            )
            session.add(new_config)
            await session.commit()
    
    # Отправляем конфиг с кнопкой "а как?"
    config_path = WireGuardService.get_config_file_path(config_name)
    
    if not LOCAL_MODE and os.path.exists(config_path):
        await bot.send_document(
            callback.from_user.id,
            FSInputFile(config_path),
            caption="📄 Вот твой конфиг\n\nЧерез 7 дней пробный период закончится.",
            reply_markup=get_after_config_kb()
        )
    else:
        await bot.send_message(
            callback.from_user.id,
            "🔧 [LOCAL_MODE] Конфиг будет отправлен на сервере",
            reply_markup=get_after_config_kb()
        )


@router.callback_query(F.data == "get_vpn")
async def get_vpn(callback: CallbackQuery):
    await callback.answer()
    user = await get_user_by_telegram_id(callback.from_user.id)
    show_trial = not user.trial_used if user else True
    
    await callback.message.edit_text(
        "📋 *Выбери тарифный план:*\n\n"
        "🎁 Пробный — 7 дней бесплатно (один раз)\n"
        "📅 30 дней — 100₽\n"
        "📅 90 дней — 200₽\n"
        "📅 180 дней — 300₽",
        parse_mode="Markdown",
        reply_markup=get_tariffs_kb(show_trial=show_trial)
    )


@router.callback_query(F.data == "extend_subscription")
async def extend_subscription(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    # Проверяем подписку на канал
    if await is_channel_required():
        is_subscribed = await check_channel_subscription(bot, callback.from_user.id)
        if not is_subscribed:
            await callback.message.edit_text(
                "📢 Для продления подписки необходимо подписаться на наш канал:",
                parse_mode="Markdown",
                reply_markup=get_check_subscription_kb()
            )
            await state.update_data(after_subscription="extend")
            return
    
    await callback.message.edit_text(
        "💳 *Продление подписки*\n\n"
        "Выбери тариф для продления.\n"
        "Дни будут добавлены к текущей подписке.",
        parse_mode="Markdown",
        reply_markup=get_tariffs_kb(show_trial=False)
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
        success, config_data, msg = await WireGuardService.create_config(config_name)
        
        if not success:
            await callback.message.edit_text(
                f"❌ Ошибка создания конфига:\n{msg}",
                reply_markup=get_back_kb()
            )
            return
        
        config = Config(
            user_id=user.id,
            name=config_name,
            public_key=config_data.public_key,
            preshared_key=config_data.preshared_key,
            allowed_ips=config_data.allowed_ips,
            client_ip=config_data.client_ip,
            is_active=True
        )
        session.add(config)
        
        expires_at = datetime.utcnow() + timedelta(days=7)
        subscription = Subscription(
            user_id=user.id,
            tariff_type="trial",
            days_total=7,
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
        
        if not LOCAL_MODE:
            config_path = WireGuardService.get_config_file_path(config_name)
            
            if os.path.exists(config_path):
                await bot.send_document(
                    callback.from_user.id,
                    FSInputFile(config_path),
                    caption="📄 Твой WireGuard конфиг\n\n📷 Если нужен QR-код, его можно найти в кнопке \"Конфиги\""
                )
        else:
            await bot.send_message(
                callback.from_user.id,
                "🔧 [LOCAL_MODE] Конфиг будет отправлен на сервере"
            )
        
        how_to_seen = await get_user_how_to_seen(callback.from_user.id)
        menu_text = (
            "Всё управление VPN — кнопками ниже:\n\n"
            "📱 *Конфиги* — информация о подключении, QR-коды и доп. конфигурации\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник всегда на связи!"
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
    
    await state.update_data(selected_tariff=tariff_key)
    # Сразу устанавливаем состояние ожидания чека — можно отправить фото до нажатия кнопки
    await state.set_state(PaymentStates.waiting_for_receipt)
    
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
    
    if not tariff_key or tariff_key not in TARIFFS:
        await message.answer("❌ Ошибка: тариф не выбран. Начните сначала.")
        await state.clear()
        return
    
    tariff = TARIFFS[tariff_key]
    expected_amount = tariff["price"]
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
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == message.from_user.id).options(
            selectinload(User.configs),
            selectinload(User.subscriptions)
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
            amount=tariff["price"],
            receipt_file_id=photo.file_id,
            ocr_result=ocr_result["raw_text"] if ocr_result else None,
            status="approved" if amount_matched else "pending"
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
        
        if not has_config:
            config_name = user_username if user_username else f"user{user_telegram_id}"
            success, config_data, msg = await WireGuardService.create_config(config_name)
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
                    name=config_name,
                    public_key=config_data.public_key,
                    preshared_key=config_data.preshared_key,
                    allowed_ips=config_data.allowed_ips,
                    client_ip=config_data.client_ip,
                    is_active=True
                )
                session.add(config)
            
            await session.commit()
        
        await message.answer(
            f"✅ *Оплата подтверждена автоматически!*\n\n"
            f"📋 Тариф: {tariff['name']}\n"
            f"📅 Действует до: {new_expires.strftime('%d.%m.%Y')}\n",
            parse_mode="Markdown"
        )
        
        if config_created and not LOCAL_MODE:
            config_path = WireGuardService.get_config_file_path(config_name)
            
            if os.path.exists(config_path):
                await bot.send_document(
                    user_telegram_id,
                    FSInputFile(config_path),
                    caption="📄 Твой WireGuard конфиг\n\n📷 Если нужен QR-код, его можно найти в кнопке \"Конфиги\""
                )
        
        how_to_seen = await get_user_how_to_seen(user_telegram_id)
        menu_text = (
            "Всё управление VPN — кнопками ниже:\n\n"
            "📱 *Конфиги* — информация о подключении, QR-коды и доп. конфигурации\n"
            "📊 *Подписка* — детали подписки и продление\n\n"
            "💬 Есть вопросы? Просто напиши — AI-помощник всегда на связи!"
        )
        await message.answer(
            menu_text,
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(user_telegram_id, True, how_to_seen)
        )
        
        await bot.send_photo(
            ADMIN_ID,
            photo.file_id,
            caption=(
                f"✅ *Платёж подтверждён автоматически*\n\n"
                f"👤 Пользователь: {user_info}\n"
                f"🆔 ID: `{user_telegram_id}`\n"
                f"{phone_info}\n"
                f"📋 Тариф: {tariff['name']}\n"
                f"💵 Сумма: {tariff['price']}₽\n\n"
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
        
        await bot.send_photo(
            ADMIN_ID,
            photo.file_id,
            caption=(
                f"💰 *Новый платёж (требует проверки)*\n\n"
                f"👤 Пользователь: {user_info}\n"
                f"🆔 ID: `{user_telegram_id}`\n"
                f"{phone_info}\n"
                f"📋 Тариф: {tariff['name']}\n"
                f"💵 Сумма: {tariff['price']}₽\n\n"
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


@router.callback_query(F.data.startswith("config_"))
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
        
        status = "🟢 Активен" if config.is_active else "🔴 Отключен"
        
        traffic_text = ""
        if config.public_key:
            traffic_stats = await WireGuardService.get_traffic_stats()
            if config.public_key in traffic_stats:
                stats = traffic_stats[config.public_key]
                received = WireGuardService.format_bytes(stats['received'])
                sent = WireGuardService.format_bytes(stats['sent'])
                total = WireGuardService.format_bytes(stats['received'] + stats['sent'])
                traffic_text = f"\n\n📊 *Трафик:*\n⬇️ Получено: {received}\n⬆️ Отправлено: {sent}\n📈 Всего: {total}"
        
        await callback.message.edit_text(
            f"📱 *Конфиг: {config.name}*\n\n"
            f"Статус: {status}\n"
            f"IP: `{config.client_ip}`\n"
            f"Создан: {config.created_at.strftime('%d.%m.%Y')}"
            f"{traffic_text}",
            parse_mode="Markdown",
            reply_markup=get_config_detail_kb(config.id, config.is_active)
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
        
        tariff_name = TARIFFS.get(active_sub.tariff_type, {}).get("name", active_sub.tariff_type)
        
        if active_sub.expires_at is None:
            status_text = "♾ *Бессрочная подписка*"
            expires_text = "Не ограничена"
        else:
            days_left = (active_sub.expires_at - datetime.utcnow()).days
            status_text = f"✅ *Подписка активна*"
            expires_text = f"{active_sub.expires_at.strftime('%d.%m.%Y')} ({days_left} дн.)"
        
        gift_text = "🎁 Подарочная" if active_sub.is_gift else ""
        
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
            f"{status_text}\n\n"
            f"📋 Тариф: {tariff_name} {gift_text}\n"
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
            await callback.message.edit_text(
                "📢 Для запроса конфига необходимо подписаться на наш канал:",
                parse_mode="Markdown",
                reply_markup=get_check_subscription_kb()
            )
            await state.update_data(after_subscription="extra_config")
            return
    
    async with async_session() as session:
        stmt = select(User).where(
            User.telegram_id == callback.from_user.id
        ).options(selectinload(User.subscriptions))
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
    
    await callback.message.edit_text(
        "📱 *Запрос дополнительного конфига*\n\n"
        "Для какого устройства требуется конфиг?\n"
        "(например: iPhone, MacBook, Windows ПК)",
        parse_mode="Markdown"
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
        
        success, config_data, msg = await WireGuardService.create_config(config_name)
        
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
                name=config_name,
                public_key=config_data.public_key,
                preshared_key=config_data.preshared_key,
                allowed_ips=config_data.allowed_ips,
                client_ip=config_data.client_ip,
                is_active=True
            )
            session.add(new_config)
            await session.commit()
        
        # Отправляем конфиг пользователю (без QR-кода — его можно найти в меню "Конфиги")
        if not LOCAL_MODE:
            config_path = WireGuardService.get_config_file_path(config_name)
            
            if os.path.exists(config_path):
                await bot.send_document(
                    message.from_user.id,
                    FSInputFile(config_path),
                    caption=f"📄 Твой новый конфиг для {device_name}\n\n📷 QR-код можно найти в меню «Конфиги»",
                    parse_mode=None
                )
        
        await message.answer(
            "✅ Конфиг создан!",
            reply_markup=get_main_menu_kb(message.from_user.id, True)
        )


@router.message(F.text)
async def handle_text_message(message: Message, state: FSMContext, bot: Bot):
    """Обработчик текстовых сообщений для AI ассистента"""
    from services.ai_assistant import get_ai_response, UserContext
    
    if not message.text or message.text.startswith('/'):
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
    
    if action == "activate_trial":
        if not context.trial_used:
            # Симулируем нажатие кнопки пробного периода
            await activate_trial_from_ai(message, bot)
        else:
            await message.answer("Пробный период уже был использован. Выбери тариф для продолжения:")
            await message.answer(
                "📋 Выбери тарифный план:",
                reply_markup=get_tariffs_kb(show_trial=False)
            )
    
    elif action == "show_tariffs":
        show_trial = not context.trial_used
        await message.answer(
            "📋 Выбери тарифный план:",
            reply_markup=get_tariffs_kb(show_trial=show_trial)
        )
    
    elif action == "show_configs":
        if context.has_subscription:
            user = await get_user_by_telegram_id(message.from_user.id)
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
                reply_markup=get_tariffs_kb(show_trial=not context.trial_used)
            )
    
    elif action == "create_config":
        if context.has_subscription:
            # Спрашиваем название устройства для дополнительного конфига
            await message.answer(
                "📱 Для какого устройства создать конфиг?\n"
                "(напиши название, например: iPhone, MacBook, Windows ПК)"
            )
            await state.set_state(ConfigRequestStates.waiting_for_device)
        else:
            # Нет подписки — предлагаем trial или тарифы
            if not context.trial_used:
                await message.answer("Сначала нужна подписка. Хочешь попробовать 7 дней бесплатно?")
                await activate_trial_from_ai(message, bot)
            else:
                await message.answer(
                    "Для создания конфига нужна активная подписка. Выбери тариф:",
                    reply_markup=get_tariffs_kb(show_trial=False)
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
        
        # Создаём подписку на 7 дней
        trial_sub = Subscription(
            user_id=db_user.id,
            tariff_type="trial",
            days_total=7,
            expires_at=datetime.utcnow() + timedelta(days=7)
        )
        session.add(trial_sub)
        await session.commit()
    
    # Создаём конфиг
    username = message.from_user.username or f"user{message.from_user.id}"
    config_name = username
    
    success, config_data, error_msg = await WireGuardService.create_config(config_name)
    
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
    if not LOCAL_MODE:
        config_path = WireGuardService.get_config_file_path(config_name)
        if os.path.exists(config_path):
            await bot.send_document(
                message.from_user.id,
                FSInputFile(config_path),
                caption="🎉 Пробный период активирован! Вот твой конфиг на 7 дней.\n\n"
                        "Скачай WireGuard и импортируй этот файл."
            )
    else:
        await message.answer(
            "🎉 Пробный период активирован на 7 дней!\n"
            "[LOCAL_MODE] Конфиг будет отправлен на сервере."
        )
