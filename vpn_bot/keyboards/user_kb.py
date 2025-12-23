from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import TARIFFS, ADMIN_ID


def get_main_menu_kb(user_id: int = None, has_subscription: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    
    if not has_subscription:
        buttons.append([InlineKeyboardButton(text="🚀 Получить конфиг", callback_data="get_vpn")])
    
    buttons.append([
        InlineKeyboardButton(text="📱 Конфиги", callback_data="my_configs"),
        InlineKeyboardButton(text="📊 Подписка", callback_data="my_subscription")
    ])
    
    if has_subscription:
        buttons.append([InlineKeyboardButton(text="💳 Продлить", callback_data="extend_subscription")])
    
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
