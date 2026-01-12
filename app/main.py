from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqladmin import Admin, ModelView, BaseView, expose
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import engine, Base, get_db, AsyncSessionLocal
from app.models import User, Assistant, Message, Product
from app.security import validate_telegram_data
from app.services import get_ai_response
from app.metrics import DashboardMetrics
from pydantic import BaseModel
from markupsafe import Markup

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Таблицы теперь создаются через Alembic
    yield
    # Shutdown: Close engine connections
    await engine.dispose()

# 1. Создаем приложение
app = FastAPI(lifespan=lifespan)

# --- ADMIN PANEL ---
class DashboardAdmin(BaseView):
    name = "Дашборд"
    icon = "fa-solid fa-chart-line"

    @expose("/dashboard", methods=["GET"])
    async def report_page(self, request: Request):
        async with AsyncSessionLocal() as session:
            metrics_service = DashboardMetrics(session)
            
            metrics = {
                "dau": await metrics_service.get_dau(),
                "mau": await metrics_service.get_mau(),
                "retention": await metrics_service.get_retention(),
                "assistant_popularity": await metrics_service.get_assistant_popularity(),
                "message_volume": await metrics_service.get_message_volume(),
                "conversion_rate": await metrics_service.get_conversion_rate(),
                "ctr_stats": await metrics_service.get_ctr_stats()
            }

        return await self.templates.TemplateResponse(request, "dashboard.html", context={"metrics": metrics})

# Инициализируем админку сразу после создания app
# index_view не поддерживается в конструкторе этой версии sqladmin, используем add_view
admin = Admin(app, engine, base_url="/admin", templates_dir="app/templates")

class UserAdmin(ModelView, model=User):
    column_list = [User.tg_id, User.username, User.created_at]
    can_view_details = True # Чтобы при нажатии на "глаз" (Просмотр) открывались детали
    column_details_list = [User.tg_id, User.username, "all_messages"] # В деталях показываем ID, Имя и ссылку на сообщения
    
    column_labels = {
        "all_messages": "История сообщений"
    }

    column_formatters = {
        "all_messages": lambda m, a: Markup(
            f'<a href="/admin/message/list?search={m.tg_id}">Открыть историю переписки</a>'
        )
    }

    column_formatters_detail = {
        "all_messages": lambda m, a: Markup(
            f'<a href="/admin/message/list?search={m.tg_id}">Открыть историю переписки</a>'
        )
    }

# Общий раздел "История переписки"
class MessageAdmin(ModelView, model=Message):
    name = "Сообщение"
    name_plural = "История переписки"
    icon = "fa-solid fa-comments"

    # Какие колонки показывать в общей таблице
    column_list = [
        Message.id, 
        Message.user_id, 
        Message.created_at,
        Message.assistant_slug, 
        Message.role, 
        Message.content
    ]

    # Включаем перенос текста (text-wrap) ---
    column_formatters = {
        Message.content: lambda m, a: Markup(
            f'<div style="white-space: pre-wrap; min-width: 200px; max-width: 400px;">{m.content}</div>'
        ) if m.content else ""
    }

    # Возможность искать по ID юзера или тексту
    column_searchable_list = [
        Message.user_id, 
        Message.content, 
        Message.assistant_slug
    ]
    
    # Сортировка: новые сверху
    column_default_sort = ("id", True)
    
    can_view_details = True
    column_details_list = [
        Message.id, 
        Message.user_id, 
        Message.created_at,
        Message.assistant_slug, 
        Message.role, 
        Message.content,
        Message.user
    ]
    can_create = False
    can_edit = False
    can_delete = True
    
class AssistantAdmin(ModelView, model=Assistant):
    column_list = [Assistant.slug, Assistant.name]
    
    
    form_include_pk = True 
    
    
    form_columns = [Assistant.slug, Assistant.name, Assistant.description, Assistant.icon_emoji, Assistant.welcome_message, Assistant.openrouter_preset]
    
    name = "Ассистент"
    name_plural = "Ассистенты"

class ProductAdmin(ModelView, model=Product):
    column_list = [Product.name, Product.keywords, Product.target_assistants, Product.impressions, Product.clicks, "ctr"]
    
    column_labels = {
        "impressions": "Показы",
        "clicks": "Клики",
        "ctr": "CTR (%)"
    }
    
    form_columns = [
        Product.name, 
        Product.keywords, 
        Product.ad_text, 
        Product.link, 
        Product.is_active, 
        Product.target_assistants 
    ]
    
    column_formatters = {
        "ctr": lambda m, a: f"{round((m.clicks / m.impressions * 100), 2) if m.impressions > 0 else 0}%"
    }

admin.add_view(DashboardAdmin) 
admin.add_view(UserAdmin)
admin.add_view(AssistantAdmin)
admin.add_view(ProductAdmin)
admin.add_view(MessageAdmin)

# --- API ---
class ChatRequest(BaseModel):
    assistant_slug: str
    text: str

@app.get("/api/assistants")
async def get_assistants(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Assistant))
    return result.scalars().all()

@app.get("/api/history")
async def get_history(
    assistant_slug: str,
    limit: int = 20,
    offset: int = 0,
    request: Request = None,
    db: AsyncSession = Depends(get_db)
):
    # 1. Валидация (Mock)
    # init_data = request.headers.get("X-Telegram-Init-Data")
    # user_data = validate_telegram_data(init_data)
    user_data = {"id": 12346, "username": "test_user2"}
    user_id = user_data["id"]

    # 2. Загрузка истории
    history_q = await db.execute(
        select(Message)
        .where(Message.user_id == user_id, Message.assistant_slug == assistant_slug)
        .order_by(Message.id.desc())
        .offset(offset)
        .limit(limit)
    )
    history = history_q.scalars().all()
    
    return [
        {"role": msg.role, "content": msg.content, "id": msg.id}
        for msg in history
    ]

@app.get("/api/click")
async def track_click(product_id: int, db: AsyncSession = Depends(get_db)):
    """
    Эндпоинт для трекинга кликов.
    1. Ищет товар по ID.
    2. Увеличивает счетчик кликов.
    3. Редиректит пользователя на целевую ссылку.
    """
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    product.clicks += 1
    await db.commit()
    
    return RedirectResponse(url=product.link)

@app.post("/api/chat")
async def chat(
    req: ChatRequest, 
    request: Request, 
    db: AsyncSession = Depends(get_db)
):
    # Мут валидации для тестов
    # 1. Валидация
    # init_data = request.headers.get("X-Telegram-Init-Data")
    # Если тестируете локально без Телеграма, закомментируйте строку ниже:
    # user_data = validate_telegram_data(init_data) 
    user_data = {"id": 12346, "username": "test_user2"} # Раскомментируйте для теста в браузере
    
    user_id = user_data["id"]

    # 2. Создаем/обновляем юзера
    user = await db.get(User, user_id)
    if not user:
        user = User(tg_id=user_id, username=user_data.get("username", "Anon"))
        db.add(user)
        await db.commit()

    # 3. Загрузка истории
    history_q = await db.execute(
        select(Message)
        .where(Message.user_id == user_id, Message.assistant_slug == req.assistant_slug)
        .order_by(Message.id.desc())
        .limit(10)
    )
    history = history_q.scalars().all()[::-1]

    # 4. Ответ ИИ
    ai_answer = await get_ai_response(req.text, req.assistant_slug, history, db, user_id=user_id)

    # 5. Сохранение
    msg_user = Message(user_id=user_id, assistant_slug=req.assistant_slug, role="user", content=req.text)
    msg_ai = Message(user_id=user_id, assistant_slug=req.assistant_slug, role="assistant", content=ai_answer)
    db.add_all([msg_user, msg_ai])
    await db.commit()

    return {"response": ai_answer}

# --- ФРОНТЕНД ---
# Важно: Сначала монтируем статику по пути /static
app.mount("/static", StaticFiles(directory="static"), name="static")

# Потом отдаем index.html на главной странице
@app.get("/")
async def read_root():
    return FileResponse("static/index.html")