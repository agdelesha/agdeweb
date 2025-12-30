import re
import logging
from typing import Optional, List
from io import BytesIO

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

logger = logging.getLogger(__name__)


class OCRService:
    AMOUNT_PATTERNS = [
        r'(\d+)\s*(?:руб|₽|rub|р\.?)',
        r'(?:сумма|итого|amount)[\s:]*(\d+)',
        r'(\d{2,4})\s*(?:00|,00|\.00)',
        r'[Ии]того\s*(\d+)',
        r'(\d+)\s*[РрPp]',
    ]
    
    @classmethod
    async def get_expected_amounts(cls) -> List[int]:
        """Получает ожидаемые суммы из настроек БД (с учётом скидок 50%)"""
        try:
            from services.settings import get_prices
            prices = await get_prices()
            amounts = set()
            for key in ['price_30', 'price_90', 'price_180']:
                price = prices.get(key, 0)
                if price > 0:
                    amounts.add(price)  # полная цена
                    amounts.add(price // 2)  # скидка 50%
            return list(amounts)
        except Exception as e:
            logger.error(f"Ошибка получения цен: {e}")
            return [100, 125, 200, 250, 300, 400, 500, 600]
    
    @classmethod
    async def extract_amount(cls, image_bytes: bytes, expected_amount: int = None) -> Optional[dict]:
        if not OCR_AVAILABLE:
            logger.warning("OCR не доступен (pytesseract/pillow не установлены)")
            return None
        
        try:
            image = Image.open(BytesIO(image_bytes))
            
            text = pytesseract.image_to_string(image, lang='rus+eng')
            
            logger.info(f"OCR результат: {text[:200]}...")
            
            # Получаем ожидаемые суммы из БД
            expected_amounts = await cls.get_expected_amounts()
            # Если передана конкретная ожидаемая сумма - добавляем её
            if expected_amount and expected_amount not in expected_amounts:
                expected_amounts.append(expected_amount)
            
            amounts_found = []
            for pattern in cls.AMOUNT_PATTERNS:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    try:
                        amount = int(match)
                        if amount in expected_amounts:
                            amounts_found.append(amount)
                    except ValueError:
                        continue
            
            # Ищем все числа в тексте
            all_numbers = re.findall(r'\b(\d{2,4})\b', text)
            for num in all_numbers:
                try:
                    amount = int(num)
                    if amount in expected_amounts and amount not in amounts_found:
                        amounts_found.append(amount)
                except ValueError:
                    continue
            
            # Приоритет: если есть ожидаемая сумма - она первая
            if expected_amount and expected_amount in amounts_found:
                amounts_found.remove(expected_amount)
                amounts_found.insert(0, expected_amount)
            
            return {
                "raw_text": text,
                "amounts_found": list(set(amounts_found)),
                "most_likely_amount": amounts_found[0] if amounts_found else None
            }
            
        except Exception as e:
            logger.error(f"Ошибка OCR: {e}")
            return None
    
    @classmethod
    def format_ocr_result(cls, result: Optional[dict]) -> str:
        if not result:
            return "❌ Не удалось распознать чек"
        
        if result["most_likely_amount"]:
            return f"💰 Распознанная сумма: {result['most_likely_amount']}₽"
        elif result["amounts_found"]:
            amounts = ", ".join(str(a) for a in result["amounts_found"])
            return f"💰 Найденные суммы: {amounts}₽"
        else:
            return "⚠️ Сумма не распознана автоматически"
