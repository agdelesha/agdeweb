# Deploy Bot

Telegram-бот для управления серверами и деплоя VPN-бота.

## Функции

- 🚀 Развёртывание VPN-бота на новые серверы
- 🔄 Синхронизация БД между серверами
- 🖥 Управление серверами (статус, запуск/остановка)
- 💻 Терминал для выполнения команд
- 🔗 Связывание серверов по SSH

## Установка

```bash
pip install -r requirements.txt
```

## Настройка

Переменные окружения:
- `BOT_TOKEN` - токен Telegram бота

## Запуск

```bash
python bot.py
```

## Systemd сервис

```ini
[Unit]
Description=Deploy Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/deploy_bot
ExecStart=/root/deploy_bot/venv/bin/python /root/deploy_bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
