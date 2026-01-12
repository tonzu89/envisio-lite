from openai import AsyncOpenAI
from app.config import settings
from sqlalchemy.future import select
from sqlalchemy import or_
from app.models import Message, Product, Assistant
import re

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=settings.OPENROUTER_API_KEY,
)

# Базовый URL для редиректов (должен быть настроен в config, но для простоты захардкодим или возьмем локальный)
REDIRECT_BASE_URL = "http://localhost:8000/api/click"

async def get_products_context(assistant_slug: str, session, history: list, user_id: int = None) -> tuple[str, list[Product]]:
    """
    Формирует инструкцию с партнерскими товарами,
    доступными для конкретного ассистента.
    Возвращает (текст_инструкции, список_рекомендованных_товаров).
    """
    # 1. Забираем ВСЕ активные товары
    result = await session.execute(select(Product).where(Product.is_active == True))
    all_products = result.scalars().all()
    
    if not all_products:
        return "", []

    # 2. ФИЛЬТРАЦИЯ
    allowed_products = []
    for p in all_products:
        # Если поле пустое — товар для всех
        if not p.target_assistants:
            allowed_products.append(p)
        # Если поле заполнено, проверяем, есть ли там наш текущий assistant_slug
        elif assistant_slug in p.target_assistants:
            allowed_products.append(p)

    if not allowed_products:
        return "", []

    # 2.1 ПРОВЕРКА: Были ли уже рекомендации в последних 10 сообщениях?
    # Проверяем переданную историю (она уже ограничена 10 сообщениями в main.py)
    # Важно: тут мы проверяем оригинальные ссылки, но ИИ теперь будет использовать редирект-ссылки.
    # Поэтому лучше искать по ID товара в ссылке редиректа, но пока оставим простую проверку по имени или ID.
    # Но для простоты пока оставим как есть - если ИИ недавно что-то рекомендовал.
    
    # Чтобы избежать дублей, мы можем проверить, есть ли ссылки на редирект в истории.
    for msg in history:
        if msg.role == "assistant" and "/api/click" in msg.content:
             return "", []

    # 3. Формируем текст только из разрешенных товаров
    products_list = []
    for p in allowed_products:
        # Генерируем ссылку для трекинга: /api/click?product_id=123&user_id=456
        tracking_link = f"{REDIRECT_BASE_URL}?product_id={p.id}"
        if user_id:
            tracking_link += f"&user_id={user_id}"
        
        products_list.append(
            f"- ТОВАР: {p.name}. "
            f"КОНТЕКСТ: {p.keywords} {p.ad_text}. "
            f"ССЫЛКА: {tracking_link}"
        )
    
    products_str = "\n".join(products_list)
    
    prompt = (
        f"\n[ИНСТРУКЦИЯ ПО РЕКЛАМЕ]\n"
        f"У тебя есть доступ к партнерским товарам:\n{products_str}\n"
        f"ВАЖНО: Если контекст последнего сообщения подходит, порекомендуй товар нативно. "
        f"Используй Markdown для ссылок: [Название](ССЫЛКА_ИЗ_ОПИСАНИЯ). "
        f"Не выдумывай ссылки, бери только те, что указаны выше."
    )
    
    return prompt, allowed_products

async def get_ai_response(user_text: str, assistant_slug: str, history: list, session, user_id: int = None):
    # 1. Получаем контекст товаров (рекламная инструкция)
    ad_system_prompt, allowed_products = await get_products_context(assistant_slug, session, history, user_id)
    
    # 2. Получаем данные ассистента, чтобы узнать его ПРЕСЕТ
    result = await session.execute(select(Assistant).where(Assistant.slug == assistant_slug))
    assistant = result.scalars().first()
    
    # Если в базе есть пресет (например, "@preset/agro-v1"), используем его.
    # Если нет — используем запасную модель.
    model_id = assistant.openrouter_preset if assistant and assistant.openrouter_preset else "openai/gpt-4o-mini"

    # 3. Формируем историю сообщений
    messages = []
   
    # Добавляем инструкцию по рекламе как системное сообщение.
    # OpenRouter сам объединит её с системным промптом, зашитым внутри пресета.
    if ad_system_prompt:
        messages.append({
                "role": "system", 
                "content": ad_system_prompt
            })
    
    # Добавляем историю переписки
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})
    
    # Добавляем текущее сообщение пользователя
    messages.append({"role": "user", "content": user_text})

    # 4. Запрос к ИИ
    response = await ai_client.chat.completions.create(
        model=model_id, # <-- Сюда подставляется пресет (напр. @preset/agro-v1)
        messages=messages,
        temperature=0.7,
        extra_headers={
            "HTTP-Referer": "https://telegram.org", 
            "X-Title": "Envisio"
        }
    )
    
    ai_content = response.choices[0].message.content

    # 5. Трекинг показов (Impressions)
    # Проверяем, вставил ли ИИ ссылку на товар в свой ответ.
    # Ищем вхождения "/api/click?product_id=X"
    if ai_content:
        # Простое регулярное выражение для поиска ID
        # Ссылка вида: .../api/click?product_id=123...
        found_ids = re.findall(r"product_id=(\d+)", ai_content)
        if found_ids:
            unique_ids = set(found_ids)
            for pid in unique_ids:
                # Находим товар в allowed_products (чтобы не делать лишний запрос в БД, хотя объекты могут быть detached)
                # Надежнее сделать update запрос
                try:
                    pid_int = int(pid)
                    # Инкремент impressions
                    product_to_update = await session.get(Product, pid_int)
                    if product_to_update:
                        product_to_update.impressions += 1
                        # session.add(product_to_update) - не обязательно, если объект привязан
                except ValueError:
                    pass
            # Commit будет сделан вызывающим кодом (в main.py) вместе с сохранением сообщения
            
    return ai_content