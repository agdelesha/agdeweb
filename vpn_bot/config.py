import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PAYMENT_PHONE = os.getenv("PAYMENT_PHONE", "89992115019")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

WG_INTERFACE = os.getenv("WG_INTERFACE", "wg0")
WG_CONF = os.getenv("WG_CONF", "/etc/wireguard/wg0.conf")
CLIENT_DIR = os.getenv("CLIENT_DIR", "/etc/wireguard/clients")
ADD_SCRIPT = os.getenv("ADD_SCRIPT", "/usr/local/bin/wg-new-conf.sh")
REMOVE_SCRIPT = os.getenv("REMOVE_SCRIPT", "/usr/local/bin/wg-remove-client.sh")

LOCAL_MODE = os.getenv("LOCAL_MODE", "false").lower() == "true"

DATABASE_URL = "sqlite+aiosqlite:///./vpn_bot.db"

TARIFFS = {
    "trial": {"days": 7, "price": 0, "name": "7 дней"},
    "30": {"days": 30, "price": 100, "name": "30 дней"},
    "90": {"days": 90, "price": 200, "name": "90 дней"},
    "180": {"days": 180, "price": 300, "name": "180 дней"},
    "unlimited": {"days": 0, "price": 0, "name": "Бессрочный"},
}

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в .env")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID не установлен в .env")
