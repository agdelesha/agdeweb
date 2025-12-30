"""
Сервис мониторинга uptime
- Проверяет доступность WireGuard серверов
- Отправляет уведомления в Telegram при проблемах
- Интегрируется с Healthchecks.io для внешнего мониторинга
"""

import asyncio
import aiohttp
import logging
import subprocess
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Конфигурация
ADMIN_TELEGRAM_ID = 906888481  # @agdelesha
HEALTHCHECKS_PING_URL = None  # Будет установлен после регистрации на healthchecks.io
CHECK_INTERVAL_SECONDS = 300  # Проверка каждые 5 минут
ALERT_COOLDOWN_MINUTES = 30  # Не спамить алертами чаще чем раз в 30 минут


@dataclass
class ServerStatus:
    """Статус сервера"""
    host: str
    is_up: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    last_check: datetime = None
    last_alert: datetime = None


class UptimeMonitor:
    """Мониторинг uptime серверов"""
    
    def __init__(self, bot=None):
        self.bot = bot
        self.server_statuses: Dict[str, ServerStatus] = {}
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
    
    async def ping_server(self, host: str, port: int = 51820) -> ServerStatus:
        """Проверяет доступность сервера через ping"""
        try:
            # Используем системный ping (более надёжно)
            process = await asyncio.create_subprocess_exec(
                'ping', '-c', '3', '-W', '5', host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            
            if process.returncode == 0:
                # Парсим latency из вывода ping
                output = stdout.decode()
                latency = None
                for line in output.split('\n'):
                    if 'avg' in line or 'average' in line:
                        # Формат: min/avg/max/mdev = 1.234/2.345/3.456/0.567 ms
                        parts = line.split('=')
                        if len(parts) >= 2:
                            times = parts[1].strip().split('/')
                            if len(times) >= 2:
                                try:
                                    latency = float(times[1])
                                except ValueError:
                                    pass
                
                return ServerStatus(
                    host=host,
                    is_up=True,
                    latency_ms=latency,
                    last_check=datetime.utcnow()
                )
            else:
                return ServerStatus(
                    host=host,
                    is_up=False,
                    error="Ping failed",
                    last_check=datetime.utcnow()
                )
        except asyncio.TimeoutError:
            return ServerStatus(
                host=host,
                is_up=False,
                error="Timeout",
                last_check=datetime.utcnow()
            )
        except Exception as e:
            return ServerStatus(
                host=host,
                is_up=False,
                error=str(e),
                last_check=datetime.utcnow()
            )
    
    async def check_wireguard_interface(self, interface: str = "wg0") -> bool:
        """Проверяет что WireGuard интерфейс активен (для локального сервера)"""
        try:
            process = await asyncio.create_subprocess_exec(
                'wg', 'show', interface,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            return process.returncode == 0
        except Exception as e:
            logger.error(f"Ошибка проверки WG интерфейса: {e}")
            return False
    
    async def send_alert(self, message: str, is_recovery: bool = False):
        """Отправляет алерт в Telegram"""
        if not self.bot:
            logger.warning(f"Бот не настроен, алерт не отправлен: {message}")
            return
        
        try:
            emoji = "✅" if is_recovery else "🚨"
            await self.bot.send_message(
                ADMIN_TELEGRAM_ID,
                f"{emoji} *Мониторинг*\n\n{message}",
                parse_mode="Markdown"
            )
            logger.info(f"Алерт отправлен: {message}")
        except Exception as e:
            logger.error(f"Ошибка отправки алерта: {e}")
    
    async def ping_healthchecks(self, status: str = ""):
        """Пингует Healthchecks.io для внешнего мониторинга"""
        if not HEALTHCHECKS_PING_URL:
            return
        
        try:
            url = HEALTHCHECKS_PING_URL
            if status:
                url = f"{url}/{status}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        logger.debug("Healthchecks ping OK")
        except Exception as e:
            logger.error(f"Ошибка ping healthchecks: {e}")
    
    async def check_all_servers(self) -> List[ServerStatus]:
        """Проверяет все серверы из БД"""
        from database import async_session
        from database.models import Server
        from sqlalchemy import select
        
        results = []
        
        try:
            async with async_session() as session:
                stmt = select(Server).where(Server.is_active == True)
                result = await session.execute(stmt)
                servers = result.scalars().all()
                
                for server in servers:
                    status = await self.ping_server(server.host)
                    status.host = f"{server.name} ({server.host})"
                    
                    # Проверяем изменение статуса
                    old_status = self.server_statuses.get(server.host)
                    
                    if old_status:
                        # Сервер упал
                        if old_status.is_up and not status.is_up:
                            can_alert = True
                            if old_status.last_alert:
                                cooldown = datetime.utcnow() - old_status.last_alert
                                can_alert = cooldown > timedelta(minutes=ALERT_COOLDOWN_MINUTES)
                            
                            if can_alert:
                                await self.send_alert(
                                    f"Сервер *{server.name}* недоступен!\n"
                                    f"🌐 Host: `{server.host}`\n"
                                    f"❌ Ошибка: {status.error}"
                                )
                                status.last_alert = datetime.utcnow()
                        
                        # Сервер восстановился
                        elif not old_status.is_up and status.is_up:
                            await self.send_alert(
                                f"Сервер *{server.name}* восстановлен!\n"
                                f"🌐 Host: `{server.host}`\n"
                                f"⏱ Latency: {status.latency_ms:.1f}ms" if status.latency_ms else "",
                                is_recovery=True
                            )
                    
                    self.server_statuses[server.host] = status
                    results.append(status)
                
        except Exception as e:
            logger.error(f"Ошибка проверки серверов: {e}")
        
        return results
    
    async def _monitor_loop(self):
        """Основной цикл мониторинга"""
        logger.info("Мониторинг uptime запущен")
        
        while self.is_running:
            try:
                results = await self.check_all_servers()
                
                # Пингуем healthchecks если все серверы в норме
                all_up = all(s.is_up for s in results) if results else True
                if all_up:
                    await self.ping_healthchecks()
                else:
                    await self.ping_healthchecks("fail")
                
                # Логируем статус
                up_count = sum(1 for s in results if s.is_up)
                logger.info(f"Мониторинг: {up_count}/{len(results)} серверов онлайн")
                
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
            
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
    
    def start(self):
        """Запускает мониторинг"""
        if self.is_running:
            return
        
        self.is_running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Мониторинг uptime запущен")
    
    def stop(self):
        """Останавливает мониторинг"""
        self.is_running = False
        if self._task:
            self._task.cancel()
        logger.info("Мониторинг uptime остановлен")
    
    def get_status_report(self) -> str:
        """Возвращает текстовый отчёт о статусе серверов"""
        if not self.server_statuses:
            return "Нет данных о серверах"
        
        lines = ["📊 *Статус серверов:*\n"]
        for host, status in self.server_statuses.items():
            emoji = "🟢" if status.is_up else "🔴"
            latency = f" ({status.latency_ms:.0f}ms)" if status.latency_ms else ""
            error = f" - {status.error}" if status.error else ""
            lines.append(f"{emoji} {status.host}{latency}{error}")
        
        return "\n".join(lines)


# Глобальный экземпляр мониторинга
monitor: Optional[UptimeMonitor] = None


def get_monitor() -> Optional[UptimeMonitor]:
    """Возвращает глобальный экземпляр мониторинга"""
    return monitor


def init_monitor(bot) -> UptimeMonitor:
    """Инициализирует мониторинг с ботом"""
    global monitor
    monitor = UptimeMonitor(bot)
    return monitor
