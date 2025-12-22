import os
import logging
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from config import TARIFFS, ADMIN_ID, LOCAL_MODE
from database import async_session, User, Config, Subscription, Payment
from keyboards.admin_kb import (
    get_admin_menu_kb, get_users_list_kb, get_user_detail_kb,
    get_payment_review_kb, get_pending_payments_kb, get_confirm_delete_kb,
    get_user_configs_kb, get_admin_config_kb, get_settings_kb,
    get_password_settings_kb, get_channel_settings_kb, get_monitoring_settings_kb
)
from keyboards.user_kb import get_main_menu_kb
from services.wireguard import WireGuardService
from services.settings import get_setting, set_setting
from states.user_states import AdminStates

logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к админ-панели")
        return
    
    async with async_session() as session:
        stmt = select(func.count()).select_from(Payment).where(Payment.status == "pending")
        result = await session.execute(stmt)
        pending_count = result.scalar()
    
    await message.answer(
        "🔧 *Админ-панель*\n\nВыберите действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_menu_kb(pending_count)
    )


@router.callback_query(F.data == "admin_menu")
async def admin_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    async with async_session() as session:
        stmt = select(func.count()).select_from(Payment).where(Payment.status == "pending")
        result = await session.execute(stmt)
        pending_count = result.scalar()
    
    await callback.message.edit_text(
        "🔧 *Админ-панель*\n\nВыберите действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_menu_kb(pending_count)
    )


@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    async with async_session() as session:
        stmt = select(User).order_by(User.created_at.desc())
        result = await session.execute(stmt)
        users = result.scalars().all()
    
    if not users:
        await callback.message.edit_text(
            "📭 Пользователей пока нет",
            reply_markup=get_admin_menu_kb()
        )
        return
    
    await callback.message.edit_text(
        f"👥 *Пользователи ({len(users)}):*",
        parse_mode="Markdown",
        reply_markup=get_users_list_kb(users)
    )


@router.callback_query(F.data.startswith("admin_users_page_"))
async def admin_users_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    page = int(callback.data.replace("admin_users_page_", ""))
    
    async with async_session() as session:
        stmt = select(User).order_by(User.created_at.desc())
        result = await session.execute(stmt)
        users = result.scalars().all()
    
    await callback.message.edit_reply_markup(
        reply_markup=get_users_list_kb(users, page)
    )


@router.callback_query(F.data.startswith("admin_user_") & ~F.data.contains("configs") & ~F.data.contains("payments"))
async def admin_user_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("admin_user_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(
            selectinload(User.configs),
            selectinload(User.subscriptions),
            selectinload(User.payments)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
    
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    active_sub = None
    for sub in user.subscriptions:
        if sub.expires_at is None:
            active_sub = sub
            break
        if sub.expires_at > datetime.utcnow():
            if active_sub is None or sub.expires_at > active_sub.expires_at:
                active_sub = sub
    
    if active_sub:
        if active_sub.expires_at is None:
            sub_status = "♾ Бессрочная"
        else:
            days_left = (active_sub.expires_at - datetime.utcnow()).days
            sub_status = f"✅ Активна ({days_left} дн.)"
    else:
        sub_status = "❌ Истекла/Нет"
    
    traffic_info = ""
    if not LOCAL_MODE and user.configs:
        traffic_stats = await WireGuardService.get_traffic_stats()
        for config in user.configs:
            if config.public_key in traffic_stats:
                stats = traffic_stats[config.public_key]
                rx = WireGuardService.format_bytes(stats['received'])
                tx = WireGuardService.format_bytes(stats['sent'])
                traffic_info += f"\n📊 {config.name}: ⬇️{rx} ⬆️{tx}"
    
    username = f"@{user.username}" if user.username else "—"
    
    await callback.message.edit_text(
        f"👤 *Пользователь #{user.id}*\n\n"
        f"🆔 Telegram ID: `{user.telegram_id}`\n"
        f"👤 Username: {username}\n"
        f"📝 Имя: {user.full_name}\n"
        f"📅 Регистрация: {user.created_at.strftime('%d.%m.%Y')}\n"
        f"🎁 Пробный: {'Использован' if user.trial_used else 'Доступен'}\n\n"
        f"📋 Подписка: {sub_status}\n"
        f"📱 Конфигов: {len(user.configs)}\n"
        f"💰 Платежей: {len(user.payments)}"
        f"{traffic_info}",
        parse_mode="Markdown",
        reply_markup=get_user_detail_kb(user.id)
    )


@router.callback_query(F.data.startswith("admin_user_configs_"))
async def admin_user_configs(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("admin_user_configs_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
    
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    if not user.configs:
        await callback.answer("У пользователя нет конфигов", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"📱 *Конфиги пользователя #{user.id}:*",
        parse_mode="Markdown",
        reply_markup=get_user_configs_kb(user.configs, user.id)
    )


@router.callback_query(F.data.startswith("admin_config_"))
async def admin_config_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    config_id = int(callback.data.replace("admin_config_", ""))
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
    
    if not config:
        await callback.answer("Конфиг не найден", show_alert=True)
        return
    
    status = "🟢 Активен" if config.is_active else "🔴 Отключен"
    
    traffic_info = ""
    if not LOCAL_MODE:
        traffic_stats = await WireGuardService.get_traffic_stats()
        if config.public_key in traffic_stats:
            stats = traffic_stats[config.public_key]
            rx = WireGuardService.format_bytes(stats['received'])
            tx = WireGuardService.format_bytes(stats['sent'])
            traffic_info = f"\n📊 Трафик: ⬇️{rx} ⬆️{tx}"
    
    await callback.message.edit_text(
        f"📱 *Конфиг: {config.name}*\n\n"
        f"Статус: {status}\n"
        f"IP: `{config.client_ip}`\n"
        f"Создан: {config.created_at.strftime('%d.%m.%Y')}"
        f"{traffic_info}",
        parse_mode="Markdown",
        reply_markup=get_admin_config_kb(config.id, config.user_id, config.is_active)
    )


@router.callback_query(F.data.startswith("admin_toggle_config_"))
async def admin_toggle_config(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    config_id = int(callback.data.replace("admin_toggle_config_", ""))
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        if config.is_active:
            success, msg = await WireGuardService.disable_config(config.public_key)
            if success:
                config.is_active = False
                await session.commit()
                await callback.answer("🔴 Конфиг отключен")
            else:
                await callback.answer(f"Ошибка: {msg}", show_alert=True)
                return
        else:
            success, msg = await WireGuardService.enable_config(
                config.public_key, config.preshared_key, config.allowed_ips
            )
            if success:
                config.is_active = True
                await session.commit()
                await callback.answer("🟢 Конфиг включен")
            else:
                await callback.answer(f"Ошибка: {msg}", show_alert=True)
                return
        
        status = "🟢 Активен" if config.is_active else "🔴 Отключен"
        await callback.message.edit_text(
            f"📱 *Конфиг: {config.name}*\n\n"
            f"Статус: {status}\n"
            f"IP: `{config.client_ip}`\n"
            f"Создан: {config.created_at.strftime('%d.%m.%Y')}",
            parse_mode="Markdown",
            reply_markup=get_admin_config_kb(config.id, config.user_id, config.is_active)
        )


@router.callback_query(F.data.startswith("admin_delete_config_"))
async def admin_delete_config(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    config_id = int(callback.data.replace("admin_delete_config_", ""))
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        user_id = config.user_id
        config_name = config.name
        
        success, msg = await WireGuardService.delete_config(config_name)
        
        await session.delete(config)
        await session.commit()
        
        await callback.answer("🗑 Конфиг удален")
        
        stmt = select(User).where(User.id == user_id).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user and user.configs:
            await callback.message.edit_text(
                f"📱 *Конфиги пользователя #{user.id}:*",
                parse_mode="Markdown",
                reply_markup=get_user_configs_kb(user.configs, user.id)
            )
        else:
            await callback.message.edit_text(
                "📭 У пользователя больше нет конфигов",
                reply_markup=get_user_detail_kb(user_id)
            )


@router.callback_query(F.data.startswith("admin_user_payments_"))
async def admin_user_payments(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("admin_user_payments_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.payments))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
    
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    if not user.payments:
        await callback.answer("У пользователя нет платежей", show_alert=True)
        return
    
    payments_text = ""
    for p in sorted(user.payments, key=lambda x: x.created_at, reverse=True)[:10]:
        status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(p.status, "❓")
        tariff_name = TARIFFS.get(p.tariff_type, {}).get("name", p.tariff_type)
        payments_text += f"\n{status_emoji} {p.created_at.strftime('%d.%m')} — {tariff_name} ({p.amount}₽)"
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user_{user_id}")]
    ])
    
    await callback.message.edit_text(
        f"💰 *История платежей пользователя #{user.id}:*\n{payments_text}",
        parse_mode="Markdown",
        reply_markup=kb
    )


@router.callback_query(F.data == "admin_pending_payments")
async def admin_pending_payments(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    async with async_session() as session:
        stmt = select(Payment).where(Payment.status == "pending").options(
            selectinload(Payment.user)
        ).order_by(Payment.created_at.desc())
        result = await session.execute(stmt)
        payments = result.scalars().all()
    
    try:
        await callback.message.delete()
    except:
        pass
    
    if not payments:
        await bot.send_message(
            callback.from_user.id,
            "✅ Нет платежей, ожидающих проверки",
            reply_markup=get_admin_menu_kb()
        )
        return
    
    await bot.send_message(
        callback.from_user.id,
        f"💰 *Ожидают проверки ({len(payments)}):*",
        parse_mode="Markdown",
        reply_markup=get_pending_payments_kb(payments)
    )


@router.callback_query(F.data.startswith("admin_payment_"))
async def admin_payment_detail(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    payment_id = int(callback.data.replace("admin_payment_", ""))
    
    async with async_session() as session:
        stmt = select(Payment).where(Payment.id == payment_id).options(selectinload(Payment.user))
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()
    
    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return
    
    user = payment.user
    tariff = TARIFFS.get(payment.tariff_type, {})
    username = f"@{user.username}" if user.username else user.full_name
    
    ocr_text = ""
    if payment.ocr_result:
        ocr_text = f"\n\n📝 OCR: {payment.ocr_result[:200]}..."
    
    await bot.send_photo(
        callback.from_user.id,
        payment.receipt_file_id,
        caption=(
            f"💰 *Платёж #{payment.id}*\n\n"
            f"👤 Пользователь: {username}\n"
            f"🆔 ID: `{user.telegram_id}`\n"
            f"📋 Тариф: {tariff.get('name', payment.tariff_type)}\n"
            f"💵 Сумма: {payment.amount}₽\n"
            f"📅 Дата: {payment.created_at.strftime('%d.%m.%Y %H:%M')}"
            f"{ocr_text}"
        ),
        parse_mode="Markdown",
        reply_markup=get_payment_review_kb(payment.id)
    )
    
    await callback.answer()


@router.callback_query(F.data.startswith("admin_approve_"))
async def admin_approve_payment(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    payment_id = int(callback.data.replace("admin_approve_", ""))
    
    user_id = None
    user_telegram_id = None
    tariff_type = None
    tariff = {}
    days = 30
    new_expires = None
    has_config = False
    existing_config_ids = []
    active_sub_id = None
    need_new_sub = False
    
    async with async_session() as session:
        stmt = select(Payment).where(Payment.id == payment_id).options(
            selectinload(Payment.user).selectinload(User.subscriptions),
            selectinload(Payment.user).selectinload(User.configs)
        )
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()
        
        if not payment:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        
        if payment.status != "pending":
            await callback.answer("Платёж уже обработан", show_alert=True)
            return
        
        user = payment.user
        user_id = user.id
        user_telegram_id = user.telegram_id
        user_username = user.username
        tariff_type = payment.tariff_type
        tariff = TARIFFS.get(payment.tariff_type, {})
        days = tariff.get("days", 30)
        
        active_sub = None
        for sub in user.subscriptions:
            if sub.expires_at is None:
                active_sub = sub
                break
            if sub.expires_at > datetime.utcnow():
                if active_sub is None or sub.expires_at > active_sub.expires_at:
                    active_sub = sub
        
        if active_sub and active_sub.expires_at:
            new_expires = active_sub.expires_at + timedelta(days=days)
            active_sub_id = active_sub.id
        else:
            new_expires = datetime.utcnow() + timedelta(days=days)
            need_new_sub = True
        
        has_config = len(user.configs) > 0
        for cfg in user.configs:
            if not cfg.is_active:
                existing_config_ids.append((cfg.id, cfg.public_key, cfg.preshared_key, cfg.allowed_ips))
    
    config_created = False
    config_name = None
    config_data = None
    
    if not has_config:
        config_name = user_username if user_username else str(user_telegram_id)
        success, config_data, msg = await WireGuardService.create_config(config_name)
        if success:
            config_created = True
        else:
            logger.error(f"Ошибка создания конфига: {msg}")
    else:
        for cfg_id, pub_key, psk, allowed_ips in existing_config_ids:
            success, msg = await WireGuardService.enable_config(pub_key, psk, allowed_ips)
            if success:
                async with async_session() as session:
                    stmt = select(Config).where(Config.id == cfg_id)
                    result = await session.execute(stmt)
                    cfg = result.scalar_one_or_none()
                    if cfg:
                        cfg.is_active = True
                        await session.commit()
                break
    
    async with async_session() as session:
        stmt = select(Payment).where(Payment.id == payment_id)
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()
        
        if payment:
            payment.status = "approved"
            payment.processed_at = datetime.utcnow()
        
        if active_sub_id:
            stmt_sub = select(Subscription).where(Subscription.id == active_sub_id)
            result_sub = await session.execute(stmt_sub)
            active_sub = result_sub.scalar_one_or_none()
            if active_sub:
                active_sub.expires_at = new_expires
                active_sub.notified_3_days = False
        elif need_new_sub:
            subscription = Subscription(
                user_id=user_id,
                tariff_type=tariff_type,
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
    
    await callback.answer("✅ Платёж подтверждён")
    
    try:
        await callback.message.edit_caption(
            caption=callback.message.caption + "\n\n✅ *ПОДТВЕРЖДЕНО*",
            parse_mode="Markdown"
        )
    except:
        pass
    
    try:
        msg_text = (
            f"✅ *Оплата подтверждена!*\n\n"
            f"📋 Тариф: {tariff.get('name', tariff_type)}\n"
            f"📅 Действует до: {new_expires.strftime('%d.%m.%Y')}\n"
        )
        
        if config_created:
            msg_text += "\nСейчас отправлю вам конфиг."
        
        await bot.send_message(user_telegram_id, msg_text, parse_mode="Markdown")
        
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
        
        await bot.send_message(
            user_telegram_id,
            "🏠 *Главное меню*",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(user_telegram_id, True)
        )
        
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю: {e}")


@router.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_payment(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    payment_id = int(callback.data.replace("admin_reject_", ""))
    
    async with async_session() as session:
        stmt = select(Payment).where(Payment.id == payment_id).options(selectinload(Payment.user))
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()
        
        if not payment:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        
        if payment.status != "pending":
            await callback.answer("Платёж уже обработан", show_alert=True)
            return
        
        payment.status = "rejected"
        payment.processed_at = datetime.utcnow()
        await session.commit()
        
        await callback.answer("❌ Платёж отклонён")
        
        try:
            await callback.message.edit_caption(
                caption=callback.message.caption + "\n\n❌ *ОТКЛОНЕНО*",
                parse_mode="Markdown"
            )
        except:
            pass
        
        try:
            await bot.send_message(
                payment.user.telegram_id,
                "❌ *Платёж отклонён*\n\n"
                "Ваш чек не прошёл проверку.\n"
                "Если вы уверены, что оплата была произведена, свяжитесь с администратором.",
                parse_mode="Markdown",
                reply_markup=get_main_menu_kb(payment.user.telegram_id, False)
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")


@router.callback_query(F.data.startswith("admin_gift_"))
async def admin_gift(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("admin_gift_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        subscription = Subscription(
            user_id=user.id,
            tariff_type="unlimited",
            days_total=0,
            expires_at=None,
            is_gift=True
        )
        session.add(subscription)
        
        config_created = False
        if not user.configs:
            config_name = f"user_{user.id}_gift"
            success, config_data, msg = await WireGuardService.create_config(config_name)
            
            if success:
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
                config_created = True
        else:
            for cfg in user.configs:
                if not cfg.is_active:
                    success, msg = await WireGuardService.enable_config(
                        cfg.public_key, cfg.preshared_key, cfg.allowed_ips
                    )
                    if success:
                        cfg.is_active = True
        
        await session.commit()
        
        await callback.answer("🎁 Бессрочный тариф выдан!")
        
        try:
            msg_text = (
                "🎁 *Вам подарен бессрочный VPN!*\n\n"
                "Ваша подписка теперь не имеет срока действия.\n"
            )
            
            if config_created:
                msg_text += "\nСейчас отправлю вам конфиг."
            
            await bot.send_message(user.telegram_id, msg_text, parse_mode="Markdown")
            
            if config_created and not LOCAL_MODE:
                config_path = WireGuardService.get_config_file_path(config_name)
                qr_path = WireGuardService.get_qr_file_path(config_name)
                
                if os.path.exists(config_path):
                    await bot.send_document(
                        user.telegram_id,
                        FSInputFile(config_path),
                        caption="📄 Ваш WireGuard конфиг"
                    )
                
                if os.path.exists(qr_path):
                    await bot.send_photo(
                        user.telegram_id,
                        FSInputFile(qr_path),
                        caption="📷 QR-код для быстрой настройки"
                    )
            
            await bot.send_message(
                user.telegram_id,
                "🏠 *Главное меню*",
                parse_mode="Markdown",
                reply_markup=get_main_menu_kb(user.telegram_id, True)
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")


@router.callback_query(F.data.startswith("admin_add_config_"))
async def admin_add_config(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("admin_add_config_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        config_num = len(user.configs) + 1
        config_name = f"user_{user.id}_{config_num}"
        
        success, config_data, msg = await WireGuardService.create_config(config_name)
        
        if not success:
            await callback.answer(f"Ошибка: {msg}", show_alert=True)
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
        await session.commit()
        
        await callback.answer("✅ Конфиг создан!")
        
        try:
            await bot.send_message(
                user.telegram_id,
                f"📱 *Вам добавлен новый конфиг!*\n\n"
                f"Имя: `{config_name}`",
                parse_mode="Markdown"
            )
            
            if not LOCAL_MODE:
                config_path = WireGuardService.get_config_file_path(config_name)
                qr_path = WireGuardService.get_qr_file_path(config_name)
                
                if os.path.exists(config_path):
                    await bot.send_document(
                        user.telegram_id,
                        FSInputFile(config_path),
                        caption="📄 Ваш WireGuard конфиг"
                    )
                
                if os.path.exists(qr_path):
                    await bot.send_photo(
                        user.telegram_id,
                        FSInputFile(qr_path),
                        caption="📷 QR-код для быстрой настройки"
                    )
                    
        except Exception as e:
            logger.error(f"Ошибка отправки конфига пользователю: {e}")


@router.callback_query(F.data.startswith("admin_delete_user_"))
async def admin_delete_user_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("admin_delete_user_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
    
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    username = f"@{user.username}" if user.username else user.full_name
    
    await callback.message.edit_text(
        f"⚠️ *Подтвердите удаление*\n\n"
        f"Пользователь: {username}\n"
        f"ID: `{user.telegram_id}`\n\n"
        f"Будут удалены:\n"
        f"• Все конфиги\n"
        f"• Все подписки\n"
        f"• История платежей\n\n"
        f"Это действие нельзя отменить!",
        parse_mode="Markdown",
        reply_markup=get_confirm_delete_kb(user.id)
    )


@router.callback_query(F.data.startswith("admin_confirm_delete_"))
async def admin_confirm_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("admin_confirm_delete_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        for config in user.configs:
            await WireGuardService.delete_config(config.name)
        
        await session.delete(user)
        await session.commit()
        
        await callback.answer("🗑 Пользователь удалён")
        
        stmt = select(User).order_by(User.created_at.desc())
        result = await session.execute(stmt)
        users = result.scalars().all()
        
        await callback.message.edit_text(
            f"👥 *Пользователи ({len(users)}):*",
            parse_mode="Markdown",
            reply_markup=get_users_list_kb(users)
        )


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    async with async_session() as session:
        users_count = await session.scalar(select(func.count()).select_from(User))
        configs_count = await session.scalar(select(func.count()).select_from(Config))
        active_configs = await session.scalar(
            select(func.count()).select_from(Config).where(Config.is_active == True)
        )
        
        active_subs = await session.scalar(
            select(func.count()).select_from(Subscription).where(
                (Subscription.expires_at.is_(None)) | 
                (Subscription.expires_at > datetime.utcnow())
            )
        )
        
        total_payments = await session.scalar(
            select(func.sum(Payment.amount)).where(Payment.status == "approved")
        ) or 0
        
        pending_payments = await session.scalar(
            select(func.count()).select_from(Payment).where(Payment.status == "pending")
        )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")]
    ])
    
    await callback.message.edit_text(
        f"📊 *Статистика сервиса*\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"📱 Конфигов: {configs_count} (активных: {active_configs})\n"
        f"✅ Активных подписок: {active_subs}\n"
        f"💰 Всего оплачено: {total_payments}₽\n"
        f"⏳ Ожидают проверки: {pending_payments}",
        parse_mode="Markdown",
        reply_markup=kb
    )


@router.message(Command("gift"))
async def cmd_gift(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование:\n"
            "`/gift @username` или `/gift telegram_id`",
            parse_mode="Markdown"
        )
        return
    
    target = args[1].strip()
    
    async with async_session() as session:
        if target.startswith("@"):
            username = target[1:]
            stmt = select(User).where(User.username == username)
        else:
            try:
                telegram_id = int(target)
                stmt = select(User).where(User.telegram_id == telegram_id)
            except ValueError:
                await message.answer("❌ Неверный формат. Укажите @username или telegram_id")
                return
        
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await message.answer("❌ Пользователь не найден в базе")
            return
        
        subscription = Subscription(
            user_id=user.id,
            tariff_type="unlimited",
            days_total=0,
            expires_at=None,
            is_gift=True
        )
        session.add(subscription)
        
        stmt_configs = select(Config).where(Config.user_id == user.id)
        result_configs = await session.execute(stmt_configs)
        configs = result_configs.scalars().all()
        
        config_created = False
        if not configs:
            config_name = f"user_{user.id}_gift"
            success, config_data, msg = await WireGuardService.create_config(config_name)
            
            if success:
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
                config_created = True
        else:
            for cfg in configs:
                if not cfg.is_active:
                    success, msg = await WireGuardService.enable_config(
                        cfg.public_key, cfg.preshared_key, cfg.allowed_ips
                    )
                    if success:
                        cfg.is_active = True
        
        await session.commit()
        
        username_display = f"@{user.username}" if user.username else user.full_name
        await message.answer(f"🎁 Бессрочный тариф выдан пользователю {username_display}")
        
        try:
            msg_text = (
                "🎁 *Вам подарен бессрочный VPN!*\n\n"
                "Ваша подписка теперь не имеет срока действия.\n"
            )
            
            if config_created:
                msg_text += "\nСейчас отправлю вам конфиг."
            
            await bot.send_message(user.telegram_id, msg_text, parse_mode="Markdown")
            
            if config_created and not LOCAL_MODE:
                config_path = WireGuardService.get_config_file_path(config_name)
                qr_path = WireGuardService.get_qr_file_path(config_name)
                
                if os.path.exists(config_path):
                    await bot.send_document(
                        user.telegram_id,
                        FSInputFile(config_path),
                        caption="📄 Ваш WireGuard конфиг"
                    )
                
                if os.path.exists(qr_path):
                    await bot.send_photo(
                        user.telegram_id,
                        FSInputFile(qr_path),
                        caption="📷 QR-код для быстрой настройки"
                    )
            
            await bot.send_message(
                user.telegram_id,
                "🏠 *Главное меню*",
                parse_mode="Markdown",
                reply_markup=get_main_menu_kb(user.telegram_id, True)
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")
            await message.answer(f"⚠️ Не удалось отправить уведомление пользователю: {e}")


@router.callback_query(F.data.startswith("cfgreq_ok_"))
async def admin_approve_config_request(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("cfgreq_ok_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        user_telegram_id = user.telegram_id
        user_username = user.username
        config_count = len(user.configs)
    
    # Извлекаем название устройства из сообщения
    import re
    device_match = re.search(r'🖥 Устройство: \*(.+?)\*', callback.message.text)
    device_name = device_match.group(1) if device_match else None
    
    # Формируем имя конфига: username_device или username_номер
    base_name = user_username if user_username else str(user_telegram_id)
    if device_name:
        # Очищаем название устройства от спецсимволов для имени файла
        clean_device = re.sub(r'[^\w\s-]', '', device_name).strip().replace(' ', '_')[:20]
        config_name = f"{base_name}_{clean_device}"
    else:
        config_name = f"{base_name}_{config_count + 1}"
    
    success, config_data, msg = await WireGuardService.create_config(config_name)
    
    if not success:
        await callback.answer(f"Ошибка создания конфига: {msg}", show_alert=True)
        return
    
    async with async_session() as session:
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
    
    await callback.answer("✅ Конфиг создан")
    
    # Актуализируем информацию о конфигах
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user_updated = result.scalar_one_or_none()
        if user_updated:
            updated_config_names = [c.name for c in user_updated.configs]
            updated_configs_info = ", ".join(updated_config_names)
            updated_config_count = len(user_updated.configs)
    
    try:
        # Обновляем сообщение с актуальной информацией о конфигах
        old_text = callback.message.text
        # Заменяем старую информацию о конфигах на новую
        import re
        new_text = re.sub(
            r'📱 Текущие конфиги \(\d+\): .+\n',
            f'📱 Текущие конфиги ({updated_config_count}): {updated_configs_info}\n',
            old_text
        )
        await callback.message.edit_text(
            new_text + "\n\n✅ *ОДОБРЕНО*",
            parse_mode="Markdown"
        )
    except:
        pass
    
    try:
        await bot.send_message(
            user_telegram_id,
            f"✅ *Дополнительный конфиг создан!*\n\n"
            f"Конфиг: `{config_name}`\n"
            f"Сейчас отправлю файлы.",
            parse_mode="Markdown"
        )
        
        if not LOCAL_MODE:
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
        
        await bot.send_message(
            user_telegram_id,
            "🏠 *Главное меню*",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(user_telegram_id, True)
        )
    except Exception as e:
        logger.error(f"Ошибка отправки конфига: {e}")


@router.callback_query(F.data.startswith("cfgreq_no_"))
async def admin_reject_config_request(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("cfgreq_no_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        user_telegram_id = user.telegram_id
    
    await callback.answer("❌ Запрос отклонён")
    
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ *ОТКЛОНЕНО*",
            parse_mode="Markdown"
        )
    except:
        pass
    
    try:
        await bot.send_message(
            user_telegram_id,
            "❌ *Запрос на дополнительный конфиг отклонён*\n\n"
            "Если у вас есть вопросы, свяжитесь с администратором.",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(user_telegram_id, True)
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")


@router.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    password_enabled = await get_setting("password_enabled") == "1"
    channel_required = await get_setting("channel_required") == "1"
    
    password_status = "🟢 Вкл" if password_enabled else "🔴 Выкл"
    channel_status = "🟢 Вкл" if channel_required else "🔴 Выкл"
    
    await callback.message.edit_text(
        f"⚙️ *Настройки бота*\n\n"
        f"🔑 Пароль: {password_status}\n"
        f"📢 Подписка на канал: {channel_status}",
        parse_mode="Markdown",
        reply_markup=get_settings_kb()
    )


@router.callback_query(F.data == "settings_password")
async def settings_password(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    password_enabled = await get_setting("password_enabled") == "1"
    current_password = await get_setting("bot_password")
    
    password_text = f"`{current_password}`" if current_password else "не установлен"
    status = "🟢 Включён" if password_enabled else "🔴 Выключен"
    
    await callback.message.edit_text(
        f"🔑 *Настройки пароля*\n\n"
        f"Статус: {status}\n"
        f"Текущий пароль: {password_text}",
        parse_mode="Markdown",
        reply_markup=get_password_settings_kb(password_enabled)
    )


@router.callback_query(F.data == "settings_password_on")
async def settings_password_on(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    current_password = await get_setting("bot_password")
    if not current_password:
        await callback.answer("❌ Сначала установите пароль!", show_alert=True)
        return
    
    await set_setting("password_enabled", "1")
    await callback.answer("✅ Пароль включён")
    
    await callback.message.edit_text(
        f"🔑 *Настройки пароля*\n\n"
        f"Статус: 🟢 Включён\n"
        f"Текущий пароль: `{current_password}`",
        parse_mode="Markdown",
        reply_markup=get_password_settings_kb(True)
    )


@router.callback_query(F.data == "settings_password_off")
async def settings_password_off(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("password_enabled", "0")
    await callback.answer("✅ Пароль выключен")
    
    current_password = await get_setting("bot_password")
    password_text = f"`{current_password}`" if current_password else "не установлен"
    
    await callback.message.edit_text(
        f"🔑 *Настройки пароля*\n\n"
        f"Статус: 🔴 Выключен\n"
        f"Текущий пароль: {password_text}",
        parse_mode="Markdown",
        reply_markup=get_password_settings_kb(False)
    )


@router.callback_query(F.data == "settings_password_change")
async def settings_password_change(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.message.edit_text(
        "🔑 *Изменение пароля*\n\n"
        "Введите новый пароль:",
        parse_mode="Markdown"
    )
    await state.set_state(AdminStates.waiting_for_new_password)


@router.message(AdminStates.waiting_for_new_password)
async def process_new_password(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    new_password = message.text.strip()
    
    if len(new_password) < 3:
        await message.answer("❌ Пароль слишком короткий (минимум 3 символа)")
        return
    
    await set_setting("bot_password", new_password)
    await state.clear()
    
    await message.answer(
        f"✅ Пароль изменён на: `{new_password}`",
        parse_mode="Markdown",
        reply_markup=get_settings_kb()
    )


@router.callback_query(F.data == "settings_channel")
async def settings_channel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    channel_required = await get_setting("channel_required") == "1"
    status = "🟢 Включена" if channel_required else "🔴 Выключена"
    
    await callback.message.edit_text(
        f"📢 *Подписка на канал*\n\n"
        f"Статус: {status}\n"
        f"Канал: @agdevpn",
        parse_mode="Markdown",
        reply_markup=get_channel_settings_kb(channel_required)
    )


@router.callback_query(F.data == "settings_channel_on")
async def settings_channel_on(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("channel_required", "1")
    await callback.answer("✅ Подписка на канал включена")
    
    await callback.message.edit_text(
        f"📢 *Подписка на канал*\n\n"
        f"Статус: 🟢 Включена\n"
        f"Канал: @agdevpn",
        parse_mode="Markdown",
        reply_markup=get_channel_settings_kb(True)
    )


@router.callback_query(F.data == "settings_channel_off")
async def settings_channel_off(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("channel_required", "0")
    await callback.answer("✅ Подписка на канал выключена")
    
    await callback.message.edit_text(
        f"📢 *Подписка на канал*\n\n"
        f"Статус: 🔴 Выключена\n"
        f"Канал: @agdevpn",
        parse_mode="Markdown",
        reply_markup=get_channel_settings_kb(False)
    )


@router.callback_query(F.data == "settings_monitoring")
async def settings_monitoring(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    monitoring_enabled = await get_setting("monitoring_enabled") != "0"
    traffic_threshold = await get_setting("monitoring_traffic_gb") or "50"
    configs_threshold = await get_setting("monitoring_configs") or "3"
    
    status = "🟢 Включён" if monitoring_enabled else "🔴 Выключен"
    
    await callback.message.edit_text(
        f"📊 *Настройки мониторинга*\n\n"
        f"Статус: {status}\n"
        f"Порог трафика: *{traffic_threshold} GB*\n"
        f"Порог конфигов: *{configs_threshold}*\n\n"
        f"_Мониторинг проверяет подозрительную активность каждые 6 часов_",
        parse_mode="Markdown",
        reply_markup=get_monitoring_settings_kb(monitoring_enabled)
    )


@router.callback_query(F.data == "settings_monitoring_on")
async def settings_monitoring_on(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("monitoring_enabled", "1")
    await callback.answer("✅ Мониторинг включён")
    
    traffic_threshold = await get_setting("monitoring_traffic_gb") or "50"
    configs_threshold = await get_setting("monitoring_configs") or "3"
    
    await callback.message.edit_text(
        f"📊 *Настройки мониторинга*\n\n"
        f"Статус: 🟢 Включён\n"
        f"Порог трафика: *{traffic_threshold} GB*\n"
        f"Порог конфигов: *{configs_threshold}*\n\n"
        f"_Мониторинг проверяет подозрительную активность каждые 6 часов_",
        parse_mode="Markdown",
        reply_markup=get_monitoring_settings_kb(True)
    )


@router.callback_query(F.data == "settings_monitoring_off")
async def settings_monitoring_off(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("monitoring_enabled", "0")
    await callback.answer("✅ Мониторинг выключен")
    
    traffic_threshold = await get_setting("monitoring_traffic_gb") or "50"
    configs_threshold = await get_setting("monitoring_configs") or "3"
    
    await callback.message.edit_text(
        f"📊 *Настройки мониторинга*\n\n"
        f"Статус: 🔴 Выключен\n"
        f"Порог трафика: *{traffic_threshold} GB*\n"
        f"Порог конфигов: *{configs_threshold}*\n\n"
        f"_Мониторинг проверяет подозрительную активность каждые 6 часов_",
        parse_mode="Markdown",
        reply_markup=get_monitoring_settings_kb(False)
    )


@router.callback_query(F.data == "settings_monitoring_traffic")
async def settings_monitoring_traffic(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    current = await get_setting("monitoring_traffic_gb") or "50"
    
    await callback.message.edit_text(
        f"📊 *Порог трафика*\n\n"
        f"Текущее значение: *{current} GB*\n\n"
        f"Введите новое значение (в GB):",
        parse_mode="Markdown"
    )
    await state.set_state(AdminStates.waiting_for_traffic_threshold)


@router.message(AdminStates.waiting_for_traffic_threshold)
async def process_traffic_threshold(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    try:
        value = int(message.text.strip())
        if value < 1 or value > 1000:
            await message.answer("❌ Значение должно быть от 1 до 1000 GB")
            return
        
        await set_setting("monitoring_traffic_gb", str(value))
        await state.clear()
        
        monitoring_enabled = await get_setting("monitoring_enabled") != "0"
        configs_threshold = await get_setting("monitoring_configs") or "3"
        
        await message.answer(
            f"✅ Порог трафика изменён на *{value} GB*\n\n"
            f"📊 *Настройки мониторинга*\n\n"
            f"Статус: {'🟢 Включён' if monitoring_enabled else '🔴 Выключен'}\n"
            f"Порог трафика: *{value} GB*\n"
            f"Порог конфигов: *{configs_threshold}*",
            parse_mode="Markdown",
            reply_markup=get_monitoring_settings_kb(monitoring_enabled)
        )
    except ValueError:
        await message.answer("❌ Введите число")


@router.callback_query(F.data == "settings_monitoring_configs")
async def settings_monitoring_configs(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    current = await get_setting("monitoring_configs") or "3"
    
    await callback.message.edit_text(
        f"📱 *Порог конфигов*\n\n"
        f"Текущее значение: *{current}*\n\n"
        f"Введите новое значение:",
        parse_mode="Markdown"
    )
    await state.set_state(AdminStates.waiting_for_configs_threshold)


@router.message(AdminStates.waiting_for_configs_threshold)
async def process_configs_threshold(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    try:
        value = int(message.text.strip())
        if value < 1 or value > 100:
            await message.answer("❌ Значение должно быть от 1 до 100")
            return
        
        await set_setting("monitoring_configs", str(value))
        await state.clear()
        
        monitoring_enabled = await get_setting("monitoring_enabled") != "0"
        traffic_threshold = await get_setting("monitoring_traffic_gb") or "50"
        
        await message.answer(
            f"✅ Порог конфигов изменён на *{value}*\n\n"
            f"📊 *Настройки мониторинга*\n\n"
            f"Статус: {'🟢 Включён' if monitoring_enabled else '🔴 Выключен'}\n"
            f"Порог трафика: *{traffic_threshold} GB*\n"
            f"Порог конфигов: *{value}*",
            parse_mode="Markdown",
            reply_markup=get_monitoring_settings_kb(monitoring_enabled)
        )
    except ValueError:
        await message.answer("❌ Введите число")
