from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_admin_menu_kb(pending_count: int = 0, pending_withdrawals: int = 0) -> InlineKeyboardMarkup:
    pending_badge = f" ({pending_count})" if pending_count > 0 else ""
    withdrawal_badge = f" ({pending_withdrawals})" if pending_withdrawals > 0 else ""
    buttons = [
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text=f"💰 Ожидают оплаты{pending_badge}", callback_data="admin_pending_payments")],
        [InlineKeyboardButton(text=f"💸 Заявки на вывод{withdrawal_badge}", callback_data="admin_withdrawals")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="admin_referrals")],
        [InlineKeyboardButton(text="🖥 Серверы", callback_data="admin_servers")],
        [InlineKeyboardButton(text="✉️ Сообщение", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_users_list_kb(users: list, page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    buttons = []
    start = page * per_page
    end = start + per_page
    page_users = users[start:end]
    
    for user in page_users:
        status = "🟢" if not user.is_blocked else "🔴"
        name = user.username or user.full_name[:20]
        buttons.append([InlineKeyboardButton(
            text=f"{status} {name}",
            callback_data=f"admin_user_{user.id}"
        )])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_users_page_{page-1}"))
    if end < len(users):
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_users_page_{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_user_detail_kb(user_id: int, max_configs: int = None) -> InlineKeyboardMarkup:
    max_text = f"📱 Лимит конфигов: {max_configs}" if max_configs else "📱 Лимит конфигов: глобальный"
    buttons = [
        [InlineKeyboardButton(text="📱 Конфиги", callback_data=f"admin_user_configs_{user_id}")],
        [InlineKeyboardButton(text="💰 История платежей", callback_data=f"admin_user_payments_{user_id}")],
        [InlineKeyboardButton(text="➕ Добавить конфиг", callback_data=f"admin_add_config_{user_id}")],
        [InlineKeyboardButton(text="🎁 Подарить", callback_data=f"admin_gift_menu_{user_id}")],
        [InlineKeyboardButton(text=max_text, callback_data=f"admin_user_max_configs_{user_id}")],
        [InlineKeyboardButton(text="🗑 Удалить пользователя", callback_data=f"admin_delete_user_{user_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_users")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_user_max_configs_cancel_kb(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура отмены при вводе лимита конфигов пользователя"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_user_{user_id}")]
    ])


def get_gift_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора срока подарочной подписки"""
    buttons = [
        [InlineKeyboardButton(text="📅 30 дней", callback_data=f"admin_gift_30_{user_id}")],
        [InlineKeyboardButton(text="📅 90 дней", callback_data=f"admin_gift_90_{user_id}")],
        [InlineKeyboardButton(text="📅 180 дней", callback_data=f"admin_gift_180_{user_id}")],
        [InlineKeyboardButton(text="♾ Бессрочная", callback_data=f"admin_gift_unlimited_{user_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user_{user_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_review_kb(payment_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_approve_{payment_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_{payment_id}"),
        ],
        [InlineKeyboardButton(text="◀️ К списку", callback_data="admin_pending_payments")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_pending_payments_kb(payments: list) -> InlineKeyboardMarkup:
    buttons = []
    for payment in payments:
        user = payment.user
        name = user.username or user.full_name[:15]
        buttons.append([InlineKeyboardButton(
            text=f"💳 {name} — {payment.amount}₽",
            callback_data=f"admin_payment_{payment.id}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_confirm_delete_kb(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin_confirm_delete_{user_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_user_{user_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_user_configs_kb(configs: list, user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for config in configs:
        status = "🟢" if config.is_active else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {config.name}",
            callback_data=f"admin_config_{config.id}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user_{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_config_kb(config_id: int, user_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 Отключить" if is_active else "🟢 Включить"
    buttons = [
        [InlineKeyboardButton(text=toggle_text, callback_data=f"admin_toggle_config_{config_id}")],
        [InlineKeyboardButton(text="🗑 Удалить конфиг", callback_data=f"admin_delete_config_{config_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user_configs_{user_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_config_request_kb(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="✅ Ок", callback_data=f"cfgreq_ok_{user_id}"),
            InlineKeyboardButton(text="❌ Не ок", callback_data=f"cfgreq_no_{user_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_settings_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🤖 Боты", callback_data="settings_bots")],
        [InlineKeyboardButton(text="🔑 Пароль", callback_data="settings_password")],
        [InlineKeyboardButton(text="📢 Подписка на канал", callback_data="settings_channel")],
        [InlineKeyboardButton(text="📱 Запрос телефона", callback_data="settings_phone")],
        [InlineKeyboardButton(text="📋 Подтверждение доп. конфига", callback_data="settings_config_approval")],
        [InlineKeyboardButton(text="📊 Мониторинг", callback_data="settings_monitoring")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_config_approval_kb(is_enabled: bool, max_configs: int = 0) -> InlineKeyboardMarkup:
    max_text = f"📱 Макс. конфигов: {max_configs}" if max_configs > 0 else "📱 Макс. конфигов: ∞"
    buttons = [
        [
            InlineKeyboardButton(text="🟢 Вкл", callback_data="settings_config_approval_on"),
            InlineKeyboardButton(text="🔴 Выкл", callback_data="settings_config_approval_off"),
        ],
        [InlineKeyboardButton(text=max_text, callback_data="settings_max_configs")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_max_configs_cancel_kb() -> InlineKeyboardMarkup:
    """Клавиатура отмены при вводе макс. конфигов"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="settings_config_approval")]
    ])


def get_channel_change_cancel_kb() -> InlineKeyboardMarkup:
    """Клавиатура отмены при изменении канала"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="settings_channel")]
    ])


def get_phone_settings_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="🟢 Вкл", callback_data="settings_phone_on"),
            InlineKeyboardButton(text="🔴 Выкл", callback_data="settings_phone_off"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_password_settings_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    status = "🟢 Вкл" if is_enabled else "🔴 Выкл"
    buttons = [
        [
            InlineKeyboardButton(text="🟢 Вкл", callback_data="settings_password_on"),
            InlineKeyboardButton(text="🔴 Выкл", callback_data="settings_password_off"),
        ],
        [InlineKeyboardButton(text="✏️ Изменить пароль", callback_data="settings_password_change")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_channel_settings_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="🟢 Вкл", callback_data="settings_channel_on"),
            InlineKeyboardButton(text="🔴 Выкл", callback_data="settings_channel_off"),
        ],
        [InlineKeyboardButton(text="✏️ Изменить канал", callback_data="settings_channel_change")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_check_subscription_kb(channel_name: str = "agdevpn") -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{channel_name}")],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscription")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_monitoring_settings_kb(is_enabled: bool) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="🟢 Вкл", callback_data="settings_monitoring_on"),
            InlineKeyboardButton(text="🔴 Выкл", callback_data="settings_monitoring_off"),
        ],
        [InlineKeyboardButton(text="📊 Порог трафика", callback_data="settings_monitoring_traffic")],
        [InlineKeyboardButton(text="📱 Порог конфигов", callback_data="settings_monitoring_configs")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_broadcast_menu_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📢 Всем", callback_data="broadcast_all")],
        [InlineKeyboardButton(text="👤 Из списка", callback_data="broadcast_select")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_broadcast_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_menu")]
    ])


def get_broadcast_users_kb(users: list, page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    buttons = []
    start = page * per_page
    end = start + per_page
    page_users = users[start:end]
    
    for user in page_users:
        name = user.username or user.full_name[:20]
        buttons.append([InlineKeyboardButton(
            text=f"👤 {name}",
            callback_data=f"broadcast_user_{user.telegram_id}"
        )])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"broadcast_page_{page-1}"))
    if end < len(users):
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"broadcast_page_{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_broadcast")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_servers_list_kb(servers: list, client_counts: dict = None) -> InlineKeyboardMarkup:
    """Клавиатура списка серверов"""
    buttons = []
    client_counts = client_counts or {}
    
    for server in servers:
        status = "🟢" if server.is_active else "🔴"
        count = client_counts.get(server.id, 0)
        buttons.append([InlineKeyboardButton(
            text=f"{status} {server.name} ({count}/{server.max_clients})",
            callback_data=f"admin_server_{server.id}"
        )])
    
    buttons.append([InlineKeyboardButton(text="➕ Добавить сервер", callback_data="admin_server_add")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_server_detail_kb(server_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Клавиатура детальной информации о сервере"""
    toggle_text = "🔴 Отключить" if is_active else "🟢 Включить"
    buttons = [
        [InlineKeyboardButton(text="🔄 Проверить подключение", callback_data=f"admin_server_check_{server_id}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"admin_server_toggle_{server_id}")],
        [InlineKeyboardButton(text="👥 Клиенты", callback_data=f"admin_server_clients_{server_id}"),
         InlineKeyboardButton(text="✉️ Сообщение", callback_data=f"admin_server_broadcast_{server_id}")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"admin_server_edit_{server_id}")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data=f"admin_server_stats_{server_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_server_delete_{server_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_servers")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_server_confirm_delete_kb(server_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления сервера"""
    buttons = [
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin_server_confirm_delete_{server_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_server_{server_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_server_add_cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены при добавлении сервера"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_servers")]
    ])


def get_server_install_kb(server_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для установки WireGuard на сервер"""
    buttons = [
        [InlineKeyboardButton(text="🚀 Установить WireGuard", callback_data=f"admin_server_install_{server_id}")],
        [InlineKeyboardButton(text="⏭ Пропустить (настрою вручную)", callback_data=f"admin_server_{server_id}")],
        [InlineKeyboardButton(text="🗑 Удалить сервер", callback_data=f"admin_server_delete_{server_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_server_edit_kb(server_id: int) -> InlineKeyboardMarkup:
    """Клавиатура редактирования сервера"""
    buttons = [
        [InlineKeyboardButton(text="📝 Изменить имя", callback_data=f"admin_server_edit_name_{server_id}")],
        [InlineKeyboardButton(text="👥 Изменить макс. клиентов", callback_data=f"admin_server_edit_max_{server_id}")],
        [InlineKeyboardButton(text="⭐ Изменить приоритет", callback_data=f"admin_server_edit_priority_{server_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_server_{server_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_server_edit_cancel_kb(server_id: int) -> InlineKeyboardMarkup:
    """Кнопка отмены при редактировании сервера"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_server_{server_id}")]
    ])


def get_server_clients_kb(users: list, server_id: int, page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    """Клавиатура списка клиентов сервера"""
    buttons = []
    start = page * per_page
    end = start + per_page
    page_users = users[start:end]
    
    for user in page_users:
        name = user.username or user.full_name[:20]
        buttons.append([InlineKeyboardButton(
            text=f"👤 {name}",
            callback_data=f"admin_srvuser_{server_id}_{user.id}"
        )])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_server_clients_page_{server_id}_{page-1}"))
    if end < len(users):
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_server_clients_page_{server_id}_{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_server_{server_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_server_user_detail_kb(user_id: int, server_id: int) -> InlineKeyboardMarkup:
    """Клавиатура детальной информации о пользователе (из списка клиентов сервера)"""
    buttons = [
        [InlineKeyboardButton(text="📱 Конфиги", callback_data=f"admin_user_configs_{user_id}")],
        [InlineKeyboardButton(text="🎁 Подарить дни", callback_data=f"admin_gift_{user_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_delete_{user_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_server_clients_{server_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_server_broadcast_cancel_kb(server_id: int) -> InlineKeyboardMarkup:
    """Кнопка отмены при рассылке клиентам сервера"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_server_{server_id}")]
    ])


def get_referrals_list_kb(users: list, page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    """Клавиатура списка рефералов (пользователей с приглашёнными)"""
    buttons = []
    start = page * per_page
    end = start + per_page
    page_users = users[start:end]
    
    for user in page_users:
        name = user.username or user.full_name[:20]
        referral_count = len(user.referrals) if hasattr(user, 'referrals') else 0
        buttons.append([InlineKeyboardButton(
            text=f"👤 {name} ({referral_count} реф.)",
            callback_data=f"admin_referral_{user.id}"
        )])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"admin_referrals_page_{page-1}"))
    if end < len(users):
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"admin_referrals_page_{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_referral_detail_kb(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура детальной информации о реферале"""
    buttons = [
        [InlineKeyboardButton(text="📊 Изменить %", callback_data=f"admin_referral_percent_{user_id}")],
        [InlineKeyboardButton(text="👤 Профиль пользователя", callback_data=f"admin_user_{user_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_referrals")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_referral_percent_cancel_kb(user_id: int) -> InlineKeyboardMarkup:
    """Кнопка отмены при изменении % реферала"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_referral_{user_id}")]
    ])


def get_withdrawal_review_kb(withdrawal_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для обработки заявки на вывод"""
    buttons = [
        [
            InlineKeyboardButton(text="✅ Вывод готов", callback_data=f"admin_withdrawal_complete_{withdrawal_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_withdrawal_cancel_{withdrawal_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_withdrawals_list_kb(withdrawals: list) -> InlineKeyboardMarkup:
    """Клавиатура списка заявок на вывод"""
    buttons = []
    for w in withdrawals:
        user = w.user
        name = user.username or user.full_name[:15]
        buttons.append([InlineKeyboardButton(
            text=f"💸 {name} — {int(w.amount)}₽",
            callback_data=f"admin_withdrawal_{w.id}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ===== УПРАВЛЕНИЕ БОТАМИ =====

def get_bots_list_kb(bots: list) -> InlineKeyboardMarkup:
    """Клавиатура списка ботов"""
    buttons = []
    for bot in bots:
        status = "🟢" if bot.is_active else "🔴"
        name = f"@{bot.username}" if bot.username else f"ID: {bot.bot_id}"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {name}",
            callback_data=f"bot_settings_{bot.bot_id}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Добавить бота", callback_data="bot_add")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bot_settings_kb(bot_id: int, bot) -> InlineKeyboardMarkup:
    """Клавиатура настроек конкретного бота"""
    pwd_status = "🟢" if bot.password else "🔴"
    channel_status = "🟢" if bot.channel else "🔴"
    phone_status = "🟢" if bot.require_phone else "🔴"
    active_status = "🟢 Активен" if bot.is_active else "🔴 Отключен"
    
    buttons = [
        [InlineKeyboardButton(text=f"{pwd_status} Пароль", callback_data=f"bot_password_{bot_id}")],
        [InlineKeyboardButton(text=f"{channel_status} Канал", callback_data=f"bot_channel_{bot_id}")],
        [InlineKeyboardButton(text=f"{phone_status} Запрос телефона", callback_data=f"bot_phone_{bot_id}")],
        [InlineKeyboardButton(text=f"📱 Макс. конфигов: {bot.max_configs}", callback_data=f"bot_max_configs_{bot_id}")],
        [InlineKeyboardButton(text=active_status, callback_data=f"bot_toggle_{bot_id}")],
        [InlineKeyboardButton(text="🗑 Удалить бота", callback_data=f"bot_delete_{bot_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="settings_bots")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bot_password_kb(bot_id: int, has_password: bool) -> InlineKeyboardMarkup:
    """Клавиатура настройки пароля бота"""
    buttons = [
        [InlineKeyboardButton(text="✏️ Изменить пароль", callback_data=f"bot_password_set_{bot_id}")],
    ]
    if has_password:
        buttons.append([InlineKeyboardButton(text="🗑 Убрать пароль", callback_data=f"bot_password_remove_{bot_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"bot_settings_{bot_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bot_channel_kb(bot_id: int, has_channel: bool) -> InlineKeyboardMarkup:
    """Клавиатура настройки канала бота"""
    buttons = [
        [InlineKeyboardButton(text="✏️ Изменить канал", callback_data=f"bot_channel_set_{bot_id}")],
    ]
    if has_channel:
        buttons.append([InlineKeyboardButton(text="🗑 Убрать канал", callback_data=f"bot_channel_remove_{bot_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"bot_settings_{bot_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bot_input_cancel_kb(bot_id: int, back_action: str) -> InlineKeyboardMarkup:
    """Клавиатура отмены при вводе данных бота"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"bot_{back_action}_{bot_id}")]
    ])


def get_bot_add_cancel_kb() -> InlineKeyboardMarkup:
    """Клавиатура отмены при добавлении бота"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="settings_bots")]
    ])


def get_bot_delete_confirm_kb(bot_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления бота"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"bot_delete_confirm_{bot_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"bot_settings_{bot_id}")
        ]
    ])
