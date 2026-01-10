from openai import AsyncOpenAI
from app.config import settings
from sqlalchemy.future import select
from sqlalchemy import or_
from app.models import Message, Product, Assistant

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=settings.OPENROUTER_API_KEY,
)

async def get_products_context(assistant_slug: str, session, history: list, user_id: int = None) -> str:
    """
    Формирует инструкцию с партнерскими товарами,
    доступными для конкретного ассистента.
    """
    # 1. Забираем ВСЕ активные товары
    result = await session.execute(select(Product).where(Product.is_active == True))
    all_products = result.scalars().all()
    
    if not all_products:
        return ""

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
        return ""

    # 2.1 ПРОВЕРКА: Были ли уже рекомендации в последних 10 сообщениях?
    # Проверяем переданную историю (она уже ограничена 10 сообщениями в main.py)
    for msg in history:
        if msg.role == "assistant":
            for p in allowed_products:
                if p.link in msg.content:
                    # Если ссылка на товар уже была в истории, не добавляем рекламу снова
                    return ""

    # 3. Формируем текст только из разрешенных товаров
    products_list = []
    for p in allowed_products:
        products_list.append(
            f"- ТОВАР: {p.name}. "
            f"КОНТЕКСТ: {p.keywords} {p.ad_text}. "
            f"ССЫЛКА: {p.link}"
        )
    
    products_str = "\n".join(products_list)
    
    return (
        f"\n[ИНСТРУКЦИЯ ПО РЕКЛАМЕ]\n"
        f"У тебя есть доступ к партнерским товарам:\n{products_str}\n"
        f"ВАЖНО: Если контекст последнего сообщения подходит, порекомендуй товар нативно. "
        f"Используй Markdown для ссылок: [Название](ссылка)."
    )

async def get_ai_response(user_text: str, assistant_slug: str, history: list, session, user_id: int = None):
    # 1. Получаем контекст товаров (рекламная инструкция)
    ad_system_prompt = await get_products_context(assistant_slug, session, history, user_id)
    
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
    
    return response.choices[0].message.content