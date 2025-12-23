# VPN Bot - Информация для следующей сессии

## Проект
Telegram бот для WireGuard VPN: `@agdevpnbot`

## Структура
- **Путь**: `/Users/agdelesha/Desktop/myScripts/wgScript/vpn_bot/`
- **Сервер**: `root@83.217.9.75:/root/vpn_bot/`
- **Сервис**: `systemctl restart vpn-bot`

## Технологии
- Python 3.8, aiogram 3.x
- SQLAlchemy async (SQLite)
- WireGuard VPN

## Ключевые файлы
- `bot.py` - точка входа
- `handlers/user.py` - пользовательские команды
- `handlers/admin.py` - админ-панель
- `services/wireguard.py` - управление WireGuard
- `services/settings.py` - настройки бота

## Деплой
```bash
scp vpn_bot/handlers/admin.py root@83.217.9.75:/root/vpn_bot/handlers/
ssh root@83.217.9.75 "systemctl restart vpn-bot"
```

## Известные особенности

### Markdown в сообщениях
- Имена конфигов могут содержать подчёркивания (`agdelesha_2`)
- Это ломает Markdown форматирование
- Решение: использовать `parse_mode=None` где выводятся имена конфигов
- Пример исправления в `admin_user_detail` (строка ~160)

### Дополнительные конфиги
- Пользователь запрашивает доп. конфиг → вводит название устройства
- Админ одобряет → конфиг создаётся с именем `username + device`
- Код в `admin.py` функция `admin_approve_config_request`
- Название устройства извлекается regex из сообщения админу

### Мигание кнопок
- Стандартное поведение Telegram при смене клавиатуры
- Попытка добавить middleware для `callback.answer()` сломала бота
- `CallbackQuery` в aiogram 3.x не имеет атрибута `answered`
- Оставили как есть - это не критично

## Последние изменения (23.12.2024)
1. Тарифы: изменён текст на "30/90/180 дней" вместо "месяцев"
2. Убран Markdown из `admin_user_detail` (parse_mode=None)
3. Конфиги называются `username + device` (без подчёркивания между ними)

## GitHub
https://github.com/agdelesha/agdevpnbot
