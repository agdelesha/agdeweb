from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base

DATABASE_URL = "sqlite:///agde_deploy.db"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Инициализация базы данных"""
    Base.metadata.create_all(engine)


def get_session():
    """Получить сессию БД"""
    return SessionLocal()
