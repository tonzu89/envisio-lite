from openai import AsyncOpenAI
from app.config import settings
from sqlalchemy.future import select
from app.models import Message, Product

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=settings.OPENROUTER_API_KEY,
)

async def get_products_context(assistant_slug: str, session) -> str:
    """
    Выгружает товары, доступные для конкретного ассистента,
    и формирует из них инструкцию для ИИ.
    """
    # Забираем все активные товары (можно добавить фильтр по assistant_slug, если нужно)
    result = await session.execute(select(Product).where(Product.is_active == True))
    products = result.scalars().all()
    
    if not products:
        return ""

    # Формируем список для промпта
    products_list = []
    for p in products:
        # Мы даем ИИ понять суть товара через его название и описание (ad_text)
        products_list.append(
            f"- ТОВАР ID {p.id}: {p.name}. "
            f"ОПИСАНИЕ/КОГДА ПРЕДЛАГАТЬ: {p.keywords} {p.ad_text}. "
            f"ССЫЛКА: {p.link}"
        )
    
    products_str = "\n".join(products_list)
    
    return (
        f"\n[ИНСТРУКЦИЯ ПО РЕКЛАМЕ]\n"
        f"У тебя есть доступ к партнерским товарам:\n{products_str}\n"
        f"ВАЖНОЕ ПРАВИЛО: Если (и только если) запрос пользователя по смыслу подходит под один из товаров, "
        f"ты обязан нативно порекомендовать его в ответе. Не предлагай просто так. "
        f"Если предлагаешь, обязательно дай ссылку."
    )

async def get_ai_response(user_text: str, assistant_slug: str, history: list, session):
    # 1. Получаем контекст товаров (вместо жесткого поиска)
    ad_system_prompt = await get_products_context(assistant_slug, session)
    
    # 2. Формируем историю сообщений
    messages = []
   
    # Добавляем инструкцию по рекламе в System Prompt
    messages.append({
            "role": "system", 
            "content": ad_system_prompt
        })
    
    # Добавляем историю переписки
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})
    
    # Добавляем текущее сообщение
    messages.append({"role": "user", "content": user_text})

    # 3. Запрос к ИИ
    response = await ai_client.chat.completions.create(
        model="openai/gpt-4o-mini", # Эта модель дешевая и умная, она поймет контекст
        messages=messages,
        temperature=0.7,
        extra_headers={"HTTP-Referer": "https://telegram.org", "X-Title": "ElderlyApp"}
    )
    
    return response.choices[0].message.content