import os
import logging
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload
import logging
import subprocess
from config import TARIFFS, ADMIN_ID, LOCAL_MODE
from database import async_session, User, Config, Subscription, Payment, Server, WithdrawalRequest
from keyboards.admin_kb import (
    get_admin_menu_kb, get_users_list_kb, get_user_detail_kb,
    get_payment_review_kb, get_pending_payments_kb, get_confirm_delete_kb,
    get_user_configs_kb, get_admin_config_kb, get_settings_kb,
    get_password_settings_kb, get_channel_settings_kb, get_monitoring_settings_kb,
    get_phone_settings_kb, get_config_approval_kb, get_broadcast_menu_kb, 
    get_broadcast_cancel_kb, get_broadcast_users_kb, get_gift_menu_kb,
    get_servers_list_kb, get_server_detail_kb, get_server_confirm_delete_kb,
    get_server_migrate_kb, get_migrate_confirm_kb,
    get_server_add_cancel_kb, get_server_install_kb, get_server_edit_kb,
    get_server_edit_cancel_kb, get_max_configs_cancel_kb, get_channel_change_cancel_kb,
    get_user_max_configs_cancel_kb, get_server_clients_kb, get_server_broadcast_cancel_kb,
    get_server_user_detail_kb, get_server_user_configs_kb, get_server_config_detail_kb,
    get_referrals_list_kb, get_referral_detail_kb,
    get_referral_percent_cancel_kb, get_withdrawal_review_kb, get_withdrawals_list_kb,
    get_user_stats_kb, get_inactive_user_kb
)
from database.models import BotSettings
from keyboards.user_kb import get_main_menu_kb
from services.wireguard import WireGuardService
from services.traffic import format_bytes, get_config_traffic, get_server_traffic
from services.wireguard_multi import WireGuardMultiService
from services.settings import get_setting, set_setting
from states.user_states import AdminStates
from utils import transliterate_ru_to_en, format_datetime_moscow, format_date_moscow

logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def create_config_multi_admin(config_name: str) -> tuple:
    """
    Создать конфиг с использованием мультисервера (для админки).
    Возвращает (success, config_data, server_id, error_msg)
    """
    async with async_session() as session:
        servers = await WireGuardMultiService.get_all_servers(session)
        
        if not servers:
            success, config_data, msg = await WireGuardService.create_config(config_name)
            return success, config_data, None, msg
        
        success, config_data, msg = await WireGuardMultiService.create_config(config_name, session)
        
        if success and config_data:
            return True, config_data, config_data.server_id, msg
        return False, None, None, msg


@router.message(Command("uptime"))
async def cmd_uptime(message: Message):
    """Показывает статус серверов"""
    if not is_admin(message.from_user.id):
        return
    
    from services.uptime_monitor import get_monitor
    monitor = get_monitor()
    
    if not monitor:
        await message.answer("❌ Мониторинг не инициализирован")
        return
    
    # Принудительно проверяем все серверы
    await message.answer("⏳ Проверяю серверы...")
    await monitor.check_all_servers()
    
    report = monitor.get_status_report()
    await message.answer(report, parse_mode="Markdown")


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У тебя нет доступа к админ-панели")
        return
    
    from services.config_queue import ConfigQueueService
    
    async with async_session() as session:
        stmt = select(func.count()).select_from(Payment).where(Payment.status == "pending")
        result = await session.execute(stmt)
        pending_count = result.scalar()
        
        stmt_w = select(func.count()).select_from(WithdrawalRequest).where(WithdrawalRequest.status == "pending")
        result_w = await session.execute(stmt_w)
        pending_withdrawals = result_w.scalar()
        
        # Счётчик неактивных пользователей
        stmt_inactive = select(func.count()).select_from(User).where(User.failed_notifications >= 3)
        result_inactive = await session.execute(stmt_inactive)
        inactive_count = result_inactive.scalar()
    
    queue_count = await ConfigQueueService.get_waiting_count()
    
    await message.answer(
        "🔧 *Админ-панель*\n\nВыбери действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_menu_kb(pending_count, pending_withdrawals, queue_count, inactive_count)
    )


@router.callback_query(F.data == "admin_menu")
async def admin_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    from services.config_queue import ConfigQueueService
    
    await callback.answer()
    async with async_session() as session:
        stmt = select(func.count()).select_from(Payment).where(Payment.status == "pending")
        result = await session.execute(stmt)
        pending_count = result.scalar()
        
        stmt_w = select(func.count()).select_from(WithdrawalRequest).where(WithdrawalRequest.status == "pending")
        result_w = await session.execute(stmt_w)
        pending_withdrawals = result_w.scalar()
        
        # Счётчик неактивных пользователей
        stmt_inactive = select(func.count()).select_from(User).where(User.failed_notifications >= 3)
        result_inactive = await session.execute(stmt_inactive)
        inactive_count = result_inactive.scalar()
    
    queue_count = await ConfigQueueService.get_waiting_count()
    
    await callback.message.edit_text(
        "🔧 *Админ-панель*\n\nВыбери действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_menu_kb(pending_count, pending_withdrawals, queue_count, inactive_count)
    )


@router.callback_query(F.data.startswith("admin_user_stats"))
async def admin_user_stats(callback: CallbackQuery):
    """Статистика пользователей: трафик, оплаты, дни до конца, неактивные"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    
    # Получаем номер страницы из callback_data
    page = 0
    if "_page_" in callback.data:
        try:
            page = int(callback.data.split("_page_")[1])
        except:
            page = 0
    
    per_page = 15
    
    async with async_session() as session:
        # Получаем всех пользователей с подписками и платежами
        stmt = select(User).options(
            selectinload(User.subscriptions),
            selectinload(User.payments),
            selectinload(User.configs)
        ).order_by(User.created_at.desc())
        result = await session.execute(stmt)
        users = result.scalars().all()
        
        # Получаем настройку автоудаления
        stmt_setting = select(BotSettings).where(BotSettings.key == "auto_delete_inactive")
        result_setting = await session.execute(stmt_setting)
        setting = result_setting.scalar_one_or_none()
        auto_delete = setting and setting.value == "true"
        
        # Получаем трафик со всех серверов
        all_traffic = {}
        try:
            servers_stmt = select(Server).where(Server.is_active == True)
            servers_result = await session.execute(servers_stmt)
            servers = servers_result.scalars().all()
            
            for server in servers:
                try:
                    server_traffic = await get_server_traffic(server)
                    if server_traffic:
                        all_traffic.update(server_traffic)
                except:
                    pass
            
            # Также локальный сервер
            try:
                local_traffic = await WireGuardService.get_traffic_stats()
                if local_traffic:
                    all_traffic.update(local_traffic)
            except:
                pass
        except:
            pass
    
    # Формируем списки с трафиком
    user_stats = []
    inactive_users = []
    
    for user in users:
        user_info = f"@{user.username}" if user.username else user.full_name[:12]
        
        # Считаем трафик по конфигам пользователя (накопительный + текущий)
        user_traffic = 0
        for config in user.configs:
            # Накопительный трафик из БД
            user_traffic += (config.total_received or 0) + (config.total_sent or 0)
            # Плюс текущий трафик с сервера (если есть и больше накопленного)
            if config.public_key in all_traffic:
                stats = all_traffic[config.public_key]
                current = stats.get('received', 0) + stats.get('sent', 0)
                saved = (config.total_received or 0) + (config.total_sent or 0)
                if current > saved:
                    user_traffic = user_traffic - saved + current
        
        traffic_str = format_bytes(user_traffic) if user_traffic else "0 B"
        
        # Считаем оплаты
        approved_payments = [p for p in user.payments if p.status == "approved"]
        total_paid = sum(p.amount for p in approved_payments)
        
        # Количество конфигов
        configs_count = len(user.configs)
        
        # Дни до конца подписки
        days_left = "∞"
        active_sub = None
        for sub in user.subscriptions:
            if sub.expires_at is None:
                days_left = "∞"
                active_sub = sub
                break
            if sub.expires_at > datetime.utcnow():
                if active_sub is None or sub.expires_at > active_sub.expires_at:
                    active_sub = sub
        
        if active_sub and active_sub.expires_at:
            days = (active_sub.expires_at - datetime.utcnow()).days
            days_left = f"{days}д" if days >= 0 else "0д"
        elif not active_sub:
            days_left = "—"
        
        line = f"{user_info} | {configs_count}📱 | {traffic_str} | {total_paid}₽ | {days_left}"
        
        if user.failed_notifications >= 3:
            inactive_users.append((user_traffic, f"⚠️ {line}"))
        else:
            user_stats.append((user_traffic, f"👤 {line}"))
    
    # Сортируем по трафику (убывание)
    user_stats.sort(key=lambda x: x[0], reverse=True)
    inactive_users.sort(key=lambda x: x[0], reverse=True)
    
    active_lines = [line for _, line in user_stats]
    inactive_lines = [line for _, line in inactive_users]
    
    # Пагинация
    total_pages = (len(active_lines) + per_page - 1) // per_page
    if total_pages == 0:
        total_pages = 1
    start = page * per_page
    end = start + per_page
    page_users = active_lines[start:end]
    
    # Формируем текст (без Markdown чтобы не ломались username с _)
    auto_status = "✅ вкл" if auto_delete else "❌ выкл"
    text = f"📊 Статистика пользователей\n"
    text += f"🗑 Автоудаление неактивных: {auto_status}\n\n"
    text += "Имя | 📱 | Трафик | Оплаты | Подписка\n"
    text += "─" * 32 + "\n"
    
    for line in page_users:
        text += f"{line}\n"
    
    if total_pages > 1:
        text += f"\n📄 Страница {page + 1}/{total_pages}"
    
    if inactive_lines:
        text += f"\n\n⚠️ Неактивные ({len(inactive_lines)}):\n"
        for line in inactive_lines[:3]:
            text += f"{line}\n"
        if len(inactive_lines) > 3:
            text += f"... и ещё {len(inactive_lines) - 3}\n"
    
    text += f"\n📈 Всего: {len(users)} пользователей"
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode=None,
            reply_markup=get_user_stats_kb(auto_delete, page, total_pages)
        )
    except Exception:
        # Сообщение не изменилось — игнорируем
        pass


@router.callback_query(F.data == "admin_toggle_auto_delete")
async def admin_toggle_auto_delete(callback: CallbackQuery):
    """Переключение автоудаления неактивных пользователей"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    async with async_session() as session:
        stmt = select(BotSettings).where(BotSettings.key == "auto_delete_inactive")
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        
        if setting:
            new_value = "false" if setting.value == "true" else "true"
            setting.value = new_value
        else:
            setting = BotSettings(key="auto_delete_inactive", value="true")
            session.add(setting)
            new_value = "true"
        
        await session.commit()
    
    status = "включено ✅" if new_value == "true" else "выключено ❌"
    await callback.answer(f"Автоудаление {status}")
    
    # Обновляем страницу
    await admin_user_stats(callback)


@router.callback_query(F.data == "admin_delete_inactive")
async def admin_delete_inactive(callback: CallbackQuery):
    """Удаление всех неактивных пользователей"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    deleted_count = 0
    
    async with async_session() as session:
        stmt = select(User).where(User.failed_notifications >= 3).options(selectinload(User.configs))
        result = await session.execute(stmt)
        inactive_users = result.scalars().all()
        
        for user in inactive_users:
            # Удаляем конфиги с серверов
            for config in user.configs:
                if config.server_id:
                    server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                    if server:
                        await WireGuardMultiService.delete_config(config.name, server, config.public_key)
                else:
                    await WireGuardService.delete_config(config.name)
            
            await session.delete(user)
            deleted_count += 1
        
        await session.commit()
    
    await callback.answer(f"🗑 Удалено {deleted_count} неактивных пользователей")
    
    # Обновляем страницу
    await admin_user_stats(callback)


@router.callback_query(F.data == "close_message")
async def close_message(callback: CallbackQuery):
    """Закрыть сообщение"""
    await callback.answer()
    try:
        await callback.message.delete()
    except:
        pass


@router.callback_query(F.data == "admin_config_queue")
async def admin_config_queue(callback: CallbackQuery, bot: Bot):
    """Просмотр очереди ожидающих конфигов"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    from services.config_queue import ConfigQueueService
    from utils import format_datetime_moscow
    
    await callback.answer()
    
    queue = await ConfigQueueService.get_waiting_queue()
    
    if not queue:
        await callback.message.edit_text(
            "⏳ *Очередь конфигов*\n\n"
            "Очередь пуста — все пользователи получили свои конфиги!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")]
            ])
        )
        return
    
    text = f"⏳ *Очередь конфигов ({len(queue)})*\n\n"
    
    for i, item in enumerate(queue[:20], 1):  # Показываем первые 20
        user = item.user
        user_info = f"@{user.username}" if user and user.username else f"ID:{user.telegram_id}" if user else "?"
        created = format_datetime_moscow(item.created_at)
        text += f"{i}. {user_info} — `{item.config_name}`\n   📅 {created}\n"
    
    if len(queue) > 20:
        text += f"\n... и ещё {len(queue) - 20} в очереди"
    
    buttons = [
        [InlineKeyboardButton(text="🔄 Обработать очередь", callback_data="admin_process_queue")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")]
    ]
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data == "admin_process_queue")
async def admin_process_queue(callback: CallbackQuery, bot: Bot):
    """Принудительная обработка очереди"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    from services.config_queue import ConfigQueueService
    
    await callback.answer("⏳ Обрабатываю очередь...")
    
    processed, errors = await ConfigQueueService.process_queue(bot)
    remaining = await ConfigQueueService.get_waiting_count()
    
    await callback.message.edit_text(
        f"✅ *Очередь обработана*\n\n"
        f"Выдано конфигов: {processed}\n"
        f"Ошибок: {errors}\n"
        f"Осталось в очереди: {remaining}\n\n"
        f"{'⚠️ Нужно добавить сервер!' if remaining > 0 else '🎉 Все получили конфиги!'}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏳ Очередь", callback_data="admin_config_queue")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")]
        ])
    )


@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer()
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
    
    await callback.answer()
    page = int(callback.data.replace("admin_users_page_", ""))
    
    async with async_session() as session:
        stmt = select(User).order_by(User.created_at.desc())
        result = await session.execute(stmt)
        users = result.scalars().all()
    
    await callback.message.edit_reply_markup(
        reply_markup=get_users_list_kb(users, page)
    )


@router.callback_query(F.data.startswith("admin_user_") & ~F.data.contains("configs") & ~F.data.contains("payments") & ~F.data.contains("max_configs"))
async def admin_user_detail(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    await state.clear()  # Сбрасываем состояние при возврате
    await callback.answer()
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
        # Собираем трафик со всех серверов
        server_traffic_cache = {}  # Кэш трафика по server_id
        async with async_session() as traffic_session:
            for config in user.configs:
                if config.server_id:
                    # Мультисервер
                    if config.server_id not in server_traffic_cache:
                        server = await WireGuardMultiService.get_server_by_id(traffic_session, config.server_id)
                        if server:
                            server_traffic_cache[config.server_id] = await WireGuardMultiService.get_traffic_stats(server)
                        else:
                            server_traffic_cache[config.server_id] = {}
                    traffic_stats = server_traffic_cache[config.server_id]
                else:
                    # Локальный сервер
                    traffic_stats = await WireGuardService.get_traffic_stats()
                
                if config.public_key in traffic_stats:
                    stats = traffic_stats[config.public_key]
                    rx = format_bytes(stats['received'])
                    tx = format_bytes(stats['sent'])
                    traffic_info += f"\n📊 {config.name}: ⬇️{rx} ⬆️{tx}"
    
    username = f"@{user.username}" if user.username else "—"
    max_configs_text = f" (лимит: {user.max_configs})" if user.max_configs else ""
    
    await callback.message.edit_text(
        f"👤 Пользователь #{user.id}\n\n"
        f"🆔 Telegram ID: {user.telegram_id}\n"
        f"👤 Username: {username}\n"
        f"📝 Имя: {user.full_name}\n"
        f"📅 Регистрация: {format_date_moscow(user.created_at)}\n"
        f"🎁 Пробный: {'Использован' if user.trial_used else 'Доступен'}\n\n"
        f"📋 Подписка: {sub_status}\n"
        f"📱 Конфигов: {len(user.configs)}{max_configs_text}\n"
        f"💰 Платежей: {len(user.payments)}"
        f"{traffic_info}",
        parse_mode=None,
        reply_markup=get_user_detail_kb(user.id, user.max_configs)
    )


@router.callback_query(F.data.startswith("admin_user_configs_"))
async def admin_user_configs(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
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


@router.callback_query(F.data.startswith("admin_config_") & ~F.data.startswith("admin_config_queue"))
async def admin_config_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
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
        async with async_session() as traffic_session:
            if config.server_id:
                # Мультисервер
                server = await WireGuardMultiService.get_server_by_id(traffic_session, config.server_id)
                if server:
                    traffic_stats = await WireGuardMultiService.get_traffic_stats(server)
                else:
                    traffic_stats = {}
            else:
                # Локальный сервер
                traffic_stats = await WireGuardService.get_traffic_stats()
            
            if config.public_key in traffic_stats:
                stats = traffic_stats[config.public_key]
                rx = format_bytes(stats['received'])
                tx = format_bytes(stats['sent'])
                traffic_info = f"\n📊 Трафик: ⬇️{rx} ⬆️{tx}"
    
    await callback.message.edit_text(
        f"📱 Конфиг: {config.name}\n\n"
        f"Статус: {status}\n"
        f"IP: {config.client_ip}\n"
        f"Создан: {format_date_moscow(config.created_at)}"
        f"{traffic_info}",
        parse_mode=None,
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
            # Отключаем конфиг
            if config.server_id:
                # Мультисервер - отключаем на удалённом сервере
                server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                if server:
                    success, msg = await WireGuardMultiService.disable_config(config.public_key, server)
                else:
                    success, msg = True, "Сервер удалён"
            else:
                # Локальный сервер
                success, msg = await WireGuardService.disable_config(config.public_key)
            
            if success:
                config.is_active = False
                await session.commit()
                await callback.answer("🔴 Конфиг отключен")
            else:
                await callback.answer(f"Ошибка: {msg}", show_alert=True)
                return
        else:
            # Включаем конфиг
            if config.server_id:
                # Мультисервер - включаем на удалённом сервере
                server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                if server:
                    success, msg = await WireGuardMultiService.enable_config(
                        config.public_key, config.preshared_key, config.allowed_ips, server
                    )
                else:
                    await callback.answer("❌ Сервер удалён, конфиг нельзя включить", show_alert=True)
                    return
            else:
                # Локальный сервер
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
            f"📱 Конфиг: {config.name}\n\n"
            f"Статус: {status}\n"
            f"IP: {config.client_ip}\n"
            f"Создан: {format_date_moscow(config.created_at)}",
            parse_mode=None,
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
        
        # Удаляем с правильного сервера
        if config.server_id:
            server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
            if server:
                await WireGuardMultiService.delete_config(config_name, server, config.public_key)
        else:
            await WireGuardService.delete_config(config_name)
        
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
    
    await callback.answer()
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
        payments_text += f"\n{status_emoji} {format_datetime_moscow(p.created_at, '%d.%m')} — {tariff_name} ({p.amount}₽)"
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user_{user_id}")]
    ])
    
    await callback.message.edit_text(
        f"💰 *История платежей пользователя #{user.id}:*\n{payments_text}",
        parse_mode="Markdown",
        reply_markup=kb
    )


@router.callback_query(F.data.startswith("admin_user_max_configs_"))
async def admin_user_max_configs(callback: CallbackQuery, state: FSMContext):
    """Настройка индивидуального лимита конфигов пользователя"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    user_id = int(callback.data.replace("admin_user_max_configs_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        current_limit = user.max_configs
        global_limit = await get_setting("max_configs") or "0"
    
    await state.set_state(AdminStates.waiting_for_user_max_configs)
    await state.update_data(user_id=user_id, prompt_msg_id=callback.message.message_id)
    
    current_text = f"{current_limit}" if current_limit else f"глобальный ({global_limit if global_limit != '0' else '∞'})"
    
    await callback.message.edit_text(
        f"📱 *Лимит конфигов для пользователя #{user_id}*\n\n"
        f"Текущий лимит: {current_text}\n\n"
        f"Введите новое значение:\n"
        f"• Число — индивидуальный лимит\n"
        f"• 0 — использовать глобальный лимит",
        parse_mode="Markdown",
        reply_markup=get_user_max_configs_cancel_kb(user_id)
    )


@router.message(AdminStates.waiting_for_user_max_configs)
async def process_user_max_configs(message: Message, state: FSMContext, bot: Bot):
    """Обработка ввода лимита конфигов пользователя"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    user_id = data.get("user_id")
    prompt_msg_id = data.get("prompt_msg_id")
    
    if not user_id:
        await state.clear()
        await message.answer("❌ Ошибка: данные не найдены")
        return
    
    try:
        max_configs = int(message.text.strip())
        if max_configs < 0:
            raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Введите число (0 или больше)",
            reply_markup=get_user_max_configs_cancel_kb(user_id)
        )
        return
    
    # Удаляем сообщение с кнопкой "отмена"
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await state.clear()
            await message.answer("❌ Пользователь не найден")
            return
        
        user.max_configs = max_configs if max_configs > 0 else None
        await session.commit()
    
    await state.clear()
    
    result_text = f"{max_configs}" if max_configs > 0 else "глобальный"
    await message.answer(
        f"✅ Лимит конфигов для пользователя #{user_id} установлен: {result_text}",
        reply_markup=get_user_detail_kb(user_id, max_configs if max_configs > 0 else None)
    )


@router.callback_query(F.data == "admin_pending_payments")
async def admin_pending_payments(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
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


@router.callback_query(F.data == "admin_clear_pending_payments")
async def admin_clear_pending_payments(callback: CallbackQuery, bot: Bot):
    """Удаление всех ожидающих платежей"""
    if not is_admin(callback.from_user.id):
        return
    
    async with async_session() as session:
        stmt = select(Payment).where(Payment.status == "pending")
        result = await session.execute(stmt)
        payments = result.scalars().all()
        
        count = len(payments)
        for payment in payments:
            await session.delete(payment)
        await session.commit()
    
    await callback.answer(f"🗑 Удалено {count} платежей")
    
    await callback.message.edit_text(
        "✅ Все ожидающие платежи удалены",
        reply_markup=get_admin_menu_kb()
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
            f"💰 Платёж #{payment.id}\n\n"
            f"👤 Пользователь: {username}\n"
            f"🆔 ID: {user.telegram_id}\n"
            f"📋 Тариф: {tariff.get('name', payment.tariff_type)}\n"
            f"💵 Сумма: {payment.amount}₽\n"
            f"📅 Дата: {format_datetime_moscow(payment.created_at)}"
            f"{ocr_text}"
        ),
        parse_mode=None,
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
    referrer_id = None
    referrer_telegram_id = None
    referrer_percent = 10.0
    payment_amount = 0
    has_referral_discount = False
    
    async with async_session() as session:
        stmt = select(Payment).where(Payment.id == payment_id).options(
            selectinload(Payment.user).selectinload(User.subscriptions),
            selectinload(Payment.user).selectinload(User.configs),
            selectinload(Payment.user).selectinload(User.referrer)
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
        payment_amount = payment.amount
        has_referral_discount = payment.has_referral_discount
        
        # Сохраняем инфо о реферере
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
    server_id = None
    
    if not has_config:
        config_name = user_username if user_username else f"user{user_telegram_id}"
        success, config_data, server_id, msg = await create_config_multi_admin(config_name)
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
                bonus = payment_amount * (referrer_percent / 100)
                referrer.referral_balance += bonus
        
        await session.commit()
    
    # Уведомляем реферера о начислении бонуса
    if referrer_telegram_id:
        bonus = payment_amount * (referrer_percent / 100)
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
    
    await callback.answer("✅ Платёж подтверждён")
    
    # Удаляем сообщение с платежом
    try:
        await callback.message.delete()
    except:
        pass
    
    # Возвращаемся к списку платежей
    async with async_session() as session:
        stmt = select(Payment).where(Payment.status == "pending").options(
            selectinload(Payment.user)
        ).order_by(Payment.created_at.desc())
        result = await session.execute(stmt)
        payments = result.scalars().all()
    
    if not payments:
        await bot.send_message(
            callback.from_user.id,
            "✅ Нет платежей, ожидающих проверки",
            reply_markup=get_admin_menu_kb()
        )
    else:
        await bot.send_message(
            callback.from_user.id,
            f"💰 Ожидают проверки ({len(payments)}):",
            reply_markup=get_pending_payments_kb(payments)
        )
    
    try:
        msg_text = (
            f"✅ *Оплата подтверждена!*\n\n"
            f"📋 Тариф: {tariff.get('name', tariff_type)}\n"
            f"📅 Действует до: {format_date_moscow(new_expires)}\n"
        )
        
        if config_created:
            msg_text += "\nСейчас отправлю тебе конфиг."
        
        await bot.send_message(user_telegram_id, msg_text, parse_mode="Markdown")
        
        if config_created and not LOCAL_MODE:
            config_path = WireGuardService.get_config_file_path(config_name)
            qr_path = WireGuardService.get_qr_file_path(config_name)
            
            if os.path.exists(config_path):
                await bot.send_document(
                    user_telegram_id,
                    FSInputFile(config_path),
                    caption="📄 Твой WireGuard конфиг"
                )
            
            if os.path.exists(qr_path):
                await bot.send_photo(
                    user_telegram_id,
                    FSInputFile(qr_path),
                    caption="📷 QR-код для быстрой настройки"
                )
        
        menu_text = (
            "👋 Привет!\n\n"
            "📱 *Конфиги* — информация о подключении, QR-коды и доп. конфигурации\n"
            "📊 *Подписка* — детали подписки и продление"
        )
        await bot.send_message(
            user_telegram_id,
            menu_text,
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
        
        user_telegram_id = payment.user.telegram_id
        
        await callback.answer("❌ Платёж отклонён")
        
        # Удаляем сообщение с платежом
        try:
            await callback.message.delete()
        except:
            pass
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_telegram_id,
                "❌ Платёж отклонён\n\n"
                "Чек не прошёл проверку.\n"
                "Если ты уверен, что оплата была — напиши нам, разберёмся!",
                parse_mode=None,
                reply_markup=get_main_menu_kb(user_telegram_id, False)
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")
    
    # Возвращаемся к списку платежей
    async with async_session() as session:
        stmt = select(Payment).where(Payment.status == "pending").options(
            selectinload(Payment.user)
        ).order_by(Payment.created_at.desc())
        result = await session.execute(stmt)
        payments = result.scalars().all()
    
    if not payments:
        await bot.send_message(
            callback.from_user.id,
            "✅ Нет платежей, ожидающих проверки",
            reply_markup=get_admin_menu_kb()
        )
    else:
        await bot.send_message(
            callback.from_user.id,
            f"💰 Ожидают проверки ({len(payments)}):",
            reply_markup=get_pending_payments_kb(payments)
        )


@router.callback_query(F.data.startswith("admin_delete_payment_"))
async def admin_delete_payment(callback: CallbackQuery, bot: Bot):
    """Удаление платежа из БД"""
    if not is_admin(callback.from_user.id):
        return
    
    payment_id = int(callback.data.replace("admin_delete_payment_", ""))
    
    async with async_session() as session:
        stmt = select(Payment).where(Payment.id == payment_id)
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()
        
        if not payment:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        
        await session.delete(payment)
        await session.commit()
    
    await callback.answer("🗑 Платёж удалён")
    
    try:
        await callback.message.delete()
    except:
        pass
    
    # Возвращаемся к списку платежей
    async with async_session() as session:
        stmt = select(Payment).where(Payment.status == "pending").options(
            selectinload(Payment.user)
        ).order_by(Payment.created_at.desc())
        result = await session.execute(stmt)
        payments = result.scalars().all()
    
    if not payments:
        await bot.send_message(
            callback.from_user.id,
            "✅ Нет платежей, ожидающих проверки",
            reply_markup=get_admin_menu_kb()
        )
    else:
        await bot.send_message(
            callback.from_user.id,
            f"💰 *Ожидают проверки ({len(payments)}):*",
            parse_mode="Markdown",
            reply_markup=get_pending_payments_kb(payments)
        )


@router.callback_query(F.data.startswith("admin_gift_menu_"))
async def admin_gift_menu(callback: CallbackQuery):
    """Показывает меню выбора срока подарочной подписки"""
    if not is_admin(callback.from_user.id):
        return
    
    user_id = int(callback.data.replace("admin_gift_menu_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        user_info = f"@{user.username}" if user.username else user.full_name
    
    await callback.message.edit_text(
        f"🎁 Подарить подписку\n\n"
        f"👤 Пользователь: {user_info}\n\n"
        f"Выбери срок подписки:",
        parse_mode=None,
        reply_markup=get_gift_menu_kb(user_id)
    )


@router.callback_query(F.data.regexp(r"admin_gift_(30|90|180|unlimited)_(\d+)"))
async def admin_gift_subscription(callback: CallbackQuery, bot: Bot):
    """Дарит подписку на выбранный срок"""
    if not is_admin(callback.from_user.id):
        return
    
    # Парсим данные из callback
    parts = callback.data.split("_")
    gift_type = parts[2]  # 30, 90, 180 или unlimited
    user_id = int(parts[3])
    
    # Определяем срок подписки
    if gift_type == "unlimited":
        days = None
        tariff_type = "unlimited"
        gift_text = "бессрочная подписка"
        user_msg = "🎁 *Тебе подарена бессрочная подписка!*\n\nТвоя подписка теперь не имеет срока действия."
    else:
        days = int(gift_type)
        tariff_type = f"gift_{days}"
        gift_text = f"подписка на {days} дней"
        user_msg = f"🎁 *Тебе подарена подписка на {days} дней!*"
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(selectinload(User.configs), selectinload(User.subscriptions))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        # Удаляем все старые подписки при выдаче бессрочной
        if gift_type == "unlimited":
            for old_sub in user.subscriptions:
                await session.delete(old_sub)
        
        # Создаём подписку
        if days:
            expires_at = datetime.utcnow() + timedelta(days=days)
            subscription = Subscription(
                user_id=user.id,
                tariff_type=tariff_type,
                days_total=days,
                expires_at=expires_at,
                is_gift=True
            )
            user_msg += f"\n\n📅 Действует до: {format_date_moscow(expires_at)}"
        else:
            subscription = Subscription(
                user_id=user.id,
                tariff_type=tariff_type,
                days_total=0,
                expires_at=None,
                is_gift=True
            )
        session.add(subscription)
        
        # Создаём конфиг если нет
        config_created = False
        config_name = None
        config_data = None
        server_id = None
        if not user.configs:
            config_name = user.username if user.username else f"user{user.telegram_id}"
            success, config_data, server_id, msg = await create_config_multi_admin(config_name)
            
            if success:
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
                config_created = True
        else:
            # Активируем неактивные конфиги
            for cfg in user.configs:
                if not cfg.is_active:
                    success, msg = await WireGuardService.enable_config(
                        cfg.public_key, cfg.preshared_key, cfg.allowed_ips
                    )
                    if success:
                        cfg.is_active = True
        
        await session.commit()
        
        await callback.answer(f"🎁 Подарено: {gift_text}!")
        
        # Возвращаемся в админское меню пользователя
        user_info = f"@{user.username}" if user.username else user.full_name
        await callback.message.edit_text(
            f"✅ Подписка подарена!\n\n"
            f"👤 Пользователь: {user_info}\n"
            f"🎁 Подарок: {gift_text}",
            parse_mode=None,
            reply_markup=get_user_detail_kb(user_id)
        )
        
        # Отправляем уведомление пользователю
        try:
            if config_created:
                user_msg += "\n\nСейчас отправлю тебе конфиг."
            
            await bot.send_message(user.telegram_id, user_msg, parse_mode="Markdown")
            
            if config_created and not LOCAL_MODE:
                config_path = WireGuardService.get_config_file_path(config_name)
                qr_path = WireGuardService.get_qr_file_path(config_name)
                
                if os.path.exists(config_path):
                    await bot.send_document(
                        user.telegram_id,
                        FSInputFile(config_path),
                        caption="📄 Твой WireGuard конфиг"
                    )
                
                if os.path.exists(qr_path):
                    await bot.send_photo(
                        user.telegram_id,
                        FSInputFile(qr_path),
                        caption="📷 QR-код для быстрой настройки"
                    )
            
            menu_text = (
                "Всё управление VPN — кнопками ниже:\n\n"
                "📱 *Конфиги* — информация о подключении, QR-коды и доп. конфигурации\n"
                "📊 *Подписка* — детали подписки и продление"
            )
            await bot.send_message(
                user.telegram_id,
                menu_text,
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
        config_name = f"{user.username or 'user' + str(user.telegram_id)}_{config_num}"
        
        success, config_data, server_id, msg = await create_config_multi_admin(config_name)
        
        if not success:
            await callback.answer(f"Ошибка: {msg}", show_alert=True)
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
        await session.commit()
        
        await callback.answer("✅ Конфиг создан!")
        
        try:
            await bot.send_message(
                user.telegram_id,
                f"📱 *Тебе добавлен новый конфиг!*\n\n"
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
                        caption="📄 Твой WireGuard конфиг"
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
    
    await callback.answer()
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
        f"⚠️ Подтвердите удаление\n\n"
        f"Пользователь: {username}\n"
        f"ID: {user.telegram_id}\n\n"
        f"Будут удалены:\n"
        f"• Все конфиги\n"
        f"• Все подписки\n"
        f"• История платежей\n\n"
        f"Это действие нельзя отменить!",
        parse_mode=None,
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
        
        # Удаляем записи из очереди конфигов
        from database.models import ConfigQueue
        queue_stmt = select(ConfigQueue).where(ConfigQueue.user_id == user_id)
        queue_result = await session.execute(queue_stmt)
        for queue_item in queue_result.scalars().all():
            await session.delete(queue_item)
        
        # Удаляем конфиги с серверов
        for config in user.configs:
            if config.server_id:
                server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                if server:
                    await WireGuardMultiService.delete_config(config.name, server, config.public_key)
            else:
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
    
    await callback.answer()
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
        config_data = None
        server_id = None
        if not configs:
            config_name = user.username if user.username else f"user{user.telegram_id}"
            success, config_data, server_id, msg = await create_config_multi_admin(config_name)
            
            if success:
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
                "🎁 *Тебе подарен бессрочный VPN!*\n\n"
                "Твоя подписка теперь не имеет срока действия.\n"
            )
            
            if config_created:
                msg_text += "\nСейчас отправлю тебе конфиг."
            
            await bot.send_message(user.telegram_id, msg_text, parse_mode="Markdown")
            
            if config_created and not LOCAL_MODE:
                config_path = WireGuardService.get_config_file_path(config_name)
                qr_path = WireGuardService.get_qr_file_path(config_name)
                
                if os.path.exists(config_path):
                    await bot.send_document(
                        user.telegram_id,
                        FSInputFile(config_path),
                        caption="📄 Твой WireGuard конфиг"
                    )
                
                if os.path.exists(qr_path):
                    await bot.send_photo(
                        user.telegram_id,
                        FSInputFile(qr_path),
                        caption="📷 QR-код для быстрой настройки"
                    )
            
            menu_text = (
                "👋 Привет!\n\n"
                "📱 *Конфиги* — информация о подключении, QR-коды и доп. конфигурации\n"
                "📊 *Подписка* — детали подписки и продление"
            )
            await bot.send_message(
                user.telegram_id,
                menu_text,
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
    device_match = re.search(r'🖥 Устройство: (.+?)$', callback.message.text, re.MULTILINE)
    device_name = device_match.group(1).strip() if device_match else None
    
    # Формируем имя конфига: usernamedevice (транслитерация + очистка)
    base_name = user_username if user_username else f"user{user_telegram_id}"
    if device_name:
        # Транслитерируем русские буквы и очищаем от спецсимволов
        device_translit = transliterate_ru_to_en(device_name)
        clean_device = re.sub(r'[^\w]', '', device_translit)[:15]
        config_name = f"{base_name}{clean_device}"
    else:
        config_name = f"{base_name}{config_count + 1}" if config_count > 0 else base_name
    
    success, config_data, server_id, msg = await create_config_multi_admin(config_name)
    
    if not success:
        await callback.answer(f"Ошибка создания конфига: {msg}", show_alert=True)
        return
    
    async with async_session() as session:
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
            new_text + "\n\n✅ ОДОБРЕНО"
        )
    except:
        pass
    
    try:
        # Отправляем конфиг пользователю (без QR-кода — его можно найти в меню "Конфиги")
        if not LOCAL_MODE:
            config_path = WireGuardService.get_config_file_path(config_name)
            
            if os.path.exists(config_path):
                await bot.send_document(
                    user_telegram_id,
                    FSInputFile(config_path),
                    caption=f"📄 Твой новый конфиг: {config_name}\n\n📷 QR-код можно найти в меню «Конфиги»"
                )
        
        menu_text = (
            "Всё управление VPN — кнопками ниже:\n\n"
            "📱 *Конфиги* — информация о подключении, QR-коды и доп. конфигурации\n"
            "📊 *Подписка* — детали подписки и продление"
        )
        await bot.send_message(
            user_telegram_id,
            menu_text,
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
            "Если есть вопросы — напишите нам!",
            parse_mode="Markdown",
            reply_markup=get_main_menu_kb(user_telegram_id, True)
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")


@router.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    password_enabled = await get_setting("password_enabled") == "1"
    channel_required = await get_setting("channel_required") == "1"
    config_approval = await get_setting("config_approval_required") != "0"
    
    password_status = "🟢 Вкл" if password_enabled else "🔴 Выкл"
    channel_status = "🟢 Вкл" if channel_required else "🔴 Выкл"
    config_approval_status = "🟢 Вкл" if config_approval else "🔴 Выкл"
    
    await callback.message.edit_text(
        f"⚙️ *Настройки бота*\n\n"
        f"🔑 Пароль: {password_status}\n"
        f"📢 Подписка на канал: {channel_status}\n"
        f"📋 Подтверждение доп. конфига: {config_approval_status}",
        parse_mode="Markdown",
        reply_markup=get_settings_kb()
    )


@router.callback_query(F.data == "settings_password")
async def settings_password(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
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
    
    await callback.answer()
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
async def settings_channel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    await state.clear()  # Сбрасываем состояние при возврате
    await callback.answer()
    channel_required = await get_setting("channel_required") == "1"
    channel_name = await get_setting("channel_name") or "agdevpn"
    status = "🟢 Включена" if channel_required else "🔴 Выключена"
    
    await callback.message.edit_text(
        f"📢 *Подписка на канал*\n\n"
        f"Статус: {status}\n"
        f"Канал: @{channel_name}",
        parse_mode="Markdown",
        reply_markup=get_channel_settings_kb(channel_required)
    )


@router.callback_query(F.data == "settings_channel_on")
async def settings_channel_on(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("channel_required", "1")
    channel_name = await get_setting("channel_name") or "agdevpn"
    await callback.answer("✅ Подписка на канал включена")
    
    await callback.message.edit_text(
        f"📢 *Подписка на канал*\n\n"
        f"Статус: 🟢 Включена\n"
        f"Канал: @{channel_name}",
        parse_mode="Markdown",
        reply_markup=get_channel_settings_kb(True)
    )


@router.callback_query(F.data == "settings_channel_off")
async def settings_channel_off(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("channel_required", "0")
    channel_name = await get_setting("channel_name") or "agdevpn"
    await callback.answer("✅ Подписка на канал выключена")
    
    await callback.message.edit_text(
        f"📢 *Подписка на канал*\n\n"
        f"Статус: 🔴 Выключена\n"
        f"Канал: @{channel_name}",
        parse_mode="Markdown",
        reply_markup=get_channel_settings_kb(False)
    )


@router.callback_query(F.data == "settings_channel_change")
async def settings_channel_change(callback: CallbackQuery, state: FSMContext):
    """Изменение названия канала"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    channel_name = await get_setting("channel_name") or "agdevpn"
    
    await state.set_state(AdminStates.waiting_for_channel_name)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        f"✏️ *Изменение канала*\n\n"
        f"Текущий канал: @{channel_name}\n\n"
        f"Введите название канала (без @):",
        parse_mode="Markdown",
        reply_markup=get_channel_change_cancel_kb()
    )


@router.message(AdminStates.waiting_for_channel_name)
async def process_channel_name(message: Message, state: FSMContext, bot: Bot):
    """Обработка ввода названия канала"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    
    # Удаляем сообщение с кнопкой "отмена"
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    channel_name = message.text.strip().replace("@", "")
    
    await set_setting("channel_name", channel_name)
    await state.clear()
    
    channel_required = await get_setting("channel_required") == "1"
    status = "🟢 Включена" if channel_required else "🔴 Выключена"
    
    await message.answer(
        f"✅ Канал изменён на @{channel_name}\n\n"
        f"📢 *Подписка на канал*\n\n"
        f"Статус: {status}\n"
        f"Канал: @{channel_name}",
        parse_mode="Markdown",
        reply_markup=get_channel_settings_kb(channel_required)
    )


@router.callback_query(F.data == "settings_phone")
async def settings_phone(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    phone_required = await get_setting("phone_required") != "0"
    status = "🟢 Включён" if phone_required else "🔴 Выключен"
    
    await callback.message.edit_text(
        f"📱 *Запрос номера телефона*\n\n"
        f"Статус: {status}\n\n"
        f"_При регистрации бот будет просить поделиться номером телефона_",
        parse_mode="Markdown",
        reply_markup=get_phone_settings_kb(phone_required)
    )


@router.callback_query(F.data == "settings_phone_on")
async def settings_phone_on(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("phone_required", "1")
    await callback.answer("✅ Запрос телефона включён")
    
    await callback.message.edit_text(
        f"📱 *Запрос номера телефона*\n\n"
        f"Статус: 🟢 Включён\n\n"
        f"_При регистрации бот будет просить поделиться номером телефона_",
        parse_mode="Markdown",
        reply_markup=get_phone_settings_kb(True)
    )


@router.callback_query(F.data == "settings_phone_off")
async def settings_phone_off(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("phone_required", "0")
    await callback.answer("✅ Запрос телефона выключен")
    
    await callback.message.edit_text(
        f"📱 *Запрос номера телефона*\n\n"
        f"Статус: 🔴 Выключен\n\n"
        f"_При регистрации бот НЕ будет просить номер телефона_",
        parse_mode="Markdown",
        reply_markup=get_phone_settings_kb(False)
    )


@router.callback_query(F.data == "settings_config_approval")
async def settings_config_approval(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    await state.clear()  # Сбрасываем состояние при возврате
    await callback.answer()
    config_approval = await get_setting("config_approval_required") != "0"
    max_configs = int(await get_setting("max_configs") or "0")
    
    status = "🟢 Включено" if config_approval else "🔴 Выключено"
    desc = "_Требуется подтверждение админа_" if config_approval else "_Конфиг создаётся автоматически_"
    max_text = f"Макс. конфигов: *{max_configs}*" if max_configs > 0 else "Макс. конфигов: *∞ (без лимита)*"
    
    await callback.message.edit_text(
        f"📋 *Подтверждение доп. конфига*\n\n"
        f"Статус: {status}\n"
        f"{max_text}\n\n"
        f"{desc}",
        parse_mode="Markdown",
        reply_markup=get_config_approval_kb(config_approval, max_configs)
    )


@router.callback_query(F.data == "settings_config_approval_on")
async def settings_config_approval_on(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("config_approval_required", "1")
    max_configs = int(await get_setting("max_configs") or "0")
    await callback.answer("✅ Подтверждение включено")
    
    await callback.message.edit_text(
        f"📋 *Подтверждение доп. конфига*\n\n"
        f"Статус: 🟢 Включено\n\n"
        f"_Требуется подтверждение админа_",
        parse_mode="Markdown",
        reply_markup=get_config_approval_kb(True, max_configs)
    )


@router.callback_query(F.data == "settings_config_approval_off")
async def settings_config_approval_off(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await set_setting("config_approval_required", "0")
    max_configs = int(await get_setting("max_configs") or "0")
    await callback.answer("✅ Подтверждение выключено")
    
    await callback.message.edit_text(
        f"📋 *Подтверждение доп. конфига*\n\n"
        f"Статус: 🔴 Выключено\n\n"
        f"_Конфиг создаётся автоматически_",
        parse_mode="Markdown",
        reply_markup=get_config_approval_kb(False, max_configs)
    )


@router.callback_query(F.data == "settings_max_configs")
async def settings_max_configs(callback: CallbackQuery, state: FSMContext):
    """Настройка глобального лимита конфигов"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    max_configs = await get_setting("max_configs") or "0"
    
    await state.set_state(AdminStates.waiting_for_max_configs)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        f"📱 *Максимальное количество конфигов*\n\n"
        f"Текущий лимит: {max_configs if max_configs != '0' else '∞ (без лимита)'}\n\n"
        f"Введите новое значение (0 = без лимита):",
        parse_mode="Markdown",
        reply_markup=get_max_configs_cancel_kb()
    )


@router.message(AdminStates.waiting_for_max_configs)
async def process_max_configs(message: Message, state: FSMContext, bot: Bot):
    """Обработка ввода лимита конфигов"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    
    try:
        max_configs = int(message.text.strip())
        if max_configs < 0:
            raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Введите число (0 или больше)",
            reply_markup=get_max_configs_cancel_kb()
        )
        return
    
    # Удаляем сообщение с кнопкой "отмена"
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    await set_setting("max_configs", str(max_configs))
    await state.clear()
    
    config_approval = await get_setting("config_approval_required") != "0"
    
    await message.answer(
        f"✅ Лимит конфигов установлен: {max_configs if max_configs > 0 else '∞ (без лимита)'}\n\n"
        f"📋 *Подтверждение доп. конфига*\n\n"
        f"Статус: {'🟢 Включено' if config_approval else '🔴 Выключено'}",
        parse_mode="Markdown",
        reply_markup=get_config_approval_kb(config_approval, max_configs)
    )


@router.callback_query(F.data == "settings_monitoring")
async def settings_monitoring(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
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
    
    await callback.answer()
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
    
    await callback.answer()
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


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    await callback.message.edit_text(
        "✉️ *Рассылка сообщений*\n\n"
        "Выбери кому отправить:",
        parse_mode="Markdown",
        reply_markup=get_broadcast_menu_kb()
    )


@router.callback_query(F.data == "broadcast_all")
async def broadcast_all(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_broadcast_all)
    await state.update_data(broadcast_prompt_msg_id=callback.message.message_id)
    
    await callback.message.edit_text(
        "📢 *Рассылка всем пользователям*\n\n"
        "Отправь сообщение, которое хочешь разослать.\n"
        "Можно отправить:\n"
        "• Текст\n"
        "• Фото\n"
        "• Голосовое\n"
        "• Кружок (видеосообщение)",
        parse_mode="Markdown",
        reply_markup=get_broadcast_cancel_kb()
    )


@router.callback_query(F.data == "broadcast_select")
async def broadcast_select(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    
    async with async_session() as session:
        stmt = select(User).where(User.is_blocked == False)
        result = await session.execute(stmt)
        users = result.scalars().all()
    
    await callback.message.edit_text(
        "👤 *Выбери пользователя:*",
        parse_mode="Markdown",
        reply_markup=get_broadcast_users_kb(users)
    )


@router.callback_query(F.data.startswith("broadcast_page_"))
async def broadcast_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    page = int(callback.data.replace("broadcast_page_", ""))
    await callback.answer()
    
    async with async_session() as session:
        stmt = select(User).where(User.is_blocked == False)
        result = await session.execute(stmt)
        users = result.scalars().all()
    
    await callback.message.edit_text(
        "👤 *Выбери пользователя:*",
        parse_mode="Markdown",
        reply_markup=get_broadcast_users_kb(users, page)
    )


@router.callback_query(F.data.startswith("broadcast_user_"))
async def broadcast_user_select(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    user_telegram_id = int(callback.data.replace("broadcast_user_", ""))
    await callback.answer()
    
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == user_telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
    
    if not user:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    
    await state.update_data(
        broadcast_user_id=user_telegram_id,
        broadcast_prompt_msg_id=callback.message.message_id
    )
    await state.set_state(AdminStates.waiting_for_broadcast_user)
    
    name = user.username or user.full_name
    await callback.message.edit_text(
        f"📨 *Сообщение для @{name}*\n\n"
        "Отправь сообщение, которое хочешь отправить.\n"
        "Можно отправить:\n"
        "• Текст\n"
        "• Фото\n"
        "• Голосовое\n"
        "• Кружок (видеосообщение)",
        parse_mode="Markdown",
        reply_markup=get_broadcast_cancel_kb()
    )


@router.message(AdminStates.waiting_for_broadcast_all)
async def process_broadcast_all(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    prompt_msg_id = data.get("broadcast_prompt_msg_id")
    
    # Удаляем сообщение с кнопкой "отмена"
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    async with async_session() as session:
        stmt = select(User).where(User.is_blocked == False)
        result = await session.execute(stmt)
        users = result.scalars().all()
    
    success = 0
    failed = 0
    
    for user in users:
        try:
            if message.text:
                await bot.send_message(user.telegram_id, message.text)
            elif message.photo:
                await bot.send_photo(
                    user.telegram_id,
                    message.photo[-1].file_id,
                    caption=message.caption
                )
            elif message.voice:
                await bot.send_voice(user.telegram_id, message.voice.file_id)
            elif message.video_note:
                await bot.send_video_note(user.telegram_id, message.video_note.file_id)
            elif message.video:
                await bot.send_video(
                    user.telegram_id,
                    message.video.file_id,
                    caption=message.caption
                )
            elif message.document:
                await bot.send_document(
                    user.telegram_id,
                    message.document.file_id,
                    caption=message.caption
                )
            success += 1
        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {user.telegram_id}: {e}")
            failed += 1
    
    await state.clear()
    await message.answer(
        f"✅ *Рассылка завершена*\n\n"
        f"📨 Отправлено: {success}\n"
        f"❌ Ошибок: {failed}",
        parse_mode="Markdown",
        reply_markup=get_broadcast_menu_kb()
    )


@router.message(AdminStates.waiting_for_broadcast_user)
async def process_broadcast_user(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    user_telegram_id = data.get("broadcast_user_id")
    prompt_msg_id = data.get("broadcast_prompt_msg_id")
    
    if not user_telegram_id:
        await state.clear()
        await message.answer("❌ Ошибка: пользователь не выбран")
        return
    
    # Удаляем сообщение с кнопкой "отмена"
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    try:
        if message.text:
            await bot.send_message(user_telegram_id, message.text)
        elif message.photo:
            await bot.send_photo(
                user_telegram_id,
                message.photo[-1].file_id,
                caption=message.caption
            )
        elif message.voice:
            await bot.send_voice(user_telegram_id, message.voice.file_id)
        elif message.video_note:
            await bot.send_video_note(user_telegram_id, message.video_note.file_id)
        elif message.video:
            await bot.send_video(
                user_telegram_id,
                message.video.file_id,
                caption=message.caption
            )
        elif message.document:
            await bot.send_document(
                user_telegram_id,
                message.document.file_id,
                caption=message.caption
            )
        
        await state.clear()
        await message.answer(
            "✅ *Сообщение отправлено!*",
            parse_mode="Markdown",
            reply_markup=get_broadcast_menu_kb()
        )
    except Exception as e:
        logger.error(f"Ошибка отправки пользователю {user_telegram_id}: {e}")
        await state.clear()
        await message.answer(
            f"❌ Ошибка отправки: {e}",
            reply_markup=get_broadcast_menu_kb()
        )


# ==================== УПРАВЛЕНИЕ СЕРВЕРАМИ ====================

@router.callback_query(F.data == "admin_servers")
async def admin_servers_list(callback: CallbackQuery, state: FSMContext):
    """Список серверов"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await state.clear()  # Сбрасываем состояние при возврате к списку
    await callback.answer()
    async with async_session() as session:
        servers = await WireGuardMultiService.get_all_servers(session)
        
        # Получаем количество клиентов для каждого сервера
        client_counts = {}
        for server in servers:
            count = await WireGuardMultiService.get_server_client_count(session, server.id)
            client_counts[server.id] = count
    
    if not servers:
        text = "🖥 *Серверы*\n\nСерверов пока нет. Добавьте первый сервер."
    else:
        text = f"🖥 *Серверы ({len(servers)}):*"
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_servers_list_kb(servers, client_counts)
    )


@router.callback_query(F.data.startswith("admin_server_") & ~F.data.startswith("admin_server_add") & ~F.data.startswith("admin_server_check_") & ~F.data.startswith("admin_server_toggle_") & ~F.data.startswith("admin_server_edit_") & ~F.data.startswith("admin_server_stats_") & ~F.data.startswith("admin_server_delete_") & ~F.data.startswith("admin_server_confirm_delete_") & ~F.data.startswith("admin_server_install_") & ~F.data.startswith("admin_server_clients_") & ~F.data.startswith("admin_server_broadcast_") & ~F.data.startswith("admin_server_migrate_") & ~F.data.startswith("admin_server_cleanup_"))
async def admin_server_detail(callback: CallbackQuery):
    """Детальная информация о сервере"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    server_id = int(callback.data.split("_")[-1])
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.message.edit_text(
                "❌ Сервер не найден",
                reply_markup=get_servers_list_kb([])
            )
            return
        
        client_count = await WireGuardMultiService.get_server_client_count(session, server_id)
    
    status = "🟢 Активен" if server.is_active else "🔴 Отключен"
    has_clients = client_count > 0
    
    text = (
        f"🖥 *{server.name}*\n\n"
        f"*Хост:* `{server.host}`\n"
        f"*Пароль:* `{server.ssh_password}`\n"
        f"*SSH:* {server.ssh_user}@{server.host}:{server.ssh_port}\n"
        f"*Статус:* {status}\n"
        f"*Клиентов:* {client_count}/{server.max_clients}\n"
        f"*Приоритет:* {server.priority}\n"
        f"*Интерфейс:* {server.wg_interface}\n"
        f"*Создан:* {format_date_moscow(server.created_at)}"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_server_detail_kb(server_id, server.is_active, has_clients)
    )


@router.callback_query(F.data == "admin_server_add")
async def admin_server_add(callback: CallbackQuery, state: FSMContext):
    """Начало добавления сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_server_data)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    
    await callback.message.edit_text(
        "🖥 *Добавление сервера*\n\n"
        "Введите данные сервера в формате:\n"
        "`имя|хост|ssh_пароль|макс_клиентов`\n\n"
        "*Пример:*\n"
        "`Germany-1|185.123.45.67|mypassword123|30`\n\n"
        "SSH пользователь по умолчанию: root\n"
        "SSH порт по умолчанию: 22",
        parse_mode="Markdown",
        reply_markup=get_server_add_cancel_kb()
    )


@router.message(AdminStates.waiting_for_server_data)
async def process_server_add(message: Message, state: FSMContext, bot: Bot):
    """Обработка добавления сервера"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    
    # Удаляем сообщение с кнопкой "отмена"
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    logger.info(f"[process_server_add] Начало добавления сервера: {message.text[:50]}...")
    parts = message.text.strip().split("|")
    if len(parts) < 3:
        await message.answer(
            "❌ Неверный формат. Используйте:\n"
            "`имя|хост|ssh_пароль|макс_клиентов`",
            parse_mode="Markdown",
            reply_markup=get_server_add_cancel_kb()
        )
        return
    
    name = parts[0].strip()
    host = parts[1].strip()
    ssh_password = parts[2].strip()
    max_clients = int(parts[3].strip()) if len(parts) > 3 else 30
    
    # Проверяем, нет ли сервера с таким хостом
    async with async_session() as session:
        existing = await session.execute(
            select(Server).where(Server.host == host)
        )
        if existing.scalar_one_or_none():
            await message.answer(
                f"❌ Сервер с хостом `{host}` уже существует",
                parse_mode="Markdown",
                reply_markup=get_server_add_cancel_kb()
            )
            return
        
        # Создаём сервер
        server = Server(
            name=name,
            host=host,
            ssh_user="root",
            ssh_port=22,
            ssh_password=ssh_password,
            max_clients=max_clients,
            is_active=False,  # Пока не активен, до проверки WG
            priority=0
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        server_id = server.id
    
    # Проверяем подключение (вне сессии)
    status_msg = await message.answer("⏳ Проверяю подключение к серверу...")
    
    # Создаём временный объект для проверки (со всеми нужными полями)
    temp_server = Server(
        id=server_id,
        name=name,
        host=host,
        ssh_user="root",
        ssh_port=22,
        ssh_password=ssh_password,
        max_clients=max_clients,
        wg_interface="wg0",
        wg_conf_path="/etc/wireguard/wg0.conf",
        client_dir="/etc/wireguard/clients",
        add_script="/usr/local/bin/wg-new-conf.sh",
        remove_script="/usr/local/bin/wg-remove-client.sh"
    )
    
    logger.info(f"[process_server_add] Проверяю SSH подключение к {host}...")
    success, msg = await WireGuardMultiService.check_server_connection(temp_server)
    logger.info(f"[process_server_add] SSH результат: success={success}, msg={msg}")
    
    if not success:
        await status_msg.edit_text(
            f"❌ *Не удалось подключиться к серверу*\n\n"
            f"*Хост:* `{host}`\n"
            f"*Ошибка:* {msg}\n\n"
            f"Сервер добавлен, но неактивен. Проверьте данные.",
            parse_mode="Markdown",
            reply_markup=get_server_detail_kb(server_id, False)
        )
        await state.clear()
        return
    
    # Проверяем готовность WireGuard
    logger.info(f"[process_server_add] Начинаю проверку WireGuard на {host}...")
    try:
        await status_msg.edit_text("⏳ Проверяю WireGuard на сервере...")
    except Exception:
        pass
    
    wg_ready, wg_msg = await WireGuardMultiService.check_wireguard_ready(temp_server)
    logger.info(f"[process_server_add] WireGuard результат: ready={wg_ready}, msg={wg_msg}")
    
    if wg_ready:
        # WireGuard готов — активируем сервер
        async with async_session() as session:
            server = await WireGuardMultiService.get_server_by_id(session, server_id)
            if server:
                server.is_active = True
                await session.commit()
        
        try:
            await status_msg.edit_text(
                f"✅ *Сервер добавлен и готов к работе!*\n\n"
                f"*Имя:* {name}\n"
                f"*Хост:* `{host}`\n"
                f"*Пароль:* `{ssh_password}`\n"
                f"*Макс. клиентов:* {max_clients}\n\n"
                f"✅ WireGuard готов",
                parse_mode="Markdown",
                reply_markup=get_server_detail_kb(server_id, True)
            )
        except Exception:
            await message.answer(
                f"✅ *Сервер добавлен и готов к работе!*\n\n"
                f"*Имя:* {name}\n"
                f"*Хост:* `{host}`\n"
                f"*Пароль:* `{ssh_password}`\n"
                f"*Макс. клиентов:* {max_clients}\n\n"
                f"✅ WireGuard готов",
                parse_mode="Markdown",
                reply_markup=get_server_detail_kb(server_id, True)
            )
        await state.clear()
        return
    
    # WireGuard не готов — предлагаем установить
    try:
        await status_msg.edit_text(
            f"⚠️ *WireGuard не настроен на сервере*\n\n"
            f"*Хост:* `{host}`\n"
            f"*Статус:* {wg_msg}\n\n"
            f"Хотите установить WireGuard автоматически?\n"
            f"Это займёт 1-2 минуты.",
            parse_mode="Markdown",
            reply_markup=get_server_install_kb(server_id)
        )
    except Exception:
        await message.answer(
            f"⚠️ *WireGuard не настроен на сервере*\n\n"
            f"*Хост:* `{host}`\n"
            f"*Статус:* {wg_msg}\n\n"
            f"Хотите установить WireGuard автоматически?\n"
            f"Это займёт 1-2 минуты.",
            parse_mode="Markdown",
            reply_markup=get_server_install_kb(server_id)
        )
    
    await state.clear()


@router.callback_query(F.data.startswith("admin_server_check_"))
async def admin_server_check(callback: CallbackQuery):
    """Проверка подключения к серверу"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    await callback.answer("⏳ Проверяю подключение...")
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        # Проверяем SSH
        ssh_ok, ssh_msg = await WireGuardMultiService.check_server_connection(server)
        
        # Проверяем WireGuard
        if ssh_ok:
            wg_ok, wg_msg = await WireGuardMultiService.check_wireguard_installed(server)
        else:
            wg_ok, wg_msg = False, "SSH недоступен"
        
        client_count = await WireGuardMultiService.get_server_client_count(session, server_id)
    
    status = "🟢 Активен" if server.is_active else "🔴 Отключен"
    ssh_status = "✅" if ssh_ok else "❌"
    wg_status = "✅" if wg_ok else "❌"
    
    text = (
        f"🖥 *{server.name}*\n\n"
        f"*Хост:* `{server.host}`\n"
        f"*Статус:* {status}\n"
        f"*Клиентов:* {client_count}/{server.max_clients}\n\n"
        f"*Проверка подключения:*\n"
        f"{ssh_status} SSH: {ssh_msg}\n"
        f"{wg_status} WireGuard: {wg_msg}"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_server_detail_kb(server_id, server.is_active)
    )


@router.callback_query(F.data.startswith("admin_server_toggle_"))
async def admin_server_toggle(callback: CallbackQuery, bot: Bot):
    """Включение/отключение сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.answer("❌ Сервер не найден", show_alert=True)
            return
        
        was_inactive = not server.is_active
        server.is_active = not server.is_active
        await session.commit()
        
        status = "включен" if server.is_active else "отключен"
        await callback.answer(f"✅ Сервер {status}")
        
        client_count = await WireGuardMultiService.get_server_client_count(session, server_id)
    
    # Если сервер был включен - обрабатываем очередь
    if was_inactive and server.is_active:
        from services.config_queue import check_and_process_queue
        await check_and_process_queue(bot)
    
    status_text = "🟢 Активен" if server.is_active else "🔴 Отключен"
    
    text = (
        f"🖥 *{server.name}*\n\n"
        f"*Хост:* `{server.host}`\n"
        f"*SSH:* {server.ssh_user}@{server.host}:{server.ssh_port}\n"
        f"*Статус:* {status_text}\n"
        f"*Клиентов:* {client_count}/{server.max_clients}\n"
        f"*Приоритет:* {server.priority}"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_server_detail_kb(server_id, server.is_active)
    )


@router.callback_query(F.data.startswith("admin_server_delete_"))
async def admin_server_delete(callback: CallbackQuery):
    """Подтверждение удаления сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    server_id = int(callback.data.split("_")[-1])
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        client_count = await WireGuardMultiService.get_server_client_count(session, server_id)
    
    if not server:
        await callback.message.edit_text("❌ Сервер не найден")
        return
    
    warning = ""
    if client_count > 0:
        warning = f"\n\n⚠️ *Внимание!* На сервере {client_count} активных конфигов!"
    
    await callback.message.edit_text(
        f"🗑 *Удалить сервер {server.name}?*{warning}",
        parse_mode="Markdown",
        reply_markup=get_server_confirm_delete_kb(server_id)
    )


@router.callback_query(F.data.startswith("admin_server_confirm_delete_"))
async def admin_server_confirm_delete(callback: CallbackQuery):
    """Удаление сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.answer("❌ Сервер не найден", show_alert=True)
            return
        
        server_name = server.name
        
        # Удаляем все конфиги этого сервера (они всё равно бесполезны без сервера)
        configs_result = await session.execute(
            select(Config).where(Config.server_id == server_id)
        )
        configs = configs_result.scalars().all()
        deleted_count = len(configs)
        for config in configs:
            await session.delete(config)
        
        await session.delete(server)
        await session.commit()
    
    await callback.answer(f"✅ Сервер {server_name} удален, {deleted_count} конфигов удалено")
    
    # Возвращаемся к списку серверов
    async with async_session() as session:
        servers = await WireGuardMultiService.get_all_servers(session)
        client_counts = {}
        for s in servers:
            count = await WireGuardMultiService.get_server_client_count(session, s.id)
            client_counts[s.id] = count
    
    await callback.message.edit_text(
        f"🖥 *Серверы ({len(servers)}):*" if servers else "🖥 *Серверы*\n\nСерверов пока нет.",
        parse_mode="Markdown",
        reply_markup=get_servers_list_kb(servers, client_counts)
    )


@router.callback_query(F.data.startswith("admin_server_stats_"))
async def admin_server_stats(callback: CallbackQuery):
    """Статистика сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    await callback.answer("⏳ Загружаю статистику...")
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        client_count = await WireGuardMultiService.get_server_client_count(session, server_id)
        
        # Получаем статистику трафика
        traffic_stats = await WireGuardMultiService.get_traffic_stats(server)
    
    total_rx = sum(p.get('received', 0) for p in traffic_stats.values())
    total_tx = sum(p.get('sent', 0) for p in traffic_stats.values())
    
    text = (
        f"📊 *Статистика: {server.name}*\n\n"
        f"*Активных пиров:* {len(traffic_stats)}\n"
        f"*Конфигов в БД:* {client_count}\n\n"
        f"*Трафик:*\n"
        f"📥 Получено: {WireGuardMultiService.format_bytes(total_rx)}\n"
        f"📤 Отправлено: {WireGuardMultiService.format_bytes(total_tx)}"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_server_detail_kb(server_id, server.is_active)
    )


@router.callback_query(F.data.startswith("admin_server_cleanup_"))
async def admin_server_cleanup(callback: CallbackQuery):
    """Очистка мёртвых пиров (есть на сервере, но нет в БД)"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.replace("admin_server_cleanup_", ""))
    await callback.answer("⏳ Анализирую пиры...")
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        # Получаем пиры с сервера
        try:
            success, stdout, stderr = await WireGuardMultiService._ssh_execute(
                server, f"wg show {server.wg_interface} peers"
            )
            if not success:
                await callback.message.edit_text(
                    f"❌ Ошибка получения пиров: {stderr}",
                    reply_markup=get_server_detail_kb(server_id, server.is_active)
                )
                return
            
            server_peers = set(stdout.strip().split('\n')) if stdout.strip() else set()
        except Exception as e:
            await callback.message.edit_text(
                f"❌ Ошибка подключения: {e}",
                reply_markup=get_server_detail_kb(server_id, server.is_active)
            )
            return
        
        # Получаем public_key из БД для этого сервера
        stmt = select(Config.public_key).where(Config.server_id == server_id)
        result = await session.execute(stmt)
        db_keys = set(row[0] for row in result.fetchall() if row[0])
        
        # Находим мёртвые пиры (есть на сервере, но нет в БД)
        dead_peers = server_peers - db_keys
        dead_peers.discard('')  # Убираем пустые строки
        
        if not dead_peers:
            await callback.message.edit_text(
                f"✅ На сервере *{server.name}* нет мёртвых пиров.\n\n"
                f"Пиров на сервере: {len(server_peers)}\n"
                f"Конфигов в БД: {len(db_keys)}",
                parse_mode="Markdown",
                reply_markup=get_server_detail_kb(server_id, server.is_active)
            )
            return
        
        # Удаляем мёртвые пиры
        removed = 0
        for peer_key in dead_peers:
            try:
                await WireGuardMultiService._ssh_execute(
                    server, f"wg set {server.wg_interface} peer {peer_key} remove"
                )
                removed += 1
            except Exception as e:
                logger.error(f"Ошибка удаления пира {peer_key}: {e}")
        
        # Сохраняем конфиг
        await WireGuardMultiService._ssh_execute(
            server, f"wg-quick save {server.wg_interface}"
        )
        
        await callback.message.edit_text(
            f"🧹 *Очистка завершена*\n\n"
            f"Сервер: *{server.name}*\n"
            f"Удалено мёртвых пиров: *{removed}*\n\n"
            f"Было пиров: {len(server_peers)}\n"
            f"Конфигов в БД: {len(db_keys)}\n"
            f"Теперь пиров: {len(server_peers) - removed}",
            parse_mode="Markdown",
            reply_markup=get_server_detail_kb(server_id, server.is_active)
        )


@router.callback_query(F.data.startswith("admin_server_install_"))
async def admin_server_install(callback: CallbackQuery, bot: Bot):
    """Установка WireGuard на сервер"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    await callback.answer()
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        # Сохраняем данные для обновления статуса
        server_name = server.name
        server_host = server.host
        server_password = server.ssh_password
        
        # Начинаем установку
        await callback.message.edit_text(
            f"🚀 *Установка WireGuard на {server_name}*\n\n"
            f"*Хост:* `{server_host}`\n\n"
            f"⏳ Подключение к серверу...",
            parse_mode="Markdown"
        )
        
        # Callback для обновления прогресса
        async def progress_callback(step: str, msg: str):
            step_icons = {
                "connect": "🔌",
                "check": "🔍",
                "install": "📦",
                "sysctl": "⚙️",
                "keys": "🔑",
                "interface": "🌐",
                "config": "📝",
                "scripts": "📜",
                "start": "🚀",
                "verify": "✅",
                "done": "🎉"
            }
            icon = step_icons.get(step, "⏳")
            try:
                await callback.message.edit_text(
                    f"🚀 *Установка WireGuard на {server_name}*\n\n"
                    f"*Хост:* `{server_host}`\n\n"
                    f"{icon} {msg}",
                    parse_mode="Markdown"
                )
            except:
                pass  # Игнорируем ошибки редактирования
        
        # Запускаем установку
        success, result_msg = await WireGuardMultiService.install_wireguard(server, progress_callback)
        
        if success:
            # Активируем сервер
            server.is_active = True
            await session.commit()
            
            # Обрабатываем очередь ожидающих
            from services.config_queue import check_and_process_queue
            await check_and_process_queue(bot)
            
            await callback.message.edit_text(
                f"✅ *WireGuard успешно установлен!*\n\n"
                f"*Сервер:* {server_name}\n"
                f"*Хост:* `{server_host}`\n"
                f"*Пароль:* `{server_password}`\n\n"
                f"{result_msg}\n\n"
                f"Сервер активирован и готов к работе!",
                parse_mode="Markdown",
                reply_markup=get_server_detail_kb(server_id, True)
            )
        else:
            await callback.message.edit_text(
                f"❌ *Ошибка установки WireGuard*\n\n"
                f"*Сервер:* {server_name}\n"
                f"*Хост:* `{server_host}`\n\n"
                f"*Ошибка:* {result_msg}\n\n"
                f"Попробуйте установить вручную или проверьте доступ к серверу.",
                parse_mode="Markdown",
                reply_markup=get_server_install_kb(server_id)
            )


@router.callback_query(F.data.startswith("admin_server_edit_") & ~F.data.startswith("admin_server_edit_name_") & ~F.data.startswith("admin_server_edit_max_") & ~F.data.startswith("admin_server_edit_priority_"))
async def admin_server_edit_menu(callback: CallbackQuery):
    """Меню редактирования сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    await callback.answer()
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        await callback.message.edit_text(
            f"✏️ *Редактирование сервера*\n\n"
            f"*Имя:* {server.name}\n"
            f"*Хост:* `{server.host}`\n"
            f"*Макс. клиентов:* {server.max_clients}\n"
            f"*Приоритет:* {server.priority}\n\n"
            f"Выберите что изменить:",
            parse_mode="Markdown",
            reply_markup=get_server_edit_kb(server_id)
        )


@router.callback_query(F.data.startswith("admin_server_edit_name_"))
async def admin_server_edit_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования имени сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    await callback.answer()
    
    await state.set_state(AdminStates.waiting_for_server_edit)
    await state.update_data(server_id=server_id, edit_field="name", prompt_msg_id=callback.message.message_id)
    
    await callback.message.edit_text(
        "📝 *Изменение имени сервера*\n\n"
        "Введите новое имя:",
        parse_mode="Markdown",
        reply_markup=get_server_edit_cancel_kb(server_id)
    )


@router.callback_query(F.data.startswith("admin_server_edit_max_"))
async def admin_server_edit_max_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования макс. клиентов"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    await callback.answer()
    
    await state.set_state(AdminStates.waiting_for_server_edit)
    await state.update_data(server_id=server_id, edit_field="max_clients", prompt_msg_id=callback.message.message_id)
    
    await callback.message.edit_text(
        "👥 *Изменение макс. клиентов*\n\n"
        "Введите новое значение (число):",
        parse_mode="Markdown",
        reply_markup=get_server_edit_cancel_kb(server_id)
    )


@router.callback_query(F.data.startswith("admin_server_edit_priority_"))
async def admin_server_edit_priority_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования приоритета"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    await callback.answer()
    
    await state.set_state(AdminStates.waiting_for_server_edit)
    await state.update_data(server_id=server_id, edit_field="priority", prompt_msg_id=callback.message.message_id)
    
    await callback.message.edit_text(
        "⭐ *Изменение приоритета*\n\n"
        "Введите новое значение (число).\n"
        "Чем выше число — тем приоритетнее сервер при распределении.",
        parse_mode="Markdown",
        reply_markup=get_server_edit_cancel_kb(server_id)
    )


@router.message(AdminStates.waiting_for_server_edit)
async def process_server_edit(message: Message, state: FSMContext, bot: Bot):
    """Обработка редактирования сервера"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    server_id = data.get("server_id")
    edit_field = data.get("edit_field")
    prompt_msg_id = data.get("prompt_msg_id")
    
    # Удаляем сообщение с кнопкой "отмена"
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    if not server_id or not edit_field:
        await state.clear()
        await message.answer("❌ Ошибка: данные не найдены")
        return
    
    value = message.text.strip()
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await state.clear()
            await message.answer("❌ Сервер не найден")
            return
        
        if edit_field == "name":
            server.name = value
            result_text = f"✅ Имя изменено на: {value}"
        elif edit_field == "max_clients":
            try:
                server.max_clients = int(value)
                result_text = f"✅ Макс. клиентов изменено на: {value}"
            except ValueError:
                await message.answer(
                    "❌ Введите число",
                    reply_markup=get_server_edit_cancel_kb(server_id)
                )
                return
        elif edit_field == "priority":
            try:
                server.priority = int(value)
                result_text = f"✅ Приоритет изменён на: {value}"
            except ValueError:
                await message.answer(
                    "❌ Введите число",
                    reply_markup=get_server_edit_cancel_kb(server_id)
                )
                return
        else:
            await state.clear()
            await message.answer("❌ Неизвестное поле")
            return
        
        await session.commit()
        
        client_count = await WireGuardMultiService.get_server_client_count(session, server_id)
    
    await state.clear()
    
    status = "🟢 Активен" if server.is_active else "🔴 Отключен"
    await message.answer(
        f"{result_text}\n\n"
        f"🖥 *{server.name}*\n\n"
        f"*Хост:* `{server.host}`\n"
        f"*Пароль:* `{server.ssh_password}`\n"
        f"*Статус:* {status}\n"
        f"*Клиентов:* {client_count}/{server.max_clients}\n"
        f"*Приоритет:* {server.priority}",
        parse_mode="Markdown",
        reply_markup=get_server_detail_kb(server_id, server.is_active)
    )


@router.callback_query(F.data.startswith("admin_server_clients_") & ~F.data.contains("page"))
async def admin_server_clients(callback: CallbackQuery):
    """Показать клиентов сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    await callback.answer()
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        # Получаем пользователей с конфигами на этом сервере
        stmt = select(User).join(Config).where(Config.server_id == server_id).distinct()
        result = await session.execute(stmt)
        users = list(result.scalars().all())
    
    if not users:
        await callback.message.edit_text(
            f"👥 *Клиенты сервера {server.name}*\n\n"
            f"На этом сервере пока нет клиентов.",
            parse_mode="Markdown",
            reply_markup=get_server_detail_kb(server_id, server.is_active)
        )
        return
    
    await callback.message.edit_text(
        f"👥 *Клиенты сервера {server.name} ({len(users)}):*",
        parse_mode="Markdown",
        reply_markup=get_server_clients_kb(users, server_id)
    )


@router.callback_query(F.data.startswith("admin_server_clients_page_"))
async def admin_server_clients_page(callback: CallbackQuery):
    """Пагинация списка клиентов сервера"""
    if not is_admin(callback.from_user.id):
        return
    
    # Формат: admin_server_clients_page_{server_id}_{page}
    parts = callback.data.split("_")
    server_id = int(parts[-2])
    page = int(parts[-1])
    await callback.answer()
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            return
        
        stmt = select(User).join(Config).where(Config.server_id == server_id).distinct()
        result = await session.execute(stmt)
        users = list(result.scalars().all())
    
    await callback.message.edit_text(
        f"👥 *Клиенты сервера {server.name} ({len(users)}):*",
        parse_mode="Markdown",
        reply_markup=get_server_clients_kb(users, server_id, page)
    )


@router.callback_query(F.data.startswith("admin_server_broadcast_"))
async def admin_server_broadcast(callback: CallbackQuery, state: FSMContext):
    """Начать рассылку клиентам сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split("_")[-1])
    await callback.answer()
    
    async with async_session() as session:
        server = await WireGuardMultiService.get_server_by_id(session, server_id)
        if not server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        # Считаем клиентов
        stmt = select(User).join(Config).where(Config.server_id == server_id).distinct()
        result = await session.execute(stmt)
        users = list(result.scalars().all())
        client_count = len(users)
    
    if client_count == 0:
        await callback.message.edit_text(
            f"❌ На сервере *{server.name}* нет клиентов для рассылки.",
            parse_mode="Markdown",
            reply_markup=get_server_detail_kb(server_id, server.is_active)
        )
        return
    
    await state.set_state(AdminStates.waiting_for_broadcast_server)
    await state.update_data(
        broadcast_server_id=server_id, 
        broadcast_server_name=server.name,
        broadcast_prompt_msg_id=callback.message.message_id
    )
    
    await callback.message.edit_text(
        f"✉️ *Рассылка клиентам сервера {server.name}*\n\n"
        f"👥 Получателей: {client_count}\n\n"
        f"Отправьте сообщение для рассылки.\n"
        f"Поддерживается:\n"
        f"• Текст\n"
        f"• Фото\n"
        f"• Видео\n"
        f"• Документ\n"
        f"• Голосовое\n"
        f"• Кружок (видеосообщение)",
        parse_mode="Markdown",
        reply_markup=get_server_broadcast_cancel_kb(server_id)
    )


@router.message(AdminStates.waiting_for_broadcast_server)
async def process_broadcast_server(message: Message, state: FSMContext, bot: Bot):
    """Обработка рассылки клиентам сервера"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    server_id = data.get("broadcast_server_id")
    server_name = data.get("broadcast_server_name")
    prompt_msg_id = data.get("broadcast_prompt_msg_id")
    
    if not server_id:
        await state.clear()
        await message.answer("❌ Ошибка: данные сервера не найдены")
        return
    
    # Удаляем сообщение с кнопкой "отмена"
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    # Получаем telegram_id всех клиентов сервера
    async with async_session() as session:
        stmt = select(User.telegram_id).join(Config).where(Config.server_id == server_id).distinct()
        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]
    
    if not user_ids:
        await state.clear()
        await message.answer(
            f"❌ На сервере *{server_name}* нет клиентов.",
            parse_mode="Markdown",
            reply_markup=get_server_detail_kb(server_id, True)
        )
        return
    
    await state.clear()
    
    success = 0
    failed = 0
    
    status_msg = await message.answer(f"⏳ Отправляю сообщение {len(user_ids)} клиентам...")
    
    for user_telegram_id in user_ids:
        try:
            if message.text:
                await bot.send_message(user_telegram_id, message.text)
            elif message.photo:
                await bot.send_photo(user_telegram_id, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await bot.send_video(user_telegram_id, message.video.file_id, caption=message.caption)
            elif message.document:
                await bot.send_document(user_telegram_id, message.document.file_id, caption=message.caption)
            elif message.voice:
                await bot.send_voice(user_telegram_id, message.voice.file_id, caption=message.caption)
            elif message.video_note:
                await bot.send_video_note(user_telegram_id, message.video_note.file_id)
            success += 1
        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {user_telegram_id}: {e}")
            failed += 1
    
    try:
        await status_msg.delete()
    except:
        pass
    
    await message.answer(
        f"✅ *Рассылка по серверу {server_name} завершена!*\n\n"
        f"📨 Отправлено: {success}\n"
        f"❌ Ошибок: {failed}",
        parse_mode="Markdown",
        reply_markup=get_server_detail_kb(server_id, True)
    )


# ===== МИГРАЦИЯ КЛИЕНТОВ =====

@router.callback_query(F.data.startswith("admin_server_migrate_"))
async def admin_server_migrate(callback: CallbackQuery):
    """Начало миграции клиентов с сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    source_server_id = int(callback.data.replace("admin_server_migrate_", ""))
    await callback.answer()
    
    async with async_session() as session:
        # Получаем исходный сервер
        stmt = select(Server).where(Server.id == source_server_id).options(selectinload(Server.configs))
        result = await session.execute(stmt)
        source_server = result.scalar_one_or_none()
        
        if not source_server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        client_count = len(source_server.configs)
        if client_count == 0:
            await callback.message.edit_text(
                "❌ На этом сервере нет клиентов для миграции",
                reply_markup=get_server_detail_kb(source_server_id, source_server.is_active, False)
            )
            return
        
        # Получаем другие активные серверы
        stmt = select(Server).where(
            Server.id != source_server_id,
            Server.is_active == True
        ).options(selectinload(Server.configs))
        result = await session.execute(stmt)
        target_servers = result.scalars().all()
        
        # Фильтруем серверы с достаточным количеством свободных мест
        available_servers = []
        for server in target_servers:
            free_slots = server.max_clients - len(server.configs)
            if free_slots > 0:
                available_servers.append(server)
        
        if not available_servers:
            await callback.message.edit_text(
                "❌ Нет доступных серверов для миграции.\n\n"
                "Все серверы либо отключены, либо заполнены.",
                reply_markup=get_server_detail_kb(source_server_id, source_server.is_active, True)
            )
            return
    
    await callback.message.edit_text(
        f"🔀 *Миграция клиентов*\n\n"
        f"📤 С сервера: *{source_server.name}*\n"
        f"👥 Клиентов: *{client_count}*\n\n"
        f"Выберите сервер, на который перенести клиентов:",
        parse_mode="Markdown",
        reply_markup=get_server_migrate_kb(source_server_id, available_servers)
    )


@router.callback_query(F.data.startswith("admin_migrate_to_"))
async def admin_migrate_select_target(callback: CallbackQuery):
    """Выбор целевого сервера для миграции"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    # Формат: admin_migrate_to_{source_id}_{target_id}
    parts = callback.data.replace("admin_migrate_to_", "").split("_")
    source_id = int(parts[0])
    target_id = int(parts[1])
    await callback.answer()
    
    async with async_session() as session:
        # Получаем оба сервера
        stmt = select(Server).where(Server.id == source_id).options(selectinload(Server.configs))
        result = await session.execute(stmt)
        source_server = result.scalar_one_or_none()
        
        stmt = select(Server).where(Server.id == target_id).options(selectinload(Server.configs))
        result = await session.execute(stmt)
        target_server = result.scalar_one_or_none()
        
        if not source_server or not target_server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        source_clients = len(source_server.configs)
        target_free = target_server.max_clients - len(target_server.configs)
        
        # Проверяем что хватает места
        can_migrate = min(source_clients, target_free)
        
        if can_migrate == 0:
            await callback.message.edit_text(
                f"❌ На сервере *{target_server.name}* нет свободных мест",
                parse_mode="Markdown",
                reply_markup=get_server_detail_kb(source_id, source_server.is_active, True)
            )
            return
        
        warning = ""
        if can_migrate < source_clients:
            warning = f"\n\n⚠️ *Внимание:* на целевом сервере только {target_free} мест, будет перенесено {can_migrate} из {source_clients} клиентов."
    
    await callback.message.edit_text(
        f"🔀 *Подтверждение миграции*\n\n"
        f"📤 С сервера: *{source_server.name}*\n"
        f"📥 На сервер: *{target_server.name}*\n"
        f"👥 Клиентов: *{can_migrate}*\n"
        f"{warning}\n\n"
        f"⚠️ Клиентам будут отправлены новые конфиги.\n"
        f"Старые конфиги перестанут работать.",
        parse_mode="Markdown",
        reply_markup=get_migrate_confirm_kb(source_id, target_id, can_migrate)
    )


@router.callback_query(F.data.startswith("admin_migrate_confirm_"))
async def admin_migrate_confirm(callback: CallbackQuery, bot: Bot):
    """Выполнение миграции клиентов"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    # Формат: admin_migrate_confirm_{source_id}_{target_id}
    parts = callback.data.replace("admin_migrate_confirm_", "").split("_")
    source_id = int(parts[0])
    target_id = int(parts[1])
    
    await callback.answer("⏳ Миграция началась...")
    await callback.message.edit_text("⏳ *Миграция в процессе...*\n\nЭто может занять несколько минут.", parse_mode="Markdown")
    
    migrated = 0
    failed = 0
    notified = 0
    
    async with async_session() as session:
        # Получаем серверы
        stmt = select(Server).where(Server.id == source_id).options(selectinload(Server.configs))
        result = await session.execute(stmt)
        source_server = result.scalar_one_or_none()
        
        stmt = select(Server).where(Server.id == target_id)
        result = await session.execute(stmt)
        target_server = result.scalar_one_or_none()
        
        if not source_server or not target_server:
            await callback.message.edit_text("❌ Сервер не найден")
            return
        
        # Получаем конфиги с пользователями
        configs_to_migrate = []
        for config in source_server.configs:
            stmt = select(Config).where(Config.id == config.id).options(selectinload(Config.user))
            result = await session.execute(stmt)
            config_with_user = result.scalar_one_or_none()
            if config_with_user:
                configs_to_migrate.append(config_with_user)
        
        target_free = target_server.max_clients - await WireGuardMultiService.get_server_client_count(session, target_id)
        
        for config in configs_to_migrate[:target_free]:
            try:
                user = config.user
                config_name = config.name
                
                # 1. Удаляем старый конфиг с исходного сервера
                try:
                    await WireGuardMultiService.delete_config_from_server(
                        source_server.host,
                        source_server.ssh_user,
                        source_server.ssh_password,
                        source_server.ssh_port,
                        config.public_key,
                        source_server.wg_interface
                    )
                except Exception as e:
                    logger.warning(f"Ошибка удаления конфига с исходного сервера: {e}")
                
                # 2. Создаём новый конфиг на целевом сервере
                success, new_config_data, msg = await WireGuardMultiService.create_config(config_name, session, target_server)
                
                if not success:
                    logger.error(f"Ошибка создания конфига на целевом сервере: {msg}")
                    failed += 1
                    continue
                
                # 3. Обновляем запись в БД
                config.server_id = target_id
                config.public_key = new_config_data.public_key
                config.preshared_key = new_config_data.preshared_key
                config.allowed_ips = new_config_data.allowed_ips
                config.client_ip = new_config_data.client_ip
                
                await session.commit()
                migrated += 1
                
                # 4. Уведомляем пользователя и отправляем новый конфиг
                if user and user.telegram_id:
                    try:
                        await bot.send_message(
                            user.telegram_id,
                            f"🔄 *Обновление конфига*\n\n"
                            f"Твой конфиг *{config_name}* был перенесён на новый сервер.\n"
                            f"Старый конфиг больше не работает.\n\n"
                            f"Сейчас отправлю новый конфиг 👇",
                            parse_mode="Markdown"
                        )
                        
                        # Отправляем новый конфиг с обычной клавиатурой клиента
                        from handlers.user import send_config_file
                        from keyboards.user_kb import get_main_menu_kb
                        await send_config_file(
                            bot, user.telegram_id, config_name, new_config_data, target_id,
                            caption="📄 Твой новый WireGuard конфиг",
                            reply_markup=get_main_menu_kb(user.telegram_id, True, True)
                        )
                        notified += 1
                    except Exception as e:
                        logger.error(f"Ошибка уведомления пользователя {user.telegram_id}: {e}")
                
            except Exception as e:
                logger.error(f"Ошибка миграции конфига {config.name}: {e}")
                failed += 1
    
    # Результат
    await callback.message.edit_text(
        f"✅ *Миграция завершена*\n\n"
        f"📤 С сервера: *{source_server.name}*\n"
        f"📥 На сервер: *{target_server.name}*\n\n"
        f"✅ Перенесено: {migrated}\n"
        f"❌ Ошибок: {failed}\n"
        f"📨 Уведомлено: {notified}",
        parse_mode="Markdown",
        reply_markup=get_server_detail_kb(target_id, target_server.is_active, migrated > 0)
    )


@router.callback_query(F.data.startswith("admin_srvuser_") & ~F.data.startswith("admin_srvuser_configs_"))
async def admin_server_user_detail(callback: CallbackQuery):
    """Детальная информация о пользователе (из списка клиентов сервера)"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    # Формат: admin_srvuser_{server_id}_{user_id}
    parts = callback.data.split("_")
    server_id = int(parts[2])
    user_id = int(parts[3])
    await callback.answer()
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(
            selectinload(User.configs),
            selectinload(User.subscriptions)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.message.edit_text("❌ Пользователь не найден")
            return
        
        # Считаем активную подписку
        active_sub = None
        if user.subscriptions:
            for sub in user.subscriptions:
                if sub.expires_at and sub.expires_at > datetime.utcnow():
                    active_sub = sub
                    break
        
        configs_count = len(user.configs) if user.configs else 0
        
        # Формируем текст
        name = f"@{user.username}" if user.username else user.full_name
        phone_info = f"📞 {user.phone}" if user.phone and user.phone != "5553535" else "📞 не указан"
        
        # Считаем оставшиеся дни из подписок
        days_left = 0
        is_unlimited = False
        if user.subscriptions:
            for sub in user.subscriptions:
                if sub.expires_at is None:
                    # Бессрочная подписка
                    is_unlimited = True
                    break
                elif sub.expires_at > datetime.utcnow():
                    sub_days = (sub.expires_at - datetime.utcnow()).days
                    if sub_days > days_left:
                        days_left = sub_days
        
        if is_unlimited:
            days_info = "♾ Бессрочная"
        elif days_left > 0:
            days_info = f"✅ {days_left} дн."
        else:
            days_info = "❌ 0 дн."
        
        text = (
            f"👤 *{name}*\n\n"
            f"🆔 ID: `{user.telegram_id}`\n"
            f"{phone_info}\n"
            f"📱 Конфигов: {configs_count}\n"
            f"📊 Осталось: {days_info}\n"
            f"📅 Регистрация: {format_date_moscow(user.created_at)}"
        )
        
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_server_user_detail_kb(user_id, server_id)
        )


@router.callback_query(F.data.startswith("admin_srvuser_configs_"))
async def admin_server_user_configs(callback: CallbackQuery):
    """Список конфигов пользователя (из контекста сервера) - только конфиги с этого сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    # Формат: admin_srvuser_configs_{server_id}_{user_id}
    parts = callback.data.split("_")
    server_id = int(parts[3])
    user_id = int(parts[4])
    await callback.answer()
    
    async with async_session() as session:
        # Получаем только конфиги пользователя с этого сервера
        stmt = select(Config).where(Config.user_id == user_id, Config.server_id == server_id)
        result = await session.execute(stmt)
        configs = list(result.scalars().all())
    
    if not configs:
        await callback.answer("У пользователя нет конфигов на этом сервере", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"📱 *Конфиги пользователя на сервере ({len(configs)}):*",
        parse_mode="Markdown",
        reply_markup=get_server_user_configs_kb(configs, user_id, server_id)
    )


@router.callback_query(F.data.startswith("admin_srvcfg_"))
async def admin_server_config_detail(callback: CallbackQuery):
    """Детальная информация о конфиге (из контекста сервера)"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    # Формат: admin_srvcfg_{server_id}_{config_id}
    parts = callback.data.split("_")
    server_id = int(parts[2])
    config_id = int(parts[3])
    await callback.answer()
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        # Получаем информацию о сервере конфига
        server_deleted = False
        if config.server_id:
            cfg_server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
            if cfg_server:
                server_name = cfg_server.name
            else:
                server_name = "⚠️ Сервер удалён"
                server_deleted = True
        else:
            server_name = "⚠️ Сервер бессрочно выбыл из работы"
            server_deleted = True
        
        if server_deleted:
            status = "🔴 Отключен"
        else:
            status = "🟢 Активен" if config.is_active else "🔴 Отключен"
        
        traffic_info = ""
        if not LOCAL_MODE and not server_deleted and cfg_server:
            traffic_stats = await WireGuardMultiService.get_traffic_stats(cfg_server)
            if config.public_key in traffic_stats:
                stats = traffic_stats[config.public_key]
                rx = format_bytes(stats['received'])
                tx = format_bytes(stats['sent'])
                traffic_info = f"\n📊 Трафик: ⬇️{rx} ⬆️{tx}"
        
        server_warning = ""
        if server_deleted:
            server_warning = "\n\n⚠️ *Этот конфиг больше не работает.*\nСервер бессрочно выбыл из работы."
        
        await callback.message.edit_text(
            f"📱 *Конфиг: {config.name}*\n\n"
            f"Статус: {status}\n"
            f"🌍 Сервер: {server_name}\n"
            f"IP: `{config.client_ip}`\n"
            f"Создан: {format_date_moscow(config.created_at)}"
            f"{traffic_info}"
            f"{server_warning}",
            parse_mode="Markdown",
            reply_markup=get_server_config_detail_kb(config.id, config.user_id, server_id, config.is_active, server_deleted)
        )


@router.callback_query(F.data.startswith("admin_toggle_srvcfg_"))
async def admin_toggle_server_config(callback: CallbackQuery):
    """Включить/отключить конфиг (из контекста сервера)"""
    if not is_admin(callback.from_user.id):
        return
    
    # Формат: admin_toggle_srvcfg_{server_id}_{config_id}
    parts = callback.data.split("_")
    server_id = int(parts[3])
    config_id = int(parts[4])
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id)
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        # Получаем сервер для операций
        cfg_server = None
        if config.server_id:
            cfg_server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
        
        if config.is_active:
            # Отключаем конфиг
            if cfg_server:
                success, msg = await WireGuardMultiService.disable_config(config.public_key, cfg_server)
            else:
                success, msg = await WireGuardService.disable_config(config.public_key)
            
            if success:
                config.is_active = False
                await session.commit()
                await callback.answer("🔴 Конфиг отключен")
            else:
                await callback.answer(f"Ошибка: {msg}", show_alert=True)
                return
        else:
            # Включаем конфиг
            if cfg_server:
                success, msg = await WireGuardMultiService.enable_config(
                    config.public_key, config.preshared_key, config.allowed_ips, cfg_server
                )
            elif config.server_id:
                await callback.answer("❌ Сервер удалён, конфиг нельзя включить", show_alert=True)
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
        
        # Обновляем сообщение
        server_deleted = False
        if config.server_id:
            cfg_server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
            if cfg_server:
                server_name = cfg_server.name
            else:
                server_name = "⚠️ Сервер удалён"
                server_deleted = True
        else:
            server_name = "⚠️ Сервер бессрочно выбыл из работы"
            server_deleted = True
        
        status = "🟢 Активен" if config.is_active else "🔴 Отключен"
        
        await callback.message.edit_text(
            f"📱 *Конфиг: {config.name}*\n\n"
            f"Статус: {status}\n"
            f"🌍 Сервер: {server_name}\n"
            f"IP: `{config.client_ip}`\n"
            f"Создан: {format_date_moscow(config.created_at)}",
            parse_mode="Markdown",
            reply_markup=get_server_config_detail_kb(config.id, config.user_id, server_id, config.is_active, server_deleted)
        )


@router.callback_query(F.data.startswith("admin_delete_srvcfg_"))
async def admin_delete_server_config(callback: CallbackQuery):
    """Удалить конфиг (из контекста сервера)"""
    if not is_admin(callback.from_user.id):
        return
    
    # Формат: admin_delete_srvcfg_{server_id}_{config_id}
    parts = callback.data.split("_")
    server_id = int(parts[3])
    config_id = int(parts[4])
    
    async with async_session() as session:
        stmt = select(Config).where(Config.id == config_id).options(selectinload(Config.user))
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        
        if not config:
            await callback.answer("Конфиг не найден", show_alert=True)
            return
        
        user_id = config.user_id
        config_name = config.name
        
        # Удаляем с правильного сервера
        if not LOCAL_MODE:
            if config.server_id:
                cfg_server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                if cfg_server:
                    await WireGuardMultiService.delete_config(config_name, cfg_server, config.public_key)
            else:
                await WireGuardService.delete_config(config_name)
        
        # Удаляем из БД
        await session.delete(config)
        await session.commit()
        
        await callback.answer(f"✅ Конфиг {config_name} удалён")
        
        # Возвращаемся к списку конфигов пользователя
        stmt = select(User).where(User.id == user_id).options(selectinload(User.configs))
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user and user.configs:
            await callback.message.edit_text(
                f"📱 *Конфиги пользователя #{user.id}:*",
                parse_mode="Markdown",
                reply_markup=get_server_user_configs_kb(user.configs, user.id, server_id)
            )
        else:
            # Нет больше конфигов - возвращаемся к пользователю
            await callback.message.edit_text(
                f"📱 У пользователя больше нет конфигов",
                parse_mode="Markdown",
                reply_markup=get_server_user_detail_kb(user_id, server_id)
            )


# ===== РЕФЕРАЛЫ =====

@router.callback_query(F.data == "admin_referrals")
async def admin_referrals(callback: CallbackQuery):
    """Список пользователей с рефералами"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    
    async with async_session() as session:
        # Получаем пользователей у которых есть рефералы
        stmt = select(User).options(selectinload(User.referrals)).order_by(User.referral_balance.desc())
        result = await session.execute(stmt)
        all_users = result.scalars().all()
        
        # Фильтруем только тех у кого есть рефералы или баланс
        users_with_referrals = [u for u in all_users if (u.referrals and len(u.referrals) > 0) or u.referral_balance > 0]
    
    if not users_with_referrals:
        await callback.message.edit_text(
            "📭 Пока нет пользователей с рефералами",
            reply_markup=get_admin_menu_kb()
        )
        return
    
    await callback.message.edit_text(
        f"👥 *Рефералы ({len(users_with_referrals)}):*\n\n"
        f"Пользователи с приглашёнными друзьями:",
        parse_mode="Markdown",
        reply_markup=get_referrals_list_kb(users_with_referrals)
    )


@router.callback_query(F.data.startswith("admin_referrals_page_"))
async def admin_referrals_page(callback: CallbackQuery):
    """Пагинация списка рефералов"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    page = int(callback.data.replace("admin_referrals_page_", ""))
    
    async with async_session() as session:
        stmt = select(User).options(selectinload(User.referrals)).order_by(User.referral_balance.desc())
        result = await session.execute(stmt)
        all_users = result.scalars().all()
        users_with_referrals = [u for u in all_users if (u.referrals and len(u.referrals) > 0) or u.referral_balance > 0]
    
    await callback.message.edit_reply_markup(
        reply_markup=get_referrals_list_kb(users_with_referrals, page)
    )


@router.callback_query(F.data.startswith("admin_referral_") & ~F.data.contains("percent"))
async def admin_referral_detail(callback: CallbackQuery):
    """Детальная информация о реферале"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    user_id = int(callback.data.replace("admin_referral_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id).options(
            selectinload(User.referrals).selectinload(User.payments)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        referral_count = len(user.referrals) if user.referrals else 0
        
        # Сумма оплат рефералов
        total_payments = 0
        for ref in (user.referrals or []):
            for payment in (ref.payments or []):
                if payment.status == "approved":
                    total_payments += payment.amount
        
        username = f"@{user.username}" if user.username else user.full_name
        
        await callback.message.edit_text(
            f"👤 *Реферал: {username}*\n\n"
            f"🆔 ID: `{user.telegram_id}`\n"
            f"👥 Приглашено: {referral_count} чел.\n"
            f"💰 Оплаты рефералов: {int(total_payments)}₽\n"
            f"📊 Процент: {int(user.referral_percent)}%\n"
            f"💵 Накоплено: {int(user.referral_balance)}₽",
            parse_mode="Markdown",
            reply_markup=get_referral_detail_kb(user_id)
        )


@router.callback_query(F.data.startswith("admin_referral_percent_"))
async def admin_referral_percent(callback: CallbackQuery, state: FSMContext):
    """Изменение процента реферала"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    user_id = int(callback.data.replace("admin_referral_percent_", ""))
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        
        current_percent = user.referral_percent
    
    await state.set_state(AdminStates.waiting_for_referral_percent)
    await state.update_data(user_id=user_id, prompt_msg_id=callback.message.message_id)
    
    await callback.message.edit_text(
        f"📊 *Изменение процента*\n\n"
        f"Текущий процент: {int(current_percent)}%\n\n"
        f"Введи новый процент (от 1 до 100):",
        parse_mode="Markdown",
        reply_markup=get_referral_percent_cancel_kb(user_id)
    )


@router.message(AdminStates.waiting_for_referral_percent)
async def process_referral_percent(message: Message, state: FSMContext, bot: Bot):
    """Обработка ввода процента реферала"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    user_id = data.get("user_id")
    prompt_msg_id = data.get("prompt_msg_id")
    
    if not user_id:
        await state.clear()
        await message.answer("❌ Ошибка: данные не найдены")
        return
    
    try:
        percent = float(message.text.strip().replace(",", "."))
        if percent < 1 or percent > 100:
            raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Введи число от 1 до 100",
            reply_markup=get_referral_percent_cancel_kb(user_id)
        )
        return
    
    # Удаляем сообщение с кнопкой "отмена"
    if prompt_msg_id:
        try:
            await bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    async with async_session() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            await state.clear()
            await message.answer("❌ Пользователь не найден")
            return
        
        user.referral_percent = percent
        await session.commit()
    
    await state.clear()
    
    await message.answer(
        f"✅ Процент установлен: {int(percent)}%",
        reply_markup=get_referral_detail_kb(user_id)
    )


# ===== ЗАЯВКИ НА ВЫВОД =====

@router.callback_query(F.data == "admin_withdrawals")
async def admin_withdrawals(callback: CallbackQuery):
    """Список заявок на вывод"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    
    async with async_session() as session:
        stmt = select(WithdrawalRequest).where(WithdrawalRequest.status == "pending").options(
            selectinload(WithdrawalRequest.user)
        ).order_by(WithdrawalRequest.created_at.desc())
        result = await session.execute(stmt)
        withdrawals = result.scalars().all()
    
    if not withdrawals:
        await callback.message.edit_text(
            "✅ Нет заявок на вывод",
            reply_markup=get_admin_menu_kb()
        )
        return
    
    await callback.message.edit_text(
        f"💸 *Заявки на вывод ({len(withdrawals)}):*",
        parse_mode="Markdown",
        reply_markup=get_withdrawals_list_kb(withdrawals)
    )


@router.callback_query(F.data.startswith("admin_withdrawal_") & ~F.data.contains("complete") & ~F.data.contains("cancel"))
async def admin_withdrawal_detail(callback: CallbackQuery):
    """Детальная информация о заявке на вывод"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    withdrawal_id = int(callback.data.replace("admin_withdrawal_", ""))
    
    async with async_session() as session:
        stmt = select(WithdrawalRequest).where(WithdrawalRequest.id == withdrawal_id).options(
            selectinload(WithdrawalRequest.user)
        )
        result = await session.execute(stmt)
        withdrawal = result.scalar_one_or_none()
        
        if not withdrawal:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        
        user = withdrawal.user
        username = f"@{user.username}" if user.username else user.full_name
        
        await callback.message.edit_text(
            f"💸 *Заявка на вывод #{withdrawal.id}*\n\n"
            f"👤 Пользователь: {username}\n"
            f"🆔 ID: `{user.telegram_id}`\n"
            f"💰 Сумма: {int(withdrawal.amount)}₽\n"
            f"🏦 Банк: {withdrawal.bank}\n"
            f"📱 Телефон: `{withdrawal.phone}`\n"
            f"📅 Дата: {format_datetime_moscow(withdrawal.created_at)}",
            parse_mode="Markdown",
            reply_markup=get_withdrawal_review_kb(withdrawal_id)
        )


@router.callback_query(F.data.startswith("admin_withdrawal_complete_"))
async def admin_withdrawal_complete(callback: CallbackQuery, bot: Bot):
    """Подтверждение вывода средств"""
    if not is_admin(callback.from_user.id):
        return
    
    withdrawal_id = int(callback.data.replace("admin_withdrawal_complete_", ""))
    
    async with async_session() as session:
        stmt = select(WithdrawalRequest).where(WithdrawalRequest.id == withdrawal_id).options(
            selectinload(WithdrawalRequest.user)
        )
        result = await session.execute(stmt)
        withdrawal = result.scalar_one_or_none()
        
        if not withdrawal:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        
        if withdrawal.status != "pending":
            await callback.answer("Заявка уже обработана", show_alert=True)
            return
        
        user_telegram_id = withdrawal.user.telegram_id
        amount = withdrawal.amount
        
        withdrawal.status = "completed"
        withdrawal.processed_at = datetime.utcnow()
        await session.commit()
    
    await callback.answer("✅ Вывод подтверждён")
    
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ *ВЫПОЛНЕНО*",
            parse_mode="Markdown"
        )
    except:
        pass
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            user_telegram_id,
            f"✅ *Вывод средств выполнен!*\n\n"
            f"💰 Сумма: {int(amount)}₽\n\n"
            f"Спасибо за участие в реферальной программе! 🎉",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления о выводе: {e}")


@router.callback_query(F.data.startswith("admin_withdrawal_cancel_"))
async def admin_withdrawal_cancel(callback: CallbackQuery, bot: Bot):
    """Отмена вывода средств"""
    if not is_admin(callback.from_user.id):
        return
    
    withdrawal_id = int(callback.data.replace("admin_withdrawal_cancel_", ""))
    
    async with async_session() as session:
        stmt = select(WithdrawalRequest).where(WithdrawalRequest.id == withdrawal_id).options(
            selectinload(WithdrawalRequest.user)
        )
        result = await session.execute(stmt)
        withdrawal = result.scalar_one_or_none()
        
        if not withdrawal:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        
        if withdrawal.status != "pending":
            await callback.answer("Заявка уже обработана", show_alert=True)
            return
        
        user_telegram_id = withdrawal.user.telegram_id
        user_id = withdrawal.user.id
        amount = withdrawal.amount
        
        # Возвращаем средства на баланс
        stmt_user = select(User).where(User.id == user_id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()
        if user:
            user.referral_balance += amount
        
        withdrawal.status = "cancelled"
        withdrawal.processed_at = datetime.utcnow()
        await session.commit()
    
    await callback.answer("❌ Вывод отменён")
    
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ *ОТМЕНЕНО*",
            parse_mode="Markdown"
        )
    except:
        pass
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            user_telegram_id,
            f"❌ *Вывод средств отменён*\n\n"
            f"Произошла ошибка при выводе.\n"
            f"Средства возвращены на баланс.\n\n"
            f"Свяжитесь с @agdelesha для уточнения.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления об отмене вывода: {e}")


# ===== УПРАВЛЕНИЕ БОТАМИ =====

@router.callback_query(F.data == "settings_bots")
async def settings_bots(callback: CallbackQuery):
    """Список ботов"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    from services.settings import get_all_bots
    from keyboards.admin_kb import get_bots_list_kb
    
    bots = await get_all_bots()
    
    await callback.message.edit_text(
        f"🤖 *Управление ботами*\n\n"
        f"Всего ботов: {len(bots)}\n\n"
        f"Выберите бота для настройки или добавьте нового:",
        parse_mode="Markdown",
        reply_markup=get_bots_list_kb(bots)
    )


@router.callback_query(F.data.startswith("bot_settings_"))
async def bot_settings_detail(callback: CallbackQuery):
    """Настройки конкретного бота"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    bot_id = int(callback.data.replace("bot_settings_", ""))
    
    from services.settings import get_bot_instance
    from keyboards.admin_kb import get_bot_settings_kb
    
    bot = await get_bot_instance(bot_id)
    if not bot:
        await callback.answer("Бот не найден", show_alert=True)
        return
    
    pwd_text = f"`{bot.password}`" if bot.password else "не установлен"
    channel_text = f"@{bot.channel}" if bot.channel else "не установлен"
    phone_text = "Да" if bot.require_phone else "Нет"
    
    await callback.message.edit_text(
        f"🤖 *Настройки бота @{bot.username}*\n\n"
        f"🔑 Пароль: {pwd_text}\n"
        f"📢 Канал: {channel_text}\n"
        f"📱 Запрос телефона: {phone_text}\n"
        f"📋 Макс. конфигов: {bot.max_configs}\n"
        f"Статус: {'🟢 Активен' if bot.is_active else '🔴 Отключен'}",
        parse_mode="Markdown",
        reply_markup=get_bot_settings_kb(bot_id, bot)
    )


@router.callback_query(F.data == "bot_add")
async def bot_add(callback: CallbackQuery, state: FSMContext):
    """Добавить нового бота"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    from keyboards.admin_kb import get_bot_add_cancel_kb
    
    msg = await callback.message.edit_text(
        "🤖 *Добавление нового бота*\n\n"
        "Отправьте токен бота (получите у @BotFather):",
        parse_mode="Markdown",
        reply_markup=get_bot_add_cancel_kb()
    )
    
    await state.set_state(AdminStates.waiting_for_bot_token)
    await state.update_data(prompt_msg_id=msg.message_id)


@router.message(AdminStates.waiting_for_bot_token)
async def process_bot_token(message: Message, state: FSMContext):
    """Обработка токена нового бота"""
    if not is_admin(message.from_user.id):
        return
    
    token = message.text.strip()
    
    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except:
        pass
    
    # Удаляем промпт
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    # Проверяем токен
    try:
        from aiogram import Bot
        test_bot = Bot(token=token)
        bot_info = await test_bot.get_me()
        await test_bot.session.close()
        
        # Проверяем, не добавлен ли уже
        from services.settings import get_bot_instance, add_bot_instance
        existing = await get_bot_instance(bot_info.id)
        if existing:
            await message.answer(
                f"❌ Бот @{bot_info.username} уже добавлен!",
                parse_mode="Markdown"
            )
            await state.clear()
            return
        
        # Добавляем бота
        await add_bot_instance(token, bot_info.id, bot_info.username, bot_info.first_name)
        
        await message.answer(
            f"✅ *Бот добавлен!*\n\n"
            f"@{bot_info.username}\n\n"
            f"🔄 Перезапускаю сервис...",
            parse_mode="Markdown"
        )
        
        # Автоматически перезапускаем сервис
        import subprocess
        try:
            subprocess.Popen(["systemctl", "restart", "vpn-bot"], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as restart_err:
            logger.error(f"Ошибка перезапуска сервиса: {restart_err}")
        
    except Exception as e:
        await message.answer(
            f"❌ Ошибка: неверный токен\n\n{str(e)[:100]}",
            parse_mode="Markdown"
        )
    
    await state.clear()


@router.callback_query(F.data.startswith("bot_password_"))
async def bot_password_menu(callback: CallbackQuery, state: FSMContext):
    """Меню пароля бота"""
    if not is_admin(callback.from_user.id):
        return
    
    action = callback.data.replace("bot_password_", "")
    
    if action.startswith("set_"):
        # Установить пароль
        bot_id = int(action.replace("set_", ""))
        await callback.answer()
        from keyboards.admin_kb import get_bot_input_cancel_kb
        
        msg = await callback.message.edit_text(
            "🔑 Введите новый пароль для бота:",
            parse_mode="Markdown",
            reply_markup=get_bot_input_cancel_kb(bot_id, "settings")
        )
        await state.set_state(AdminStates.waiting_for_bot_password)
        await state.update_data(bot_id=bot_id, prompt_msg_id=msg.message_id)
        
    elif action.startswith("remove_"):
        # Убрать пароль
        bot_id = int(action.replace("remove_", ""))
        from services.settings import update_bot_setting
        await update_bot_setting(bot_id, "password", None)
        await callback.answer("✅ Пароль убран")
        
        # Возвращаемся к настройкам бота
        from services.settings import get_bot_instance
        from keyboards.admin_kb import get_bot_settings_kb
        bot = await get_bot_instance(bot_id)
        await callback.message.edit_text(
            f"🤖 *Настройки бота @{bot.username}*\n\n"
            f"🔑 Пароль: не установлен\n"
            f"📢 Канал: {'@' + bot.channel if bot.channel else 'не установлен'}\n"
            f"📱 Запрос телефона: {'Да' if bot.require_phone else 'Нет'}\n"
            f"📋 Макс. конфигов: {bot.max_configs}",
            parse_mode="Markdown",
            reply_markup=get_bot_settings_kb(bot_id, bot)
        )
    else:
        # Меню пароля
        bot_id = int(action)
        await callback.answer()
        from services.settings import get_bot_instance
        from keyboards.admin_kb import get_bot_password_kb
        
        bot = await get_bot_instance(bot_id)
        pwd_text = f"`{bot.password}`" if bot.password else "не установлен"
        
        await callback.message.edit_text(
            f"🔑 *Пароль бота @{bot.username}*\n\n"
            f"Текущий пароль: {pwd_text}",
            parse_mode="Markdown",
            reply_markup=get_bot_password_kb(bot_id, bool(bot.password))
        )


@router.message(AdminStates.waiting_for_bot_password)
async def process_bot_password(message: Message, state: FSMContext):
    """Обработка нового пароля бота"""
    if not is_admin(message.from_user.id):
        return
    
    password = message.text.strip()
    data = await state.get_data()
    bot_id = data.get("bot_id")
    
    # Удаляем сообщения
    try:
        await message.delete()
    except:
        pass
    
    prompt_msg_id = data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    from services.settings import update_bot_setting, get_bot_instance
    from keyboards.admin_kb import get_bot_settings_kb
    
    await update_bot_setting(bot_id, "password", password)
    await state.clear()
    
    bot = await get_bot_instance(bot_id)
    await message.answer(
        f"✅ Пароль установлен: `{password}`\n\n"
        f"🤖 *Настройки бота @{bot.username}*",
        parse_mode="Markdown",
        reply_markup=get_bot_settings_kb(bot_id, bot)
    )


@router.callback_query(F.data.startswith("bot_channel_"))
async def bot_channel_menu(callback: CallbackQuery, state: FSMContext):
    """Меню канала бота"""
    if not is_admin(callback.from_user.id):
        return
    
    action = callback.data.replace("bot_channel_", "")
    
    if action.startswith("set_"):
        bot_id = int(action.replace("set_", ""))
        await callback.answer()
        from keyboards.admin_kb import get_bot_input_cancel_kb
        
        msg = await callback.message.edit_text(
            "📢 Введите username канала (без @):",
            parse_mode="Markdown",
            reply_markup=get_bot_input_cancel_kb(bot_id, "settings")
        )
        await state.set_state(AdminStates.waiting_for_bot_channel)
        await state.update_data(bot_id=bot_id, prompt_msg_id=msg.message_id)
        
    elif action.startswith("remove_"):
        bot_id = int(action.replace("remove_", ""))
        from services.settings import update_bot_setting, get_bot_instance
        from keyboards.admin_kb import get_bot_settings_kb
        
        await update_bot_setting(bot_id, "channel", None)
        await callback.answer("✅ Канал убран")
        
        bot = await get_bot_instance(bot_id)
        await callback.message.edit_text(
            f"🤖 *Настройки бота @{bot.username}*",
            parse_mode="Markdown",
            reply_markup=get_bot_settings_kb(bot_id, bot)
        )
    else:
        bot_id = int(action)
        await callback.answer()
        from services.settings import get_bot_instance
        from keyboards.admin_kb import get_bot_channel_kb
        
        bot = await get_bot_instance(bot_id)
        channel_text = f"@{bot.channel}" if bot.channel else "не установлен"
        
        await callback.message.edit_text(
            f"📢 *Канал бота @{bot.username}*\n\n"
            f"Текущий канал: {channel_text}",
            parse_mode="Markdown",
            reply_markup=get_bot_channel_kb(bot_id, bool(bot.channel))
        )


@router.message(AdminStates.waiting_for_bot_channel)
async def process_bot_channel(message: Message, state: FSMContext):
    """Обработка канала бота"""
    if not is_admin(message.from_user.id):
        return
    
    channel = message.text.strip().replace("@", "")
    data = await state.get_data()
    bot_id = data.get("bot_id")
    
    try:
        await message.delete()
    except:
        pass
    
    prompt_msg_id = data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    from services.settings import update_bot_setting, get_bot_instance
    from keyboards.admin_kb import get_bot_settings_kb
    
    await update_bot_setting(bot_id, "channel", channel)
    await state.clear()
    
    bot = await get_bot_instance(bot_id)
    await message.answer(
        f"✅ Канал установлен: @{channel}\n\n"
        f"🤖 *Настройки бота @{bot.username}*",
        parse_mode="Markdown",
        reply_markup=get_bot_settings_kb(bot_id, bot)
    )


@router.callback_query(F.data.startswith("bot_phone_"))
async def bot_phone_toggle(callback: CallbackQuery):
    """Переключение запроса телефона"""
    if not is_admin(callback.from_user.id):
        return
    
    bot_id = int(callback.data.replace("bot_phone_", ""))
    
    from services.settings import get_bot_instance, update_bot_setting
    from keyboards.admin_kb import get_bot_settings_kb
    
    bot = await get_bot_instance(bot_id)
    new_value = not bot.require_phone
    await update_bot_setting(bot_id, "require_phone", new_value)
    
    await callback.answer(f"✅ Запрос телефона: {'Вкл' if new_value else 'Выкл'}")
    
    bot = await get_bot_instance(bot_id)
    await callback.message.edit_text(
        f"🤖 *Настройки бота @{bot.username}*\n\n"
        f"🔑 Пароль: {'`' + bot.password + '`' if bot.password else 'не установлен'}\n"
        f"📢 Канал: {'@' + bot.channel if bot.channel else 'не установлен'}\n"
        f"📱 Запрос телефона: {'Да' if bot.require_phone else 'Нет'}\n"
        f"📋 Макс. конфигов: {bot.max_configs}",
        parse_mode="Markdown",
        reply_markup=get_bot_settings_kb(bot_id, bot)
    )


@router.callback_query(F.data.startswith("bot_toggle_"))
async def bot_toggle_active(callback: CallbackQuery):
    """Включение/выключение бота"""
    if not is_admin(callback.from_user.id):
        return
    
    bot_id = int(callback.data.replace("bot_toggle_", ""))
    
    from services.settings import get_bot_instance, update_bot_setting
    from keyboards.admin_kb import get_bot_settings_kb
    
    bot = await get_bot_instance(bot_id)
    new_value = not bot.is_active
    await update_bot_setting(bot_id, "is_active", new_value)
    
    await callback.answer(f"✅ Бот {'активирован' if new_value else 'отключен'}")
    
    bot = await get_bot_instance(bot_id)
    await callback.message.edit_text(
        f"🤖 *Настройки бота @{bot.username}*\n\n"
        f"Статус: {'🟢 Активен' if bot.is_active else '🔴 Отключен'}\n\n"
        f"⚠️ Перезапустите сервис для применения",
        parse_mode="Markdown",
        reply_markup=get_bot_settings_kb(bot_id, bot)
    )


@router.callback_query(F.data.startswith("bot_delete_"))
async def bot_delete(callback: CallbackQuery):
    """Удаление бота"""
    if not is_admin(callback.from_user.id):
        return
    
    action = callback.data.replace("bot_delete_", "")
    
    if action.startswith("confirm_"):
        bot_id = int(action.replace("confirm_", ""))
        from services.settings import delete_bot_instance, get_all_bots
        from keyboards.admin_kb import get_bots_list_kb
        
        await delete_bot_instance(bot_id)
        await callback.answer("✅ Бот удалён")
        
        bots = await get_all_bots()
        await callback.message.edit_text(
            f"🤖 *Управление ботами*\n\n"
            f"Бот удалён. Всего ботов: {len(bots)}",
            parse_mode="Markdown",
            reply_markup=get_bots_list_kb(bots)
        )
    else:
        bot_id = int(action)
        await callback.answer()
        from services.settings import get_bot_instance
        from keyboards.admin_kb import get_bot_delete_confirm_kb
        
        bot = await get_bot_instance(bot_id)
        await callback.message.edit_text(
            f"🗑 *Удаление бота @{bot.username}*\n\n"
            f"Вы уверены? Это действие нельзя отменить.",
            parse_mode="Markdown",
            reply_markup=get_bot_delete_confirm_kb(bot_id)
        )


@router.callback_query(F.data.startswith("bot_max_configs_"))
async def bot_max_configs(callback: CallbackQuery, state: FSMContext):
    """Изменение лимита конфигов бота"""
    if not is_admin(callback.from_user.id):
        return
    
    bot_id = int(callback.data.replace("bot_max_configs_", ""))
    await callback.answer()
    
    from keyboards.admin_kb import get_bot_input_cancel_kb
    
    msg = await callback.message.edit_text(
        "📋 Введите максимальное количество конфигов:",
        parse_mode="Markdown",
        reply_markup=get_bot_input_cancel_kb(bot_id, "settings")
    )
    await state.set_state(AdminStates.waiting_for_bot_max_configs)
    await state.update_data(bot_id=bot_id, prompt_msg_id=msg.message_id)


@router.message(AdminStates.waiting_for_bot_max_configs)
async def process_bot_max_configs(message: Message, state: FSMContext):
    """Обработка лимита конфигов"""
    if not is_admin(message.from_user.id):
        return
    
    try:
        max_configs = int(message.text.strip())
        if max_configs < 1:
            raise ValueError()
    except:
        await message.answer("❌ Введите число больше 0")
        return
    
    data = await state.get_data()
    bot_id = data.get("bot_id")
    
    try:
        await message.delete()
    except:
        pass
    
    prompt_msg_id = data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    from services.settings import update_bot_setting, get_bot_instance
    from keyboards.admin_kb import get_bot_settings_kb
    
    await update_bot_setting(bot_id, "max_configs", max_configs)
    await state.clear()
    
    bot = await get_bot_instance(bot_id)
    await message.answer(
        f"✅ Лимит конфигов: {max_configs}\n\n"
        f"🤖 *Настройки бота @{bot.username}*",
        parse_mode="Markdown",
        reply_markup=get_bot_settings_kb(bot_id, bot)
    )


# ===== УПРАВЛЕНИЕ ЦЕНАМИ =====

@router.callback_query(F.data == "admin_prices")
async def admin_prices(callback: CallbackQuery, state: FSMContext):
    """Меню управления ценами"""
    await callback.answer()
    await state.clear()
    
    from services.settings import get_prices
    from keyboards.admin_kb import get_prices_kb
    
    prices = await get_prices()
    
    await callback.message.edit_text(
        "💵 *Управление ценами*\n\n"
        "Нажми на тариф, чтобы изменить цену:",
        parse_mode="Markdown",
        reply_markup=get_prices_kb(prices)
    )


@router.callback_query(F.data == "price_trial")
async def price_trial_edit(callback: CallbackQuery, state: FSMContext):
    """Редактирование пробного периода"""
    await callback.answer()
    
    from keyboards.admin_kb import get_price_edit_cancel_kb
    
    msg = await callback.message.edit_text(
        "🎁 *Пробный период*\n\n"
        "Введи количество дней для пробного периода:",
        parse_mode="Markdown",
        reply_markup=get_price_edit_cancel_kb()
    )
    
    await state.set_state(AdminStates.waiting_for_price_trial)
    await state.update_data(prompt_msg_id=msg.message_id)


@router.callback_query(F.data == "price_30")
async def price_30_edit(callback: CallbackQuery, state: FSMContext):
    """Редактирование цены 30 дней"""
    await callback.answer()
    
    from keyboards.admin_kb import get_price_edit_cancel_kb
    
    msg = await callback.message.edit_text(
        "📅 *Тариф 30 дней*\n\n"
        "Введи новую цену в рублях:",
        parse_mode="Markdown",
        reply_markup=get_price_edit_cancel_kb()
    )
    
    await state.set_state(AdminStates.waiting_for_price_30)
    await state.update_data(prompt_msg_id=msg.message_id)


@router.callback_query(F.data == "price_90")
async def price_90_edit(callback: CallbackQuery, state: FSMContext):
    """Редактирование цены 90 дней"""
    await callback.answer()
    
    from keyboards.admin_kb import get_price_edit_cancel_kb
    
    msg = await callback.message.edit_text(
        "📅 *Тариф 90 дней*\n\n"
        "Введи новую цену в рублях:",
        parse_mode="Markdown",
        reply_markup=get_price_edit_cancel_kb()
    )
    
    await state.set_state(AdminStates.waiting_for_price_90)
    await state.update_data(prompt_msg_id=msg.message_id)


@router.callback_query(F.data == "price_180")
async def price_180_edit(callback: CallbackQuery, state: FSMContext):
    """Редактирование цены 180 дней"""
    await callback.answer()
    
    from keyboards.admin_kb import get_price_edit_cancel_kb
    
    msg = await callback.message.edit_text(
        "📅 *Тариф 180 дней*\n\n"
        "Введи новую цену в рублях:",
        parse_mode="Markdown",
        reply_markup=get_price_edit_cancel_kb()
    )
    
    await state.set_state(AdminStates.waiting_for_price_180)
    await state.update_data(prompt_msg_id=msg.message_id)


@router.message(AdminStates.waiting_for_price_trial)
async def process_price_trial(message: Message, state: FSMContext):
    """Обработка ввода пробного периода"""
    try:
        days = int(message.text.strip())
        if days < 1 or days > 30:
            raise ValueError()
    except:
        await message.answer("❌ Введите число от 1 до 30")
        return
    
    try:
        await message.delete()
    except:
        pass
    
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    from services.settings import set_price, get_prices
    from keyboards.admin_kb import get_prices_kb
    
    await set_price("trial_days", days)
    await state.clear()
    
    prices = await get_prices()
    await message.answer(
        f"✅ Пробный период: {days} дней\n\n"
        "💵 *Управление ценами*",
        parse_mode="Markdown",
        reply_markup=get_prices_kb(prices)
    )


@router.message(AdminStates.waiting_for_price_30)
async def process_price_30(message: Message, state: FSMContext):
    """Обработка ввода цены 30 дней"""
    try:
        price = int(message.text.strip())
        if price < 1:
            raise ValueError()
    except:
        await message.answer("❌ Введите число больше 0")
        return
    
    try:
        await message.delete()
    except:
        pass
    
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    from services.settings import set_price, get_prices
    from keyboards.admin_kb import get_prices_kb
    
    await set_price("price_30", price)
    await state.clear()
    
    prices = await get_prices()
    await message.answer(
        f"✅ Цена 30 дней: {price}₽\n\n"
        "💵 *Управление ценами*",
        parse_mode="Markdown",
        reply_markup=get_prices_kb(prices)
    )


@router.message(AdminStates.waiting_for_price_90)
async def process_price_90(message: Message, state: FSMContext):
    """Обработка ввода цены 90 дней"""
    try:
        price = int(message.text.strip())
        if price < 1:
            raise ValueError()
    except:
        await message.answer("❌ Введите число больше 0")
        return
    
    try:
        await message.delete()
    except:
        pass
    
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    from services.settings import set_price, get_prices
    from keyboards.admin_kb import get_prices_kb
    
    await set_price("price_90", price)
    await state.clear()
    
    prices = await get_prices()
    await message.answer(
        f"✅ Цена 90 дней: {price}₽\n\n"
        "💵 *Управление ценами*",
        parse_mode="Markdown",
        reply_markup=get_prices_kb(prices)
    )


@router.message(AdminStates.waiting_for_price_180)
async def process_price_180(message: Message, state: FSMContext):
    """Обработка ввода цены 180 дней"""
    try:
        price = int(message.text.strip())
        if price < 1:
            raise ValueError()
    except:
        await message.answer("❌ Введите число больше 0")
        return
    
    try:
        await message.delete()
    except:
        pass
    
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_msg_id)
        except:
            pass
    
    from services.settings import set_price, get_prices
    from keyboards.admin_kb import get_prices_kb
    
    await set_price("price_180", price)
    await state.clear()
    
    prices = await get_prices()
    await message.answer(
        f"✅ Цена 180 дней: {price}₽\n\n"
        "💵 *Управление ценами*",
        parse_mode="Markdown",
        reply_markup=get_prices_kb(prices)
    )


# ===== ПЕРЕЗАГРУЗКА СЕРВИСА =====

@router.callback_query(F.data == "admin_restart_service")
async def admin_restart_service(callback: CallbackQuery):
    """Перезагрузка сервиса бота"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer()
    
    await callback.message.edit_text(
        "⚠️ *Перезагрузка сервиса*\n\n"
        "Бот будет перезапущен через systemctl.\n"
        "Это займёт около 5 секунд.\n\n"
        "Продолжить?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, перезагрузить", callback_data="admin_restart_confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin_menu")
            ]
        ])
    )


@router.callback_query(F.data == "admin_restart_confirm")
async def admin_restart_confirm(callback: CallbackQuery):
    """Подтверждение перезагрузки"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.answer("🔄 Перезагружаю сервис...")
    
    await callback.message.edit_text(
        "✅ *Сервис перезагружается...*\n\n"
        "Бот будет недоступен несколько секунд.",
        parse_mode="Markdown"
    )
    
    logger.info(f"Администратор {callback.from_user.id} запустил перезагрузку сервиса")
    
    # Запускаем перезагрузку в фоне с задержкой, чтобы сообщение успело отправиться
    import asyncio
    await asyncio.sleep(1)  # Даём время на отправку сообщения
    
    # Используем Popen чтобы не ждать завершения (бот всё равно умрёт)
    subprocess.Popen(['systemctl', 'restart', 'vpn-bot'])


# ===== УПРАВЛЕНИЕ ЛОГАМИ =====

@router.callback_query(F.data == "admin_logs")
async def admin_logs(callback: CallbackQuery):
    """Меню управления логами"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    
    from services.telegram_logger import get_log_channels
    from keyboards.admin_kb import get_logs_menu_kb
    
    channels = await get_log_channels()
    
    await callback.message.edit_text(
        "📝 *Управление логами*\n\n"
        "Логи отправляются в реальном времени в подключённые чаты.\n"
        f"Подключено чатов: {len(channels)}",
        parse_mode="Markdown",
        reply_markup=get_logs_menu_kb(channels)
    )


@router.callback_query(F.data == "log_add_channel")
async def log_add_channel(callback: CallbackQuery, state: FSMContext):
    """Добавление канала для логов"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    
    from keyboards.admin_kb import get_log_add_cancel_kb
    
    msg = await callback.message.edit_text(
        "➕ *Добавление чата для логов*\n\n"
        "Перешлите любое сообщение из чата/канала, куда нужно отправлять логи.\n\n"
        "ℹ️ Бот должен быть админом в этом чате/канале.",
        parse_mode="Markdown",
        reply_markup=get_log_add_cancel_kb()
    )
    await state.set_state(AdminStates.waiting_for_log_channel)
    await state.update_data(prompt_msg_id=msg.message_id)


@router.message(AdminStates.waiting_for_log_channel)
async def process_log_channel(message: Message, state: FSMContext, bot: Bot):
    """Обработка пересланного сообщения для добавления канала логов"""
    if not is_admin(message.from_user.id):
        return
    
    try:
        await message.delete()
    except:
        pass
    
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    
    # Проверяем что сообщение переслано
    if not message.forward_from_chat:
        await bot.send_message(
            message.chat.id,
            "❌ Перешлите сообщение из чата или канала"
        )
        return
    
    chat_id = message.forward_from_chat.id
    chat_title = message.forward_from_chat.title or f"Chat {chat_id}"
    
    # Проверяем что бот может писать в этот чат
    try:
        test_msg = await bot.send_message(chat_id, "📝 Тестовое сообщение - логи подключены!")
        await test_msg.delete()
    except Exception as e:
        await bot.send_message(
            message.chat.id,
            f"❌ Не могу отправить сообщение в этот чат.\n"
            f"Убедитесь, что бот добавлен в чат и имеет права на отправку сообщений."
        )
        return
    
    # Добавляем канал
    from services.telegram_logger import add_log_channel, get_log_channels
    from keyboards.admin_kb import get_logs_menu_kb
    
    await add_log_channel(chat_id, chat_title)
    await state.clear()
    
    channels = await get_log_channels()
    
    if prompt_msg_id:
        try:
            await bot.edit_message_text(
                f"✅ Чат *{chat_title}* добавлен для логов!\n\n"
                "📝 *Управление логами*\n"
                f"Подключено чатов: {len(channels)}",
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                parse_mode="Markdown",
                reply_markup=get_logs_menu_kb(channels)
            )
        except:
            pass


@router.callback_query(F.data.startswith("log_channel_"))
async def log_channel_detail(callback: CallbackQuery):
    """Детали канала логов"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    channel_id = int(callback.data.replace("log_channel_", ""))
    
    from database.models import LogChannel
    from keyboards.admin_kb import get_log_channel_kb
    
    async with async_session() as session:
        stmt = select(LogChannel).where(LogChannel.id == channel_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if not channel:
            await callback.answer("Канал не найден", show_alert=True)
            return
        
        status = "🟢 Активен" if channel.is_active else "🔴 Отключён"
        title = channel.title or f"ID: {channel.chat_id}"
        
        # Статусы типов логов
        bot_logs = getattr(channel, 'bot_logs', True)
        system_logs = getattr(channel, 'system_logs', False)
        aiogram_logs = getattr(channel, 'aiogram_logs', False)
        
        await callback.message.edit_text(
            f"📝 *Канал логов*\n\n"
            f"📌 Название: {title}\n"
            f"🆔 ID: `{channel.chat_id}`\n"
            f"📊 Уровень: {channel.log_level}\n"
            f"Статус: {status}\n\n"
            f"*Типы логов:*\n"
            f"📦 Логи бота: {'✅' if bot_logs else '❌'}\n"
            f"🖥 Серверные: {'✅' if system_logs else '❌'}\n"
            f"🤖 Сетевые: {'✅' if aiogram_logs else '❌'}",
            parse_mode="Markdown",
            reply_markup=get_log_channel_kb(channel.id, channel.is_active, bot_logs, system_logs, aiogram_logs)
        )


@router.callback_query(F.data.startswith("log_toggle_"))
async def log_toggle_channel(callback: CallbackQuery):
    """Переключение активности канала"""
    if not is_admin(callback.from_user.id):
        return
    
    channel_id = int(callback.data.replace("log_toggle_", ""))
    
    from services.telegram_logger import toggle_log_channel
    from database.models import LogChannel
    from keyboards.admin_kb import get_log_channel_kb
    
    new_state = await toggle_log_channel(channel_id)
    if new_state is not None:
        status = "включён" if new_state else "отключён"
        await callback.answer(f"Канал {status}")
    
    # Перезагружаем детали канала
    async with async_session() as session:
        stmt = select(LogChannel).where(LogChannel.id == channel_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if channel:
            status = "🟢 Активен" if channel.is_active else "🔴 Отключён"
            title = channel.title or f"ID: {channel.chat_id}"
            bot_logs = getattr(channel, 'bot_logs', True)
            system_logs = getattr(channel, 'system_logs', False)
            aiogram_logs = getattr(channel, 'aiogram_logs', False)
            
            await callback.message.edit_text(
                f"📝 *Канал логов*\n\n"
                f"📌 Название: {title}\n"
                f"🆔 ID: `{channel.chat_id}`\n"
                f"📊 Уровень: {channel.log_level}\n"
                f"Статус: {status}\n\n"
                f"*Типы логов:*\n"
                f"📦 Логи бота: {'✅' if bot_logs else '❌'}\n"
                f"🖥 Серверные: {'✅' if system_logs else '❌'}\n"
                f"🤖 Сетевые: {'✅' if aiogram_logs else '❌'}",
                parse_mode="Markdown",
                reply_markup=get_log_channel_kb(channel.id, channel.is_active, bot_logs, system_logs, aiogram_logs)
            )


@router.callback_query(F.data.startswith("log_level_"))
async def log_level_menu(callback: CallbackQuery):
    """Меню выбора уровня логов"""
    if not is_admin(callback.from_user.id):
        return
    
    await callback.answer()
    channel_id = int(callback.data.replace("log_level_", ""))
    
    from keyboards.admin_kb import get_log_level_kb
    
    await callback.message.edit_text(
        "📊 *Выберите уровень логов*\n\n"
        "🔍 DEBUG - все логи\n"
        "ℹ️ INFO - информационные и выше\n"
        "⚠️ WARNING - предупреждения и ошибки\n"
        "❌ ERROR - только ошибки",
        parse_mode="Markdown",
        reply_markup=get_log_level_kb(channel_id)
    )


@router.callback_query(F.data.startswith("log_setlevel_"))
async def log_set_level(callback: CallbackQuery):
    """Установка уровня логов"""
    if not is_admin(callback.from_user.id):
        return
    
    parts = callback.data.split("_")
    channel_id = int(parts[2])
    level = parts[3]
    
    from services.telegram_logger import set_log_level
    from database.models import LogChannel
    from keyboards.admin_kb import get_log_channel_kb
    
    if await set_log_level(channel_id, level):
        await callback.answer(f"Уровень установлен: {level}")
    
    # Перезагружаем детали канала
    async with async_session() as session:
        stmt = select(LogChannel).where(LogChannel.id == channel_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if channel:
            status = "🟢 Активен" if channel.is_active else "🔴 Отключён"
            title = channel.title or f"ID: {channel.chat_id}"
            bot_logs = getattr(channel, 'bot_logs', True)
            system_logs = getattr(channel, 'system_logs', False)
            aiogram_logs = getattr(channel, 'aiogram_logs', False)
            
            await callback.message.edit_text(
                f"📝 *Канал логов*\n\n"
                f"📌 Название: {title}\n"
                f"🆔 ID: `{channel.chat_id}`\n"
                f"📊 Уровень: {channel.log_level}\n"
                f"Статус: {status}\n\n"
                f"*Типы логов:*\n"
                f"📦 Логи бота: {'✅' if bot_logs else '❌'}\n"
                f"🖥 Серверные: {'✅' if system_logs else '❌'}\n"
                f"🤖 Сетевые: {'✅' if aiogram_logs else '❌'}",
                parse_mode="Markdown",
                reply_markup=get_log_channel_kb(channel.id, channel.is_active, bot_logs, system_logs, aiogram_logs)
            )


@router.callback_query(F.data.startswith("log_type_"))
async def log_toggle_type(callback: CallbackQuery):
    """Переключение типа логов"""
    if not is_admin(callback.from_user.id):
        return
    
    # log_type_{channel_id}_{type}
    parts = callback.data.split("_")
    channel_id = int(parts[2])
    log_type = "_".join(parts[3:])  # bot_logs, system_logs, aiogram_logs
    
    from services.telegram_logger import toggle_log_type
    from database.models import LogChannel
    from keyboards.admin_kb import get_log_channel_kb
    
    new_state = await toggle_log_type(channel_id, log_type)
    if new_state is not None:
        type_names = {
            'bot_logs': 'Логи бота',
            'system_logs': 'Серверные логи',
            'aiogram_logs': 'Сетевые логи'
        }
        status = "включены" if new_state else "отключены"
        await callback.answer(f"{type_names.get(log_type, log_type)} {status}")
    
    # Перезагружаем детали канала
    async with async_session() as session:
        stmt = select(LogChannel).where(LogChannel.id == channel_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if channel:
            status = "🟢 Активен" if channel.is_active else "🔴 Отключён"
            title = channel.title or f"ID: {channel.chat_id}"
            bot_logs = getattr(channel, 'bot_logs', True)
            system_logs = getattr(channel, 'system_logs', False)
            aiogram_logs = getattr(channel, 'aiogram_logs', False)
            
            await callback.message.edit_text(
                f"📝 *Канал логов*\n\n"
                f"📌 Название: {title}\n"
                f"🆔 ID: `{channel.chat_id}`\n"
                f"📊 Уровень: {channel.log_level}\n"
                f"Статус: {status}\n\n"
                f"*Типы логов:*\n"
                f"📦 Логи бота: {'✅' if bot_logs else '❌'}\n"
                f"🖥 Серверные: {'✅' if system_logs else '❌'}\n"
                f"🤖 Сетевые: {'✅' if aiogram_logs else '❌'}",
                parse_mode="Markdown",
                reply_markup=get_log_channel_kb(channel.id, channel.is_active, bot_logs, system_logs, aiogram_logs)
            )


@router.callback_query(F.data.startswith("log_delete_"))
async def log_delete_channel(callback: CallbackQuery):
    """Удаление канала логов"""
    if not is_admin(callback.from_user.id):
        return
    
    channel_id = int(callback.data.replace("log_delete_", ""))
    
    from services.telegram_logger import remove_log_channel, get_log_channels
    from keyboards.admin_kb import get_logs_menu_kb
    
    await remove_log_channel(channel_id)
    await callback.answer("Канал удалён")
    
    channels = await get_log_channels()
    
    await callback.message.edit_text(
        "📝 *Управление логами*\n\n"
        "Логи отправляются в реальном времени в подключённые чаты.\n"
        f"Подключено чатов: {len(channels)}",
        parse_mode="Markdown",
        reply_markup=get_logs_menu_kb(channels)
    )


@router.callback_query(F.data.startswith("log_goto_"))
async def log_goto_channel(callback: CallbackQuery, bot: Bot):
    """Перейти в чат логов"""
    if not is_admin(callback.from_user.id):
        return
    
    channel_id = int(callback.data.replace("log_goto_", ""))
    
    from database.models import LogChannel
    
    async with async_session() as session:
        stmt = select(LogChannel).where(LogChannel.id == channel_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if channel:
            # Отправляем ссылку на чат
            try:
                chat = await bot.get_chat(channel.chat_id)
                if chat.invite_link:
                    await callback.answer()
                    await callback.message.answer(
                        f"📎 Ссылка на чат: {chat.invite_link}"
                    )
                else:
                    await callback.answer("Нет ссылки на чат", show_alert=True)
            except:
                await callback.answer("Не удалось получить ссылку", show_alert=True)
