from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Float
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from typing import List


class Base(DeclarativeBase):
    pass


class Server(Base):
    """VPN сервер для распределения клиентов"""
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # "Germany-1", "Russia-1"
    host: Mapped[str] = mapped_column(String(255), nullable=False)  # IP или домен
    ssh_user: Mapped[str] = mapped_column(String(50), default="root")
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # пароль SSH
    
    # WireGuard пути на сервере
    wg_interface: Mapped[str] = mapped_column(String(20), default="wg0")
    wg_conf_path: Mapped[str] = mapped_column(String(255), default="/etc/wireguard/wg0.conf")
    client_dir: Mapped[str] = mapped_column(String(255), default="/etc/wireguard/clients")
    add_script: Mapped[str] = mapped_column(String(255), default="/usr/local/bin/wg-new-conf.sh")
    remove_script: Mapped[str] = mapped_column(String(255), default="/usr/local/bin/wg-remove-client.sh")
    
    # Лимиты и статус
    max_clients: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # выше = приоритетнее
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связь с конфигами
    configs: Mapped[List["Config"]] = relationship("Config", back_populates="server")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    how_to_seen: Mapped[bool] = mapped_column(Boolean, default=False)
    max_configs: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # индивидуальный лимит конфигов (None = глобальный)
    registration_complete: Mapped[bool] = mapped_column(Boolean, default=False)  # прошёл ли все этапы регистрации
    
    # Реферальная система
    referrer_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)  # кто пригласил
    referral_balance: Mapped[float] = mapped_column(Float, default=0.0)  # накопленный баланс от рефералов
    referral_percent: Mapped[float] = mapped_column(Float, default=10.0)  # % от оплат рефералов
    first_payment_done: Mapped[bool] = mapped_column(Boolean, default=False)  # была ли первая оплата (для скидки 50%)

    configs: Mapped[List["Config"]] = relationship("Config", back_populates="user", cascade="all, delete-orphan")
    subscriptions: Mapped[List["Subscription"]] = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    withdrawals: Mapped[List["WithdrawalRequest"]] = relationship("WithdrawalRequest", back_populates="user", cascade="all, delete-orphan")
    referrals: Mapped[List["User"]] = relationship("User", back_populates="referrer", foreign_keys="User.referrer_id")
    referrer: Mapped[Optional["User"]] = relationship("User", back_populates="referrals", remote_side="User.id", foreign_keys="User.referrer_id")


class Config(Base):
    __tablename__ = "configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    server_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("servers.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    public_key: Mapped[str] = mapped_column(String(255), nullable=False)
    preshared_key: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_ips: Mapped[str] = mapped_column(String(255), nullable=False)
    client_ip: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="configs")
    server: Mapped[Optional["Server"]] = relationship("Server", back_populates="configs")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    tariff_type: Mapped[str] = mapped_column(String(50), nullable=False)
    days_total: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_gift: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notified_3_days: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship("User", back_populates="subscriptions")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    tariff_type: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    receipt_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ocr_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    admin_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    has_referral_discount: Mapped[bool] = mapped_column(Boolean, default=False)  # была ли применена скидка 50% реферала

    user: Mapped["User"] = relationship("User", back_populates="payments")


class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)


class WithdrawalRequest(Base):
    """Заявка на вывод реферальных средств"""
    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)  # сумма вывода
    bank: Mapped[str] = mapped_column(String(100), nullable=False)  # банк для вывода
    phone: Mapped[str] = mapped_column(String(20), nullable=False)  # номер телефона
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, completed, cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="withdrawals")


class BotInstance(Base):
    """Экземпляр бота с индивидуальными настройками"""
    __tablename__ = "bot_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)  # telegram bot id
    token: Mapped[str] = mapped_column(String(100), nullable=False)  # токен бота
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # @username бота
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # название бота
    
    # Индивидуальные настройки
    password: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # пароль для доступа
    channel: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # канал для подписки
    require_phone: Mapped[bool] = mapped_column(Boolean, default=False)  # требовать телефон
    max_configs: Mapped[int] = mapped_column(Integer, default=3)  # лимит конфигов
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConfigQueue(Base):
    """Очередь ожидающих конфигов (когда все серверы заполнены)"""
    __tablename__ = "config_queue"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    config_name: Mapped[str] = mapped_column(String(100), nullable=False)  # желаемое имя конфига
    status: Mapped[str] = mapped_column(String(20), default="waiting")  # waiting, processing, completed, cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Связь с пользователем
    user: Mapped["User"] = relationship("User", backref="queue_items")


class LogChannel(Base):
    """Чаты/каналы для отправки логов"""
    __tablename__ = "log_channels"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)  # ID чата/канала
    title: Mapped[str] = mapped_column(String(255), nullable=True)  # Название чата
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # Активен ли
    log_level: Mapped[str] = mapped_column(String(20), default="INFO")  # Минимальный уровень логов
    # Типы логов
    bot_logs: Mapped[bool] = mapped_column(Boolean, default=True)  # Внутренние логи бота
    system_logs: Mapped[bool] = mapped_column(Boolean, default=False)  # Серверные логи (journald)
    aiogram_logs: Mapped[bool] = mapped_column(Boolean, default=False)  # Логи aiogram (ошибки сети и т.д.)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
