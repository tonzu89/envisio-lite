from fastapi import FastAPI, Depends, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqladmin import Admin, ModelView
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import engine, Base, get_db
from app.models import User, Assistant, Message, Product
from app.security import validate_telegram_data
from app.services import get_ai_response
from pydantic import BaseModel

# 1. Создаем приложение
app = FastAPI()

# --- ADMIN PANEL ---
# Инициализируем админку сразу после создания app
admin = Admin(app, engine, base_url="/admin")

class UserAdmin(ModelView, model=User):
    column_list = [User.tg_id, User.username]

# app/main.py

class AssistantAdmin(ModelView, model=Assistant):
    column_list = [Assistant.slug, Assistant.name]
    
    
    form_include_pk = True 
    
    
    form_columns = [Assistant.slug, Assistant.name, Assistant.description, Assistant.icon_emoji, Assistant.welcome_message, Assistant.openrouter_preset]
    
    name = "Ассистент"
    name_plural = "Ассистенты"

class ProductAdmin(ModelView, model=Product):
    column_list = [Product.name, Product.keywords]

admin.add_view(UserAdmin)
admin.add_view(AssistantAdmin)
admin.add_view(ProductAdmin)

# --- EVENTS ---
@app.on_event("startup")
async def init_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --- API ---
class ChatRequest(BaseModel):
    assistant_slug: str
    text: str

@app.get("/api/assistants")
async def get_assistants(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Assistant))
    return result.scalars().all()

@app.post("/api/chat")
async def chat(
    req: ChatRequest, 
    request: Request, 
    db: AsyncSession = Depends(get_db)
):
    # 1. Валидация
    init_data = request.headers.get("X-Telegram-Init-Data")
    # Если тестируете локально без Телеграма, закомментируйте строку ниже:
    user_data = validate_telegram_data(init_data) 
    # user_data = {"id": 12345, "username": "test_user"} # Раскомментируйте для теста в браузере
    
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
    ai_answer = await get_ai_response(req.text, req.assistant_slug, history, db)

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