from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton
)


def get_phone_kb() -> ReplyKeyboardMarkup:
    """Клавиатура для запроса номера телефона"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_main_menu_kb(server=None) -> InlineKeyboardMarkup:
    """Главное меню после добавления сервера"""
    buttons = []
    
    if server:
        # Проверяем есть ли что устанавливать
        not_installed = []
        if not server.wg_installed:
            not_installed.append("WG")
        if not server.awg_installed:
            not_installed.append("AWG")
        if not server.v2ray_installed:
            not_installed.append("V2Ray")
        if not server.vpn_bot_installed:
            not_installed.append("Bot")
        
        # Кнопка "Установить всё" если есть что устанавливать
        if len(not_installed) > 1:
            buttons.append([InlineKeyboardButton(text="📦 Установить всё (~5 мин)", callback_data=f"install_all_{server.id}")])
        
        # Показываем только неустановленные компоненты
        if not server.vpn_bot_installed:
            buttons.append([InlineKeyboardButton(text="🤖 Деплой бота", callback_data=f"deploy_bot_{server.id}")])
        if not server.wg_installed:
            buttons.append([InlineKeyboardButton(text="🔐 Установить WireGuard", callback_data=f"install_wg_{server.id}")])
        if not server.awg_installed:
            buttons.append([InlineKeyboardButton(text="🛡️ Установить AmneziaWG", callback_data=f"install_awg_{server.id}")])
        if not server.v2ray_installed:
            buttons.append([InlineKeyboardButton(text="🚀 Установить V2Ray", callback_data=f"install_v2ray_{server.id}")])
        
        # Кнопка управления сервером
        buttons.append([InlineKeyboardButton(text="⚙️ Управление сервером", callback_data=f"manage_server_{server.id}")])
    
    buttons.append([InlineKeyboardButton(text="➕ Добавить сервер", callback_data="add_server")])
    buttons.append([InlineKeyboardButton(text="📋 Мои серверы", callback_data="my_servers")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_server_menu_kb(server) -> InlineKeyboardMarkup:
    """Меню управления сервером"""
    buttons = []
    
    # Проверяем есть ли что устанавливать
    not_installed = []
    if not server.wg_installed:
        not_installed.append("WG")
    if not server.awg_installed:
        not_installed.append("AWG")
    if not server.v2ray_installed:
        not_installed.append("V2Ray")
    if not server.vpn_bot_installed:
        not_installed.append("Bot")
    
    # Кнопка "Установить всё" если есть что устанавливать
    if len(not_installed) > 1:
        buttons.append([InlineKeyboardButton(text="📦 Установить всё (~5 мин)", callback_data=f"install_all_{server.id}")])
    
    # Кнопки установки для неустановленных компонентов
    if not server.vpn_bot_installed:
        buttons.append([InlineKeyboardButton(text="🤖 Деплой бота", callback_data=f"deploy_bot_{server.id}")])
    if not server.wg_installed:
        buttons.append([InlineKeyboardButton(text="🔐 Установить WireGuard", callback_data=f"install_wg_{server.id}")])
    if not server.awg_installed:
        buttons.append([InlineKeyboardButton(text="🛡️ Установить AmneziaWG", callback_data=f"install_awg_{server.id}")])
    if not server.v2ray_installed:
        buttons.append([InlineKeyboardButton(text="🚀 Установить V2Ray", callback_data=f"install_v2ray_{server.id}")])
    
    buttons.append([InlineKeyboardButton(text="🔄 Проверить статус", callback_data=f"check_status_{server.id}")])
    buttons.append([InlineKeyboardButton(text="🗑 Удалить сервер", callback_data=f"delete_server_{server.id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="my_servers")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_servers_list_kb(servers: list) -> InlineKeyboardMarkup:
    """Список серверов пользователя"""
    buttons = []
    
    for server in servers:
        # Иконки установленных компонентов
        icons = []
        if server.wg_installed:
            icons.append("🔐")
        if server.awg_installed:
            icons.append("🛡️")
        if server.v2ray_installed:
            icons.append("🚀")
        if server.vpn_bot_installed:
            icons.append("🤖")
        
        icons_str = " ".join(icons) if icons else "⚪"
        name = server.name or server.ip
        buttons.append([InlineKeyboardButton(
            text=f"{icons_str} {name}",
            callback_data=f"server_{server.id}"
        )])
    
    buttons.append([InlineKeyboardButton(text="➕ Добавить сервер", callback_data="add_server")])
    buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])


def get_confirm_kb(action: str, server_id: int) -> InlineKeyboardMarkup:
    """Кнопки подтверждения действия"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{action}_{server_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"server_{server_id}")]
    ])


def get_back_to_server_kb(server_id: int) -> InlineKeyboardMarkup:
    """Кнопка возврата к серверу"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К серверу", callback_data=f"server_{server_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])
