import aiohttp
import logging
import re
from typing import Optional, Dict, List, Tuple
from collections import defaultdict
from dataclasses import dataclass

from config import DEEPSEEK_API_KEY

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


SYSTEM_PROMPT = """Ты — AI-помощник VPN сервиса agdevpn, встроенный прямо в этого бота. Отвечай кратко и по делу на русском языке.
ВАЖНО: Обращайся к пользователю на "ты", а не на "вы". Будь дружелюбным и неформальным.
ВАЖНО: НЕ используй Markdown разметку (звёздочки, подчёркивания). Пиши простым текстом.
ВАЖНО: Ты и есть этот бот. НЕ говори "в боте", "в основном боте", "нажми кнопку в боте" — просто говори "нажми кнопку" или выполняй действие сам.

## О сервисе
Мы предоставляем VPN на базе WireGuard — это современный, быстрый и безопасный VPN протокол.

## Тарифы
- Пробный период: 7 дней БЕСПЛАТНО (один раз на аккаунт)
- 30 дней: 100 рублей
- 90 дней: 200 рублей  
- 180 дней: 300 рублей

## Что такое WireGuard?
WireGuard — это современный VPN протокол нового поколения. Преимущества:
- Очень быстрый — минимальные задержки, высокая скорость
- Безопасный — использует современную криптографию (ChaCha20, Curve25519)
- Легковесный — минимальное потребление батареи на телефоне
- Простой код — всего 4000 строк (OpenVPN — 100000+), легче аудит безопасности

## Почему WireGuard не заблокируют?
1. Трафик выглядит как обычный UDP — нет характерных сигнатур, сложно отличить от игр или видеозвонков
2. Нет рукопожатия — соединение устанавливается мгновенно, нет паттернов для блокировки
3. Используется везде — крупные компании (Cloudflare, Mullvad) используют WireGuard, блокировка затронет легитимный бизнес
4. Постоянно меняющиеся IP — мы можем быстро менять серверы

## Как настроить WireGuard?
1. Скачай приложение WireGuard:
   - iPhone/iPad: App Store, поиск "WireGuard"
   - Android: Google Play, поиск "WireGuard"  
   - Windows/Mac/Linux: wireguard.com/install
2. Получи конфиг (я могу создать его прямо сейчас)
3. Импортируй конфиг в приложение (можно через QR-код или файл .conf)
4. Включи VPN одной кнопкой — готово!

## Проблемы с подключением?
- Убедись что интернет работает без VPN
- Попробуй переподключиться (выключи/включи VPN)
- Проверь что подписка активна
- Если не помогает — напиши @agdelesha

## ДЕЙСТВИЯ
Ты можешь выполнять действия за пользователя. Если пользователь хочет что-то сделать, добавь в КОНЕЦ своего ответа специальный тег:
- [ACTION:ACTIVATE_TRIAL] — активировать пробный период 7 дней и создать конфиг (только если trial_available=true И has_subscription=false)
- [ACTION:CREATE_CONFIG] — создать дополнительный конфиг (только если has_subscription=true)
- [ACTION:SHOW_TARIFFS] — показать тарифы для покупки/продления
- [ACTION:SHOW_CONFIGS] — показать конфиги пользователя
- [ACTION:SHOW_SUBSCRIPTION] — показать информацию о подписке

Примеры когда использовать действия:
- "создай конфиг" / "хочу конфиг" / "дай конфиг" (если нет подписки и trial доступен) → [ACTION:ACTIVATE_TRIAL]
- "создай конфиг" / "хочу еще конфиг" / "дополнительный конфиг" (если есть подписка) → [ACTION:CREATE_CONFIG]
- "хочу попробовать" / "хочу бесплатно" / "давай 7 дней" → [ACTION:ACTIVATE_TRIAL]
- "хочу купить" / "хочу продлить" / "какие тарифы" → [ACTION:SHOW_TARIFFS]
- "покажи конфиги" / "где мой конфиг" / "qr код" → [ACTION:SHOW_CONFIGS]
- "сколько осталось" / "моя подписка" / "когда заканчивается" → [ACTION:SHOW_SUBSCRIPTION]

Будь дружелюбным, помогай пользователям. Если не знаешь ответ — направь к @agdelesha."""


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
        lines.append("- Пробный период: ДОСТУПЕН, можно активировать 7 дней бесплатно (trial_available=true)")
    
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
        
        # Формируем системный промпт с контекстом пользователя
        full_system_prompt = SYSTEM_PROMPT
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
