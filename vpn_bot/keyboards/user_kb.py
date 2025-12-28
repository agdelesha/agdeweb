from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import TARIFFS, ADMIN_ID


def get_main_menu_kb(user_id: int = None, has_subscription: bool = False, how_to_seen: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    
    if not has_subscription:
        buttons.append([InlineKeyboardButton(text="🚀 Получить конфиг", callback_data="get_vpn")])
    
    buttons.append([
        InlineKeyboardButton(text="📱 Конфиги", callback_data="my_configs"),
        InlineKeyboardButton(text="📊 Подписка", callback_data="my_subscription")
    ])
    
    if not how_to_seen:
        buttons.append([InlineKeyboardButton(text="❓ а как?", callback_data="how_to")])
    
    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton(text="🔧 Админ", callback_data="admin_menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariffs_kb(show_trial: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    
    if show_trial:
        buttons.append([InlineKeyboardButton(
            text="7 дней — бесплатно",
            callback_data="tariff_trial"
        )])
    
    buttons.append([InlineKeyboardButton(text="30 дней — 100₽", callback_data="tariff_30")])
    buttons.append([InlineKeyboardButton(text="90 дней — 200₽", callback_data="tariff_90")])
    buttons.append([InlineKeyboardButton(text="180 дней — 300₽", callback_data="tariff_180")])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📸 Отправить чек", callback_data="send_receipt")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])


def get_subscription_kb(has_active: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    if has_active:
        buttons.append([InlineKeyboardButton(text="💳 Продлить", callback_data="extend_subscription")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_how_to_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да понял я, понял", callback_data="how_to_understood")]
    ])


def get_no_configs_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Получить конфиг", callback_data="get_vpn")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])


def get_no_subscription_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Получить конфиг", callback_data="get_vpn")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])


def get_configs_kb(configs: list) -> InlineKeyboardMarkup:
    buttons = []
    for config in configs:
        status = "🟢" if config.is_active else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {config.name}",
            callback_data=f"config_{config.id}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Хочу ещё конфиг", callback_data="request_extra_config")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_config_detail_kb(config_id: int, is_active: bool) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📥 Скачать конфиг", callback_data=f"download_config_{config_id}")],
        [InlineKeyboardButton(text="📷 QR-код", callback_data=f"qr_config_{config_id}")],
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="my_configs")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_welcome_kb(show_trial: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура приветствия для новых пользователей без подписки"""
    buttons = []
    if show_trial:
        buttons.append([InlineKeyboardButton(text="🎁 Пробный доступ", callback_data="funnel_trial")])
    buttons.append([InlineKeyboardButton(text="💳 Тарифы", callback_data="funnel_tariffs")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_trial_activated_kb() -> InlineKeyboardMarkup:
    """Клавиатура после активации пробного периода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Получить", callback_data="funnel_get_config")]
    ])


def get_after_config_kb() -> InlineKeyboardMarkup:
    """Клавиатура после получения конфига (для пробного периода)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Купить подписку", callback_data="funnel_tariffs")],
        [InlineKeyboardButton(text="❓ а как?", callback_data="how_to")]
    ])


def get_device_selection_kb() -> InlineKeyboardMarkup:
    """Клавиатура выбора устройства для дополнительного конфига"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Телефон", callback_data="device_phone")],
        [InlineKeyboardButton(text="💻 ПК", callback_data="device_pc")],
        [InlineKeyboardButton(text="📟 Планшет", callback_data="device_tablet")],
        [InlineKeyboardButton(text="📡 Роутер", callback_data="device_router")],
        [InlineKeyboardButton(text="📺 Смарт ТВ", callback_data="device_tv")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="my_configs")]
    ])
