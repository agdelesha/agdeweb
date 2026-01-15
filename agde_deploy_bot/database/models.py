from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, BigInteger
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    """Клиент бота"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    registered_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    servers = relationship("Server", back_populates="owner", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User {self.telegram_id} ({self.username})>"


class Server(Base):
    """Сервер клиента"""
    __tablename__ = 'servers'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    ip = Column(String, nullable=False)
    password = Column(String, nullable=False)
    name = Column(String, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    
    # Статусы установок
    wg_installed = Column(Boolean, default=False)
    awg_installed = Column(Boolean, default=False)
    v2ray_installed = Column(Boolean, default=False)
    vpn_bot_installed = Column(Boolean, default=False)
    
    owner = relationship("User", back_populates="servers")
    
    def __repr__(self):
        return f"<Server {self.ip} (user_id={self.user_id})>"
