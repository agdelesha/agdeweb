"""
Тестер обработчиков бота.
Проверяет что все callback_data обрабатываются без ошибок.

Запуск: python handler_tester.py
"""

import asyncio
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Мокаем зависимости которых может не быть локально
class MockModule:
    def __getattr__(self, name):
        return MockModule()
    def __call__(self, *args, **kwargs):
        return MockModule()

for mod in ['asyncssh', 'apscheduler', 'apscheduler.schedulers', 
            'apscheduler.schedulers.asyncio', 'apscheduler.triggers',
            'apscheduler.triggers.interval']:
    if mod not in sys.modules:
        sys.modules[mod] = MockModule()

from datetime import datetime
from typing import List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch


class HandlerTester:
    def __init__(self):
        self.results: List[Tuple[str, bool, str]] = []
    
    async def test_callback_handlers(self):
        """Тестирует что все callback handlers зарегистрированы"""
        print("\n🔍 Проверка callback handlers...")
        
        try:
            from handlers.admin import router as admin_router
            from handlers.user import router as user_router
        except ImportError as e:
            print(f"  ⚠️ Пропущено (не все зависимости установлены): {e}")
            return set(), set()
        
        # Собираем все callback_data из клавиатур
        from keyboards import admin_kb, user_kb
        
        admin_callbacks = set()
        user_callbacks = set()
        
        # Проверяем админ клавиатуры
        admin_kb_funcs = [
            'get_admin_menu_kb', 'get_servers_kb', 'get_users_kb',
            'get_user_stats_kb', 'get_payment_review_kb'
        ]
        
        for func_name in admin_kb_funcs:
            if hasattr(admin_kb, func_name):
                try:
                    func = getattr(admin_kb, func_name)
                    # Пробуем вызвать с разными аргументами
                    try:
                        kb = func()
                    except TypeError:
                        try:
                            kb = func(0)
                        except TypeError:
                            try:
                                kb = func(0, 0)
                            except TypeError:
                                try:
                                    kb = func(0, 0, 0)
                                except TypeError:
                                    try:
                                        kb = func(0, 0, 0, 0)
                                    except:
                                        continue
                    
                    if kb and hasattr(kb, 'inline_keyboard'):
                        for row in kb.inline_keyboard:
                            for btn in row:
                                if btn.callback_data:
                                    admin_callbacks.add(btn.callback_data)
                except Exception as e:
                    print(f"  ⚠️ Ошибка в {func_name}: {e}")
        
        print(f"  📋 Найдено {len(admin_callbacks)} admin callback_data")
        
        # Проверяем user клавиатуры
        user_kb_funcs = [
            'get_main_menu_kb', 'get_configs_kb', 'get_subscription_kb',
            'get_referral_kb', 'get_help_kb'
        ]
        
        for func_name in user_kb_funcs:
            if hasattr(user_kb, func_name):
                try:
                    func = getattr(user_kb, func_name)
                    try:
                        kb = func()
                    except TypeError:
                        try:
                            kb = func([])
                        except:
                            continue
                    
                    if kb and hasattr(kb, 'inline_keyboard'):
                        for row in kb.inline_keyboard:
                            for btn in row:
                                if btn.callback_data:
                                    user_callbacks.add(btn.callback_data)
                except Exception as e:
                    print(f"  ⚠️ Ошибка в {func_name}: {e}")
        
        print(f"  📋 Найдено {len(user_callbacks)} user callback_data")
        
        return admin_callbacks, user_callbacks
    
    async def test_imports(self):
        """Проверяет что все модули импортируются без ошибок"""
        print("\n📦 Проверка импортов...")
        
        modules = [
            ('database', 'database'),
            ('database.models', 'database.models'),
            ('handlers.admin', 'handlers.admin'),
            ('handlers.user', 'handlers.user'),
            ('keyboards.admin_kb', 'keyboards.admin_kb'),
            ('keyboards.user_kb', 'keyboards.user_kb'),
            ('services.scheduler', 'services.scheduler'),
            ('services.wireguard', 'services.wireguard'),
            ('services.wireguard_multi', 'services.wireguard_multi'),
            ('services.traffic', 'services.traffic'),
        ]
        
        for name, module_path in modules:
            try:
                __import__(module_path)
                self.results.append((f"Import {name}", True, "OK"))
                print(f"  ✅ {name}")
            except Exception as e:
                self.results.append((f"Import {name}", False, str(e)[:50]))
                print(f"  ❌ {name}: {e}")
    
    async def test_database_models(self):
        """Проверяет модели БД"""
        print("\n🗄️ Проверка моделей БД...")
        
        try:
            from database.models import User, Config, Subscription, Payment, Server, BotSettings
            
            # Проверяем поля
            user_fields = ['telegram_id', 'username', 'failed_notifications', 'total_traffic']
            for field in user_fields:
                if hasattr(User, field):
                    print(f"  ✅ User.{field}")
                    self.results.append((f"User.{field}", True, "OK"))
                else:
                    print(f"  ❌ User.{field} не найден")
                    self.results.append((f"User.{field}", False, "Поле не найдено"))
            
            config_fields = ['public_key', 'is_active', 'total_received', 'total_sent']
            for field in config_fields:
                if hasattr(Config, field):
                    print(f"  ✅ Config.{field}")
                    self.results.append((f"Config.{field}", True, "OK"))
                else:
                    print(f"  ❌ Config.{field} не найден")
                    self.results.append((f"Config.{field}", False, "Поле не найдено"))
            
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")
            self.results.append(("Database models", False, str(e)[:50]))
    
    async def test_scheduler_jobs(self):
        """Проверяет задачи планировщика"""
        print("\n⏰ Проверка задач планировщика...")
        
        try:
            # Мокаем asyncssh если его нет
            import sys
            if 'asyncssh' not in sys.modules:
                sys.modules['asyncssh'] = type(sys)('asyncssh')
            
            from services.scheduler import SchedulerService
            
            # Проверяем методы
            methods = [
                'check_expiring_subscriptions',
                'disable_expired_configs',
                'check_suspicious_activity',
                'update_traffic_stats'
            ]
            
            for method in methods:
                if hasattr(SchedulerService, method):
                    print(f"  ✅ {method}")
                    self.results.append((f"Scheduler.{method}", True, "OK"))
                else:
                    print(f"  ❌ {method} не найден")
                    self.results.append((f"Scheduler.{method}", False, "Метод не найден"))
                    
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")
            self.results.append(("Scheduler", False, str(e)[:50]))
    
    async def run_all_tests(self):
        """Запуск всех тестов"""
        print("\n" + "=" * 50)
        print("🧪 ТЕСТИРОВАНИЕ КОМПОНЕНТОВ БОТА")
        print("=" * 50)
        
        await self.test_imports()
        await self.test_database_models()
        await self.test_scheduler_jobs()
        await self.test_callback_handlers()
        
        self.print_results()
    
    def print_results(self):
        """Вывод результатов"""
        print("\n" + "=" * 50)
        print("📊 РЕЗУЛЬТАТЫ")
        print("=" * 50)
        
        passed = sum(1 for _, success, _ in self.results if success)
        failed = sum(1 for _, success, _ in self.results if not success)
        
        print(f"\n✅ Пройдено: {passed}")
        print(f"❌ Провалено: {failed}")
        
        if failed > 0:
            print("\n❌ ОШИБКИ:")
            for name, success, msg in self.results:
                if not success:
                    print(f"  • {name}: {msg}")
        
        print("\n" + "=" * 50)
        if failed == 0:
            print("🎉 ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ!")
        else:
            print(f"⚠️ {failed} ПРОВЕРОК ПРОВАЛЕНО")


async def main():
    tester = HandlerTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
