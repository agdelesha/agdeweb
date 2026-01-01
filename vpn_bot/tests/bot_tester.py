"""
Автотестер для VPN бота.
Использует Pyrogram для эмуляции реального пользователя.

Для запуска:
1. pip install pyrogram tgcrypto
2. Получить api_id и api_hash на https://my.telegram.org
3. Запустить: python bot_tester.py

При первом запуске попросит ввести номер телефона и код из Telegram.
"""

import asyncio
import sys
import os
from datetime import datetime
from typing import Optional, List, Tuple

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from pyrogram import Client
    from pyrogram.types import Message, CallbackQuery
    from pyrogram.errors import MessageNotModified, ButtonDataInvalid
except ImportError:
    print("❌ Установите pyrogram: pip install pyrogram tgcrypto")
    sys.exit(1)


# ============ НАСТРОЙКИ ============
API_ID = None  # Получить на https://my.telegram.org
API_HASH = None  # Получить на https://my.telegram.org
BOT_USERNAME = "@agdevpnbot"  # Username бота для тестирования
SESSION_NAME = "bot_tester"
# ===================================


class BotTester:
    def __init__(self, api_id: int, api_hash: str, bot_username: str):
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_username = bot_username
        self.client: Optional[Client] = None
        self.results: List[Tuple[str, bool, str]] = []
        self.current_message: Optional[Message] = None
    
    async def start(self):
        """Запуск клиента"""
        self.client = Client(SESSION_NAME, api_id=self.api_id, api_hash=self.api_hash)
        await self.client.start()
        print(f"✅ Клиент запущен как {(await self.client.get_me()).first_name}")
    
    async def stop(self):
        """Остановка клиента"""
        if self.client:
            await self.client.stop()
    
    async def send_command(self, command: str, wait_seconds: float = 2.0) -> Optional[Message]:
        """Отправить команду боту и дождаться ответа"""
        try:
            await self.client.send_message(self.bot_username, command)
            await asyncio.sleep(wait_seconds)
            
            # Получаем последнее сообщение от бота
            async for msg in self.client.get_chat_history(self.bot_username, limit=1):
                if msg.from_user and msg.from_user.is_bot:
                    self.current_message = msg
                    return msg
            return None
        except Exception as e:
            print(f"❌ Ошибка отправки команды {command}: {e}")
            return None
    
    async def click_button(self, button_text: str = None, callback_data: str = None, 
                           wait_seconds: float = 1.5) -> Tuple[bool, str]:
        """Нажать кнопку в текущем сообщении"""
        if not self.current_message or not self.current_message.reply_markup:
            return False, "Нет сообщения с кнопками"
        
        try:
            # Ищем кнопку
            for row in self.current_message.reply_markup.inline_keyboard:
                for button in row:
                    if button_text and button_text in button.text:
                        await self.current_message.click(button.text)
                        await asyncio.sleep(wait_seconds)
                        
                        # Обновляем текущее сообщение
                        async for msg in self.client.get_chat_history(self.bot_username, limit=1):
                            if msg.from_user and msg.from_user.is_bot:
                                self.current_message = msg
                        
                        return True, f"Нажата кнопка: {button.text}"
                    
                    if callback_data and button.callback_data == callback_data:
                        await self.current_message.click(callback_data)
                        await asyncio.sleep(wait_seconds)
                        
                        async for msg in self.client.get_chat_history(self.bot_username, limit=1):
                            if msg.from_user and msg.from_user.is_bot:
                                self.current_message = msg
                        
                        return True, f"Нажата кнопка: {button.text}"
            
            return False, f"Кнопка не найдена: {button_text or callback_data}"
        
        except MessageNotModified:
            return True, "Сообщение не изменилось (это нормально)"
        except Exception as e:
            return False, f"Ошибка: {e}"
    
    async def test_command(self, command: str, expected_text: str = None) -> bool:
        """Тест команды"""
        print(f"\n🔹 Тест команды: {command}")
        msg = await self.send_command(command)
        
        if not msg:
            self.results.append((f"Команда {command}", False, "Нет ответа"))
            print(f"  ❌ Нет ответа от бота")
            return False
        
        if expected_text and expected_text not in (msg.text or msg.caption or ""):
            self.results.append((f"Команда {command}", False, f"Ожидался текст: {expected_text}"))
            print(f"  ❌ Ожидался текст: {expected_text}")
            return False
        
        self.results.append((f"Команда {command}", True, msg.text[:50] if msg.text else "OK"))
        print(f"  ✅ Ответ получен")
        return True
    
    async def test_button(self, button_text: str, expected_text: str = None) -> bool:
        """Тест нажатия кнопки"""
        print(f"  🔸 Нажатие кнопки: {button_text}")
        success, result = await self.click_button(button_text=button_text)
        
        if not success:
            self.results.append((f"Кнопка '{button_text}'", False, result))
            print(f"    ❌ {result}")
            return False
        
        if expected_text and self.current_message:
            text = self.current_message.text or self.current_message.caption or ""
            if expected_text not in text:
                self.results.append((f"Кнопка '{button_text}'", False, f"Ожидался: {expected_text}"))
                print(f"    ❌ Ожидался текст: {expected_text}")
                return False
        
        self.results.append((f"Кнопка '{button_text}'", True, "OK"))
        print(f"    ✅ OK")
        return True
    
    async def run_all_tests(self):
        """Запуск всех тестов"""
        print("\n" + "=" * 50)
        print("🧪 АВТОТЕСТИРОВАНИЕ VPN БОТА")
        print("=" * 50)
        
        # === ТЕСТЫ ПОЛЬЗОВАТЕЛЬСКИХ КОМАНД ===
        print("\n📱 ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ")
        print("-" * 30)
        
        # /start
        await self.test_command("/start", "Привет")
        
        # Главное меню
        await self.test_button("🔑 Мои конфиги")
        await self.test_button("◀️ Назад")
        
        await self.test_button("💳 Подписка")
        await self.test_button("◀️ Назад")
        
        await self.test_button("👥 Рефералы")
        await self.test_button("◀️ Назад")
        
        await self.test_button("❓ Помощь")
        await self.test_button("◀️ Назад")
        
        # === ТЕСТЫ АДМИН-ПАНЕЛИ ===
        print("\n🔧 АДМИН-ПАНЕЛЬ")
        print("-" * 30)
        
        await self.test_command("/admin", "Админ-панель")
        
        # Статистика пользователей
        await self.test_button("📊 Статистика")
        await self.test_button("🔄 Обновить")
        await self.test_button("◀️ В меню")
        
        # Серверы
        await self.test_button("🖥 Серверы")
        await self.test_button("◀️ Назад")
        
        # Пользователи
        await self.test_button("👥 Пользователи")
        await self.test_button("◀️ Назад")
        
        # Рассылка
        await self.test_button("📢 Рассылка")
        await self.test_button("❌ Отмена")
        
        # Настройки
        await self.test_button("⚙️ Настройки")
        await self.test_button("◀️ Назад")
        
        # Возврат в главное меню
        await self.send_command("/start")
        
        # === РЕЗУЛЬТАТЫ ===
        self.print_results()
    
    def print_results(self):
        """Вывод результатов тестирования"""
        print("\n" + "=" * 50)
        print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
        print("=" * 50)
        
        passed = sum(1 for _, success, _ in self.results if success)
        failed = sum(1 for _, success, _ in self.results if not success)
        
        print(f"\n✅ Пройдено: {passed}")
        print(f"❌ Провалено: {failed}")
        print(f"📈 Всего: {len(self.results)}")
        
        if failed > 0:
            print("\n❌ ПРОВАЛИВШИЕСЯ ТЕСТЫ:")
            for name, success, msg in self.results:
                if not success:
                    print(f"  • {name}: {msg}")
        
        print("\n" + "=" * 50)
        if failed == 0:
            print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        else:
            print(f"⚠️ {failed} ТЕСТОВ ПРОВАЛЕНО")
        print("=" * 50)


async def main():
    # Проверяем настройки
    if not API_ID or not API_HASH:
        print("❌ Укажите API_ID и API_HASH в начале файла!")
        print("   Получить можно на https://my.telegram.org")
        
        # Интерактивный ввод
        try:
            api_id = int(input("\nВведите API_ID: "))
            api_hash = input("Введите API_HASH: ")
        except (ValueError, KeyboardInterrupt):
            print("\n❌ Отменено")
            return
        
        tester = BotTester(api_id, api_hash, BOT_USERNAME)
    else:
        tester = BotTester(API_ID, API_HASH, BOT_USERNAME)
    
    try:
        await tester.start()
        await tester.run_all_tests()
    finally:
        await tester.stop()


if __name__ == "__main__":
    asyncio.run(main())
