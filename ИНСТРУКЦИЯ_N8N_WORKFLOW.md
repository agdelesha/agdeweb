# Инструкция по настройке Telegram + DeepSeek в n8n

## Файл workflow
Файл находится здесь: `/Users/agdelesha/Desktop/myScripts/wgScript/n8n_telegram_deepseek_workflow.json`

## Шаг 1: Настройка credentials в n8n

### 1.1 Telegram Bot Token
1. Откройте https://agden8n.ru (подождите пока DNS обновится)
2. Зайдите в **Settings** → **Credentials** → **Add Credential**
3. Найдите **Telegram API**
4. Введите ваш Bot Token от @BotFather
5. Сохраните как "VPN Bot"

### 1.2 DeepSeek API
1. В **Settings** → **Credentials** → **Add Credential**
2. Выберите **HTTP Header Auth** (или создайте custom credential)
3. Настройте так:
   - **Name**: DeepSeek API
   - **Header Name**: `Authorization`
   - **Header Value**: `Bearer ВАШ_DEEPSEEK_API_KEY`
4. Сохраните

**Альтернатива для DeepSeek:**
Если нет готового типа credential, используйте **HTTP Request** с ручной настройкой:
- В узле "DeepSeek API" добавьте header:
  - Name: `Authorization`
  - Value: `Bearer ВАШ_API_KEY`

## Шаг 2: Импорт workflow

1. Откройте n8n: https://agden8n.ru
2. Нажмите **+** (Create new workflow)
3. Нажмите на **три точки** (⋮) в правом верхнем углу
4. Выберите **Import from File**
5. Загрузите файл `n8n_telegram_deepseek_workflow.json`
6. Workflow появится на канвасе

**⚠️ ВАЖНО:** После импорта в каждом Telegram узле нужно **вручную выбрать credential**:
- Откройте каждый узел с Telegram (их 4 штуки)
- В поле **Credential for Telegram API** выберите ваш созданный credential
- Это нужно сделать для: "Telegram Trigger", "Отправить статус", "Отправить ответ AI", "Отправить ошибку"

## Шаг 3: Настройка узлов

### 3.1 Telegram Trigger
1. Откройте узел "Telegram Trigger"
2. В поле **Credential** выберите "VPN Bot" (созданный в шаге 1.1)
3. Убедитесь что выбрано **Updates**: `message`
4. Сохраните

### 3.2 DeepSeek API
1. Откройте узел "DeepSeek API"
2. Проверьте URL: `https://api.deepseek.com/v1/chat/completions`
3. В **Authentication** выберите credential DeepSeek (из шага 1.2)
4. Или добавьте header вручную:
   - Header: `Authorization`
   - Value: `Bearer ВАШ_DEEPSEEK_API_KEY`
5. Проверьте body параметры (уже настроены)

### 3.3 Остальные Telegram узлы
Для узлов:
- "Отправить статус"
- "Отправить ответ AI"
- "Отправить ошибку"

В каждом выберите тот же credential "VPN Bot"

## Шаг 4: Активация workflow

1. Нажмите **Save** (сохранить workflow)
2. Переключите тумблер **Active** в положение ON (вверху справа)
3. Webhook автоматически зарегистрируется в Telegram

## Шаг 5: Тестирование

1. Откройте ваш VPN бот в Telegram
2. Отправьте любое текстовое сообщение, например: "Привет, как подключиться к VPN?"
3. Бот должен:
   - Отправить "Обрабатываю ваш запрос... ⏳"
   - Получить ответ от DeepSeek
   - Отправить ответ пользователю

## Как работает workflow

```
1. [Telegram Trigger] → Получает сообщение от пользователя
2. [Фильтр] → Проверяет что это текстовое сообщение
3. [Отправить статус] → Отправляет "Обрабатываю..."
4. [DeepSeek API] → Отправляет запрос к AI с контекстом VPN бота
5. [Обработать ответ] → Извлекает текст ответа
6. [Отправить ответ AI] → Отправляет ответ пользователю
7. [Отправить ошибку] → (если что-то пошло не так)
```

## Настройка системного промпта

Чтобы изменить поведение AI, отредактируйте узел **DeepSeek API**:

В параметре `messages` найдите `system` роль:
```json
{
  "role": "system",
  "content": "Ты помощник VPN бота. Отвечай кратко и по делу на русском языке. Помогай пользователям с вопросами о VPN сервисе."
}
```

Измените `content` под ваши нужды, например:
- "Ты техподдержка VPN сервиса. Помогай с подключением, тарифами и проблемами."
- "Отвечай максимально кратко, не более 2-3 предложений."

## Параметры DeepSeek

В узле **DeepSeek API** можно настроить:
- `temperature` (0.0-2.0): креативность ответов (0.7 по умолчанию)
- `max_tokens`: максимальная длина ответа (500 по умолчанию)
- `model`: модель AI (`deepseek-chat` или `deepseek-coder`)

## Получение DeepSeek API ключа

1. Зайдите на https://platform.deepseek.com
2. Зарегистрируйтесь / войдите
3. Перейдите в **API Keys**
4. Создайте новый ключ
5. Скопируйте и используйте в n8n

## Troubleshooting

### Бот не отвечает
- Проверьте что workflow **Active** (зелёный тумблер)
- Проверьте credentials Telegram
- Откройте **Executions** в n8n и посмотрите логи

### Ошибка DeepSeek API
- Проверьте API ключ
- Убедитесь что есть баланс на аккаунте DeepSeek
- Проверьте URL: `https://api.deepseek.com/v1/chat/completions`

### Webhook не работает
- Убедитесь что домен доступен по HTTPS: https://agden8n.ru
- Telegram требует HTTPS для webhook
- Проверьте что порт 443 открыт

## Дополнительные возможности

### Добавить историю диалога
Можно сохранять контекст разговора в переменных n8n или базе данных

### Добавить команды
В фильтре можно проверять `/start`, `/help` и отвечать по-разному

### Логирование
Добавьте узел для сохранения всех запросов в Google Sheets или базу данных

---

**Готово!** Теперь ваш VPN бот будет автоматически отвечать на вопросы пользователей через DeepSeek AI.
