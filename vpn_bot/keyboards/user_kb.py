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
    
    buttons.append([InlineKeyboardButton(text="👥 Реферальная программа", callback_data="referral_menu")])
    
    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton(text="🔧 Админ", callback_data="admin_menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariffs_kb(show_trial: bool = True, has_referral_discount: bool = False, prices: dict = None) -> InlineKeyboardMarkup:
    """
    Клавиатура тарифов.
    prices - словарь с ценами из БД: {trial_days, price_30, price_90, price_180}
    """
    # Дефолтные цены если не переданы
    if prices is None:
        prices = {"trial_days": 3, "price_30": 200, "price_90": 400, "price_180": 600}
    
    trial_days = prices.get("trial_days", 3)
    price_30 = prices.get("price_30", 200)
    price_90 = prices.get("price_90", 400)
    price_180 = prices.get("price_180", 600)
    
    buttons = []
    
    if show_trial:
        buttons.append([InlineKeyboardButton(
            text=f"{trial_days} дня — бесплатно" if trial_days < 5 else f"{trial_days} дней — бесплатно",
            callback_data="tariff_trial"
        )])
    
    if has_referral_discount:
        # Показываем цены со скидкой 50%
        buttons.append([InlineKeyboardButton(text=f"30 дней — {price_30 // 2}₽ (скидка 50%)", callback_data="tariff_30")])
        buttons.append([InlineKeyboardButton(text=f"90 дней — {price_90 // 2}₽ (скидка 50%)", callback_data="tariff_90")])
        buttons.append([InlineKeyboardButton(text=f"180 дней — {price_180 // 2}₽ (скидка 50%)", callback_data="tariff_180")])
    else:
        buttons.append([InlineKeyboardButton(text=f"30 дней — {price_30}₽", callback_data="tariff_30")])
        buttons.append([InlineKeyboardButton(text=f"90 дней — {price_90}₽", callback_data="tariff_90")])
        buttons.append([InlineKeyboardButton(text=f"180 дней — {price_180}₽", callback_data="tariff_180")])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_payment_kb(show_referral_pay: bool = False, tariff_key: str = None) -> InlineKeyboardMarkup:
    """
    Клавиатура оплаты.
    show_referral_pay - показывать кнопку оплаты с реферального баланса
    tariff_key - ключ тарифа для callback_data
    """
    buttons = []
    if show_referral_pay and tariff_key:
        buttons.append([InlineKeyboardButton(text="💰 Оплатить с реф. баланса", callback_data=f"pay_referral_{tariff_key}")])
    buttons.append([InlineKeyboardButton(text="📸 Отправить чек", callback_data="send_receipt")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")]
    ])


def get_subscription_kb(has_active: bool = True, prices: dict = None) -> InlineKeyboardMarkup:
    """Клавиатура подписки с тарифами сразу"""
    buttons = []
    
    # Показываем тарифы сразу
    if prices:
        price_30 = prices.get('price_30', 200)
        price_90 = prices.get('price_90', 400)
        price_180 = prices.get('price_180', 600)
    else:
        price_30, price_90, price_180 = 200, 400, 600
    
    buttons.append([InlineKeyboardButton(text=f"30 дней — {price_30}₽", callback_data="tariff_30")])
    buttons.append([InlineKeyboardButton(text=f"90 дней — {price_90}₽", callback_data="tariff_90")])
    buttons.append([InlineKeyboardButton(text=f"180 дней — {price_180}₽", callback_data="tariff_180")])
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


def get_config_detail_kb(config_id: int, is_active: bool, server_deleted: bool = False, protocol_type: str = "wg") -> InlineKeyboardMarkup:
    buttons = []
    if not server_deleted:
        if protocol_type in ("awg", "v2ray"):
            # Для AWG и V2Ray показываем конфиг текстом
            buttons.append([InlineKeyboardButton(text="📋 Показать конфиг", callback_data=f"show_config_{config_id}")])
        else:
            # Для обычного WG скачиваем файл
            buttons.append([InlineKeyboardButton(text="📥 Скачать конфиг", callback_data=f"download_config_{config_id}")])
        buttons.append([InlineKeyboardButton(text="📷 QR-код", callback_data=f"qr_config_{config_id}")])
    buttons.append([InlineKeyboardButton(text="🗑 Удалить конфиг", callback_data=f"user_delete_config_{config_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="my_configs")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_user_config_delete_confirm_kb(config_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления конфига пользователем"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"user_confirm_delete_config_{config_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"config_{config_id}")
        ]
    ])


def get_welcome_kb(show_trial: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура приветствия для новых пользователей без подписки"""
    buttons = []
    buttons.append([InlineKeyboardButton(text="🚀 Получить доступ", callback_data="funnel_trial")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_trial_activated_kb() -> InlineKeyboardMarkup:
    """Клавиатура после активации пробного периода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Получить", callback_data="funnel_get_config")]
    ])


def get_after_config_kb() -> InlineKeyboardMarkup:
    """Клавиатура после получения конфига (для пробного периода)"""
    return InlineKeyboardMarkup(inline_keyboard=[])


def get_device_input_cancel_kb() -> InlineKeyboardMarkup:
    """Клавиатура отмены при вводе названия устройства"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_device_input")]
    ])


def get_referral_menu_kb(has_balance: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура реферальной программы"""
    buttons = [
        [InlineKeyboardButton(text="🔗 Получить ссылку", callback_data="referral_get_link")],
    ]
    if has_balance:
        buttons.append([InlineKeyboardButton(text="💸 Вывести средства", callback_data="referral_withdraw")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_referral_back_kb() -> InlineKeyboardMarkup:
    """Кнопка назад к реферальному меню"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="referral_menu")]
    ])


def get_withdrawal_cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены при выводе средств"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="referral_menu")]
    ])


def get_protocol_choice_kb(has_wg: bool = True, has_awg: bool = True, has_v2ray: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура выбора протокола VPN для доп. конфига"""
    buttons = []
    if has_wg:
        buttons.append([InlineKeyboardButton(text="🔒 WireGuard", callback_data="protocol_wg")])
    if has_awg:
        buttons.append([InlineKeyboardButton(text="🛡 AmneziaWG (защищённый)", callback_data="protocol_awg")])
    if has_v2ray:
        buttons.append([InlineKeyboardButton(text="🚀 V2Ray/VLESS (максимальная защита)", callback_data="protocol_v2ray")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_device_input")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_funnel_protocol_kb(has_wg: bool = True, has_awg: bool = True, has_v2ray: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура выбора протокола для первого конфига (воронка)"""
    buttons = []
    if has_wg:
        buttons.append([InlineKeyboardButton(text="🔒 WireGuard — простой и быстрый", callback_data="funnel_protocol_wg")])
    if has_awg:
        buttons.append([InlineKeyboardButton(text="🛡 AmneziaWG — защищённый", callback_data="funnel_protocol_awg")])
    if has_v2ray:
        buttons.append([InlineKeyboardButton(text="🚀 V2Ray — максимальная защита", callback_data="funnel_protocol_v2ray")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
