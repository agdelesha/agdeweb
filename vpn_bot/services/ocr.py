import re
import logging
from typing import Optional
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
        r'(\d{2,3})\s*(?:00|,00|\.00)',
    ]
    
    EXPECTED_AMOUNTS = [100, 200, 300]
    
    @classmethod
    async def extract_amount(cls, image_bytes: bytes) -> Optional[dict]:
        if not OCR_AVAILABLE:
            logger.warning("OCR не доступен (pytesseract/pillow не установлены)")
            return None
        
        try:
            image = Image.open(BytesIO(image_bytes))
            
            text = pytesseract.image_to_string(image, lang='rus+eng')
            
            logger.info(f"OCR результат: {text[:200]}...")
            
            amounts_found = []
            for pattern in cls.AMOUNT_PATTERNS:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    try:
                        amount = int(match)
                        if amount in cls.EXPECTED_AMOUNTS:
                            amounts_found.append(amount)
                    except ValueError:
                        continue
            
            all_numbers = re.findall(r'\b(\d{2,3})\b', text)
            for num in all_numbers:
                try:
                    amount = int(num)
                    if amount in cls.EXPECTED_AMOUNTS and amount not in amounts_found:
                        amounts_found.append(amount)
                except ValueError:
                    continue
            
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
