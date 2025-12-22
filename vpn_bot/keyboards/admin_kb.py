from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_admin_menu_kb(pending_count: int = 0) -> InlineKeyboardMarkup:
    pending_badge = f" ({pending_count})" if pending_count > 0 else ""
    buttons = [
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text=f"💰 Ожидают оплаты{pending_badge}", callback_data="admin_pending_payments")],
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


def get_user_detail_kb(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📱 Конфиги", callback_data=f"admin_user_configs_{user_id}")],
        [InlineKeyboardButton(text="💰 История платежей", callback_data=f"admin_user_payments_{user_id}")],
        [InlineKeyboardButton(text="➕ Добавить конфиг", callback_data=f"admin_add_config_{user_id}")],
        [InlineKeyboardButton(text="🎁 Подарить бессрочный", callback_data=f"admin_gift_{user_id}")],
        [InlineKeyboardButton(text="🗑 Удалить пользователя", callback_data=f"admin_delete_user_{user_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_users")],
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
        [InlineKeyboardButton(text="🔑 Пароль", callback_data="settings_password")],
        [InlineKeyboardButton(text="📢 Подписка на канал", callback_data="settings_channel")],
        [InlineKeyboardButton(text="📊 Мониторинг", callback_data="settings_monitoring")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")],
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
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_check_subscription_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📢 Подписаться", url="https://t.me/agdevpn")],
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
