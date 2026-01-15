# AGDE Deploy Bot

Бот для корпоративных клиентов. Позволяет устанавливать WireGuard, AmneziaWG, V2Ray и VPN бота на серверы клиентов.

## Возможности

- 🔐 **WireGuard** — классический VPN протокол
- 🛡️ **AmneziaWG** — защищённый от блокировок VPN
- 🚀 **V2Ray/Xray** — продвинутый прокси с маскировкой
- 🤖 **VPN Bot** — Telegram бот для управления VPN (клиент становится админом)

## Установка

```bash
# Создать виртуальное окружение
python3 -m venv venv
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt

# Создать .env файл
cp .env.example .env
# Отредактировать .env и указать токен бота

# Запустить
python bot.py
```

## Деплой на сервер

```bash
# Скопировать файлы
scp -r agde_deploy_bot root@SERVER_IP:/root/

# На сервере создать сервис
cat > /etc/systemd/system/agde-deploy-bot.service << EOF
[Unit]
Description=AGDE Deploy Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/agde_deploy_bot
ExecStart=/root/agde_deploy_bot/venv/bin/python /root/agde_deploy_bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable agde-deploy-bot
systemctl start agde-deploy-bot
```

## Главный админ

ID: 906888481

Получает уведомления о новых клиентах и деплоях.
