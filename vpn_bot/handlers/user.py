import os
import logging
from typing import Optional
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import TARIFFS, PAYMENT_PHONE, ADMIN_ID, CLIENT_DIR, LOCAL_MODE
from database import async_session, User, Config, Subscription, Payment
from keyboards.user_kb import (
    get_main_menu_kb, get_tariffs_kb, get_payment_kb, 
    get_back_kb, get_configs_kb, get_config_detail_kb
)
from states.user_states import PaymentStates, RegistrationStates, ConfigRequestStates
from services.wireguard import WireGuardService
from services.ocr import OCRService
from services.settings import is_password_required, is_channel_required, get_bot_password
from keyboards.admin_kb import get_payment_review_kb, get_config_request_kb, get_check_subscription_kb

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
        "🌐 Простой незаметный турецкий VPN со встроенной блокировкой рекламы.\n\n"
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
        
        msg = await message.answer(
            f"👋 Привет, *{message.from_user.first_name}*!\n\n"
            "Это бот для получения VPN-конфигов.\n\n"
            "📱 Пожалуйста, поделитесь номером телефона для связи:\n"
            "(или нажмите 'Пропустить')",
            parse_mode="Markdown",
            reply_markup=get_phone_keyboard()
        )
        await save_bot_message(state, msg.message_id)
        await state.set_state(RegistrationStates.waiting_for_phone)
        return
    
    has_sub = await check_has_subscription(message.from_user.id)
    msg = await message.answer(
        f"👋 С возвращением, *{message.from_user.first_name}*!\n\n"
        "� Немного свободного интернета?",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(message.from_user.id, has_sub)
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
    
    msg = await message.answer(
        "✅ Пароль принят!\n\n"
        "📱 Пожалуйста, поделитесь номером телефона для связи:\n"
        "(или нажмите 'Пропустить')",
        parse_mode="Markdown",
        reply_markup=get_phone_keyboard()
    )
    await save_bot_message(state, msg.message_id)
    await state.set_state(RegistrationStates.waiting_for_phone)


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    is_subscribed = await check_channel_subscription(bot, callback.from_user.id)
    
    if not is_subscribed:
        await callback.answer("❌ Вы не подписаны на канал!", show_alert=True)
        return
    
    data = await state.get_data()
    after_subscription = data.get("after_subscription")
    
    if after_subscription == "registration":
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
    elif after_subscription == "extend":
        await state.clear()
        await callback.message.edit_text(
            "✅ Подписка подтверждена!\n\n"
            "💳 *Продление подписки*\n\n"
            "Выберите тариф для продления.\n"
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
            "� Немного свободного интернета?",
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
        "� Немного свободного интернета?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    # Удаляем это сообщение и отправляем с inline-кнопками
    await bot.delete_message(message.chat.id, msg.message_id)
    msg2 = await message.answer(
        "� Немного свободного интернета?",
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
        "� Немного свободного интернета?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    # Удаляем это сообщение и отправляем с inline-кнопками
    await bot.delete_message(message.chat.id, msg.message_id)
    msg2 = await message.answer(
        "� Немного свободного интернета?",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(message.from_user.id, False)
    )
    await state.clear()
    await save_bot_message(state, msg2.message_id)


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    has_sub = await check_has_subscription(callback.from_user.id)
    await callback.message.edit_text(
        "� Немного свободного интернета?",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(callback.from_user.id, has_sub)
    )


@router.callback_query(F.data == "get_vpn")
async def get_vpn(callback: CallbackQuery):
    user = await get_user_by_telegram_id(callback.from_user.id)
    show_trial = not user.trial_used if user else True
    
    await callback.message.edit_text(
        "📋 *Выберите тарифный план:*\n\n"
        "🎁 Пробный — 7 дней бесплатно (один раз)\n"
        "📅 1 месяц — 100₽\n"
        "📅 3 месяца — 200₽\n"
        "📅 6 месяцев — 300₽",
        parse_mode="Markdown",
        reply_markup=get_tariffs_kb(show_trial=show_trial)
    )


@router.callback_query(F.data == "extend_subscription")
async def extend_subscription(callback: CallbackQuery, state: FSMContext, bot: Bot):
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
        "Выберите тариф для продления.\n"
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
        
        config_name = user.username if user.username else str(callback.from_user.id)
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
            "Сейчас отправлю вам конфиг и QR-код.",
            parse_mode="Markdown"
        )
        
        if not LOCAL_MODE:
            config_path = WireGuardService.get_config_file_path(config_name)
            qr_path = WireGuardService.get_qr_file_path(config_name)
            
            if os.path.exists(config_path):
                await bot.send_document(
                    callback.from_user.id,
                    FSInputFile(config_path),
                    caption="📄 Ваш WireGuard конфиг"
                )
            
            if os.path.exists(qr_path):
                await bot.send_photo(
                    callback.from_user.id,
                    FSInputFile(qr_path),
                    caption="📷 QR-код для быстрой настройки"
                )
        else:
            await bot.send_message(
                callback.from_user.id,
                "🔧 [LOCAL_MODE] Конфиг и QR-код будут отправлены на сервере"
            )
        
        await bot.send_message(
            callback.from_user.id,
            "🏠 *Главное меню*",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(callback.from_user.id, True)
        )


@router.callback_query(F.data.startswith("tariff_"))
async def tariff_selected(callback: CallbackQuery, state: FSMContext):
    tariff_key = callback.data.replace("tariff_", "")
    
    if tariff_key not in TARIFFS:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return
    
    tariff = TARIFFS[tariff_key]
    
    if tariff["price"] == 0:
        await callback.answer("Этот тариф недоступен для покупки", show_alert=True)
        return
    
    await state.update_data(selected_tariff=tariff_key)
    
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
    await state.set_state(PaymentStates.waiting_for_receipt)
    await callback.message.edit_text(
        "📸 *Отправьте фото чека об оплате*\n\n"
        "Просто отправьте фотографию в этот чат.",
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "cancel_payment")
async def cancel_payment(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    has_sub = await check_has_subscription(callback.from_user.id)
    await callback.message.edit_text(
        "❌ Оплата отменена\n\n"
        "� Немного свободного интернета?",
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
            config_name = user_username if user_username else str(user_telegram_id)
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
            qr_path = WireGuardService.get_qr_file_path(config_name)
            
            if os.path.exists(config_path):
                await bot.send_document(
                    user_telegram_id,
                    FSInputFile(config_path),
                    caption="📄 Ваш WireGuard конфиг"
                )
            
            if os.path.exists(qr_path):
                await bot.send_photo(
                    user_telegram_id,
                    FSInputFile(qr_path),
                    caption="📷 QR-код для быстрой настройки"
                )
        
        await message.answer(
            "🏠 *Главное меню*",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(user_telegram_id, True)
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
            "Ваш платёж отправлен на проверку администратору.\n"
            "Вы получите уведомление после подтверждения.",
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


@router.callback_query(F.data == "my_configs")
async def my_configs(callback: CallbackQuery):
    async with async_session() as session:
        stmt = select(User).where(
            User.telegram_id == callback.from_user.id
        ).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user or not user.configs:
            await callback.message.edit_text(
                "📭 У вас пока нет конфигов.\n\n"
                "Получите VPN, чтобы создать первый конфиг.",
                reply_markup=get_back_kb()
            )
            return
        
        await callback.message.edit_text(
            f"📱 *Ваши конфиги ({len(user.configs)}):*\n\n"
            "🟢 — активен\n"
            "🔴 — отключен",
            parse_mode="Markdown",
            reply_markup=get_configs_kb(user.configs)
        )


@router.callback_query(F.data.startswith("config_"))
async def config_detail(callback: CallbackQuery):
    config_id = int(callback.data.replace("config_", ""))
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config or config.user.telegram_id != callback.from_user.id:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        status = "🟢 Активен" if config.is_active else "🔴 Отключен"
        
        await callback.message.edit_text(
            f"📱 *Конфиг: {config.name}*\n\n"
            f"Статус: {status}\n"
            f"IP: `{config.client_ip}`\n"
            f"Создан: {config.created_at.strftime('%d.%m.%Y')}",
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
                caption=f"📄 Конфиг: {config.name}"
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
                "📭 У вас нет активной подписки.\n\n"
                "Получите VPN, чтобы активировать подписку.",
                reply_markup=get_back_kb()
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
                "Продлите подписку для возобновления доступа.",
                parse_mode="Markdown",
                reply_markup=get_back_kb()
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
        
        await callback.message.edit_text(
            f"{status_text}\n\n"
            f"📋 Тариф: {tariff_name} {gift_text}\n"
            f"📅 Действует до: {expires_text}\n"
            f"📱 Конфигов: {len(user.configs)}",
            parse_mode="Markdown",
            reply_markup=get_back_kb()
        )


@router.callback_query(F.data == "request_extra_config")
async def request_extra_config(callback: CallbackQuery, state: FSMContext, bot: Bot):
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
    
    await state.clear()
    
    user_info = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
    phone_info = f"📞 Телефон: `{user_phone}`" if user_phone and user_phone != "5553535" else "📞 Телефон: не указан"
    configs_info = ", ".join(config_names) if config_names else "нет"
    
    await message.answer(
        "✅ *Запрос отправлен!*\n\n"
        "Администратор рассмотрит вашу заявку и создаст конфиг.",
        parse_mode="Markdown",
        reply_markup=get_main_menu_kb(message.from_user.id, True)
    )
    
    # Экранируем специальные символы Markdown в названии устройства
    import re
    safe_device_name = re.sub(r'([_*\[\]()~`>#+=|{}.!-])', r'\\\1', device_name)
    
    await bot.send_message(
        ADMIN_ID,
        f"📱 *Запрос дополнительного конфига*\n\n"
        f"👤 Пользователь: {user_info}\n"
        f"🆔 ID: `{message.from_user.id}`\n"
        f"{phone_info}\n"
        f"📱 Текущие конфиги ({config_count}): {configs_info}\n\n"
        f"🖥 Устройство: *{safe_device_name}*",
        parse_mode="Markdown",
        reply_markup=get_config_request_kb(user_id)
    )
