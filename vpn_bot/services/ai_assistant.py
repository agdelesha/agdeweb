import aiohttp
import logging
import re
from typing import Optional, Dict, List, Tuple
from collections import defaultdict
from dataclasses import dataclass

from config import DEEPSEEK_API_KEY
from services.settings import get_prices

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# Хранилище истории диалогов: {user_id: [messages]}
conversation_history: Dict[int, List[dict]] = defaultdict(list)
MAX_HISTORY_LENGTH = 10  # Максимум 10 сообщений в памяти


@dataclass
class UserContext:
    """Контекст пользователя для AI"""
    has_subscription: bool = False
    days_left: int = 0
    trial_used: bool = False
    configs_count: int = 0


@dataclass 
class AIResponse:
    """Ответ AI с возможным действием"""
    text: str
    action: Optional[str] = None  # "activate_trial", "show_tariffs", "show_configs", "show_subscription"


SYSTEM_PROMPT_TEMPLATE = """Ты — AI-помощник сетевого сервиса, встроенный в Telegram-бота.

## ЗАПРЕЩЕНО:
- Слова: VPN, обход блокировок, анонимность, запрещённые сайты
- Обсуждение обхода ограничений
- Юридические/политические оценки

## РАЗРЕШЁННЫЕ ФОРМУЛИРОВКИ:
- "сетевой сервис", "приватное соединение", "защищённое подключение"
- "конфиг", "параметры подключения", "техподдержка"

## СТИЛЬ:
- Обращайся на "ты", будь дружелюбным
- НЕ используй Markdown — пиши простым текстом
- Ты и есть этот бот — говори "нажми кнопку", а не "в боте"
- Отвечай кратко и по делу

## О СЕРВИСЕ
Приватный сетевой сервис для стабильного и защищённого соединения.
Включает: персональные параметры подключения, инструкции, техподдержку.

## ТАРИФЫ
{tariffs_info}

## РЕФЕРАЛЬНАЯ ПРОГРАММА
Пользователи могут приглашать друзей и зарабатывать:
- Приглашённый получает скидку 50% на первую оплату
- Пригласивший получает 10% от каждого платежа друга на свой баланс
- Накопленными средствами можно ОПЛАТИТЬ ПОДПИСКУ (это важно!)
- Также можно вывести средства на карту
- Реферальную ссылку можно получить в меню "Реферальная программа"

Если спрашивают про рефералку — объясни как работает, что можно оплатить подписку накопленными средствами, и предложи получить ссылку.

## КАК ПОДКЛЮЧИТЬСЯ
1. Скачай WireGuard (App Store / Google Play / wireguard.com)
2. Получи конфиг в боте
3. Импортируй конфиг в приложение
4. Включи соединение — готово!

Подключение занимает 1-2 минуты.

## ПРОБЛЕМЫ С ПОДКЛЮЧЕНИЕМ
- Проверь интернет
- Переподключись
- Проверь подписку
- Если не помогает — напиши @agdelesha

## ПРАВИЛА
- Доступ персональный, не передавай третьим лицам
- Не используй для высоконагруженных задач
- При нарушении — доступ может быть ограничен

## ОТВЕТ НА "Это VPN?"
"Это сетевой сервис для приватного и стабильного соединения. Я помогаю с настройкой и поддержкой."

## ДЕЙСТВИЯ
Добавь в КОНЕЦ ответа тег для выполнения действия:
- [ACTION:ACTIVATE_TRIAL] — активировать пробный (если trial_available=true И has_subscription=false)
- [ACTION:CREATE_CONFIG] — создать доп. конфиг (если has_subscription=true)
- [ACTION:SHOW_TARIFFS] — показать тарифы
- [ACTION:SHOW_CONFIGS] — показать конфиги
- [ACTION:SHOW_SUBSCRIPTION] — показать подписку
- [ACTION:SHOW_REFERRAL] — показать реферальное меню

## ПРИМЕРЫ:
- "создай конфиг" (нет подписки, trial доступен) → [ACTION:ACTIVATE_TRIAL]
- "создай конфиг" (есть подписка) → [ACTION:CREATE_CONFIG]
- "хочу попробовать" / "бесплатно" → [ACTION:ACTIVATE_TRIAL]
- "хочу купить" / "продлить" / "тарифы" → [ACTION:SHOW_TARIFFS]
- "покажи конфиги" / "qr код" → [ACTION:SHOW_CONFIGS]
- "сколько осталось" / "подписка" → [ACTION:SHOW_SUBSCRIPTION]
- "реферальная программа" / "пригласить друга" / "заработать" / "оплатить баллами" → [ACTION:SHOW_REFERRAL]

Если не знаешь ответ — направь к @agdelesha."""


async def get_system_prompt() -> str:
    """Получает системный промпт с актуальными ценами из БД"""
    try:
        prices = await get_prices()
        tariffs_info = (
            f"- Пробный период: {prices.get('trial_days', 3)} дня бесплатно (один раз)\n"
            f"- 30 дней: {prices.get('price_30', 200)}₽\n"
            f"- 90 дней: {prices.get('price_90', 400)}₽\n"
            f"- 180 дней: {prices.get('price_180', 600)}₽"
        )
        return SYSTEM_PROMPT_TEMPLATE.format(tariffs_info=tariffs_info)
    except Exception as e:
        logger.error(f"Ошибка получения цен для AI: {e}")
        # Fallback с дефолтными ценами
        tariffs_info = (
            "- Пробный период: 3 дня бесплатно (один раз)\n"
            "- 30 дней: 200₽\n"
            "- 90 дней: 400₽\n"
            "- 180 дней: 600₽"
        )
        return SYSTEM_PROMPT_TEMPLATE.format(tariffs_info=tariffs_info)


def get_user_history(user_id: int) -> List[dict]:
    """Получить историю диалога пользователя"""
    return conversation_history[user_id]


def add_to_history(user_id: int, role: str, content: str):
    """Добавить сообщение в историю диалога"""
    conversation_history[user_id].append({
        "role": role,
        "content": content
    })
    # Ограничиваем историю последними MAX_HISTORY_LENGTH сообщениями
    if len(conversation_history[user_id]) > MAX_HISTORY_LENGTH * 2:
        # Удаляем старые сообщения парами (user + assistant)
        conversation_history[user_id] = conversation_history[user_id][-MAX_HISTORY_LENGTH * 2:]


def clear_history(user_id: int):
    """Очистить историю диалога пользователя"""
    conversation_history[user_id] = []


def build_context_prompt(context: UserContext) -> str:
    """Построить промпт с контекстом пользователя"""
    lines = ["\n## Информация о текущем пользователе:"]
    
    if context.has_subscription:
        lines.append(f"- Подписка: АКТИВНА, осталось {context.days_left} дней")
        lines.append(f"- Конфигов: {context.configs_count}")
    else:
        lines.append("- Подписка: НЕТ активной подписки")
    
    if context.trial_used:
        lines.append("- Пробный период: уже использован (trial_available=false)")
    else:
        lines.append("- Пробный период: ДОСТУПЕН, можно активировать 3 дня бесплатно (trial_available=true)")
    
    return "\n".join(lines)


def parse_action(response_text: str) -> Tuple[str, Optional[str]]:
    """Извлечь действие из ответа AI и вернуть чистый текст"""
    action = None
    clean_text = response_text
    
    # Ищем тег действия
    action_match = re.search(r'\[ACTION:(\w+)\]', response_text)
    if action_match:
        action = action_match.group(1).lower()
        # Убираем тег из текста
        clean_text = re.sub(r'\s*\[ACTION:\w+\]\s*', '', response_text).strip()
    
    return clean_text, action


async def get_ai_response(user_message: str, user_id: int = 0, context: Optional[UserContext] = None) -> AIResponse:
    """
    Отправляет сообщение пользователя в DeepSeek API и возвращает ответ с возможным действием
    
    Args:
        user_message: Текст сообщения от пользователя
        user_id: ID пользователя для хранения истории диалога
        context: Контекст пользователя (подписка, trial и т.д.)
        
    Returns:
        AIResponse с текстом и возможным действием
    """
    try:
        # Добавляем сообщение пользователя в историю
        add_to_history(user_id, "user", user_message)
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Формируем системный промпт с актуальными ценами и контекстом пользователя
        full_system_prompt = await get_system_prompt()
        if context:
            full_system_prompt += build_context_prompt(context)
        
        # Формируем сообщения: системный промпт + история диалога
        messages = [{"role": "system", "content": full_system_prompt}]
        messages.extend(get_user_history(user_id))
        
        payload = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 500
        }
        
        logger.info(f"DeepSeek request for user {user_id}: {user_message[:50]}...")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                DEEPSEEK_API_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"DeepSeek API error {response.status}: {error_text}")
                    # Fallback ответ при ошибке API
                    return AIResponse(
                        text="Сейчас я немного занят, но ты можешь использовать кнопки меню для навигации. "
                             "Если нужна помощь — напиши @agdelesha"
                    )
                
                data = await response.json()
                logger.info(f"DeepSeek response received for user {user_id}")
                
                if "choices" in data and len(data["choices"]) > 0:
                    raw_response = data["choices"][0]["message"]["content"]
                    if not raw_response or not raw_response.strip():
                        logger.warning(f"DeepSeek returned empty response for user {user_id}")
                        return AIResponse(
                            text="Не совсем понял вопрос. Попробуй переформулировать или используй кнопки меню."
                        )
                    # Парсим действие из ответа
                    clean_text, action = parse_action(raw_response)
                    # Добавляем чистый ответ AI в историю
                    add_to_history(user_id, "assistant", clean_text)
                    logger.info(f"DeepSeek action for user {user_id}: {action}")
                    return AIResponse(text=clean_text, action=action)
                else:
                    logger.error(f"Unexpected DeepSeek response format: {data}")
                    return AIResponse(
                        text="Произошла техническая ошибка. Используй кнопки меню или напиши @agdelesha"
                    )
                    
    except aiohttp.ClientError as e:
        logger.error(f"Network error calling DeepSeek API: {e}")
        return AIResponse(
            text="Проблема с подключением к AI. Используй кнопки меню или попробуй позже."
        )
    except Exception as e:
        logger.error(f"Unexpected error calling DeepSeek API: {e}")
        return AIResponse(
            text="Произошла ошибка. Используй кнопки меню или напиши @agdelesha"
        )
