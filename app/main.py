from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, HTTPException, Form, UploadFile, File
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqladmin import Admin, ModelView, BaseView, expose
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import engine, Base, get_db, AsyncSessionLocal
from app.models import User, Assistant, Message, Product, UserClick
from app.security import validate_telegram_data
from app.services import get_ai_response
from app.metrics import DashboardMetrics
from pydantic import BaseModel
from markupsafe import Markup
from PIL import Image
import io
import gspread 
import os
import uuid
from oauth2client.service_account import ServiceAccountCredentials 
from starlette.responses import RedirectResponse 

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
    # 1. Список пользователей (Главная таблица) 
    column_list = [ 
        User.tg_id, 
        User.username, 
        User.created_at, 
        "msg_count",   # Виртуальная колонка (счетчик) 
        "clicks_count" # Виртуальная колонка (счетчик) 
    ] 
    
    column_labels = { 
        User.tg_id: "ID", 
        User.username: "Юзернейм", 
        User.created_at: "Регистрация", 
        "msg_count": "Сообщений", 
        "clicks_count": "Кликов", 
        "history_link": "Переписка",   # Лейбл для ссылки 
        "clicks_link": "Клики"         # Лейбл для ссылки 
    } 
 
    # 2. Детальный просмотр (Карточка юзера) 
    can_view_details = True 
    
    column_details_list = [ 
        User.tg_id, 
        User.username, 
        User.created_at, 
        "msg_count", 
        "clicks_count", 
        "last_active", 
        # --- ВМЕСТО СПИСКОВ ВСТАВЛЯЕМ НАШИ ВИРТУАЛЬНЫЕ ССЫЛКИ --- 
        "history_link", 
        "clicks_link" 
    ] 
 
    # --- ФОРМАТТЕРЫ (Логика отображения) --- 
    
    # Для счетчиков 
    def _format_msg_count(model, context): 
        # Фильтруем сообщения по роли 'user' для консистентности
        return len([m for m in model.messages if m.role == 'user']) 
         
    def _format_clicks_count(model, context): 
        # Проверка на случай, если clicks еще нет в модели 
        return len(model.clicks) if hasattr(model, 'clicks') else 0 
 
    def _format_last_active(model, context): 
        # Фильтруем по 'user' роли
        user_messages = [m for m in model.messages if m.role == 'user']
        if not user_messages: 
            return "-" 
        last_msg = max(user_messages, key=lambda m: m.id) 
        return last_msg.created_at.strftime("%Y-%m-%d %H:%M") 
 
    # Для ССЫЛОК (Самое важное) 
    def _format_history_link(model, context): 
        count = len([m for m in model.messages if m.role == 'user']) 
        # Формируем HTML ссылку. Класс btn делает её похожей на кнопку. 
        # Ссылка ведет на /admin/message/list и ставит фильтр ?search=ID 
        return Markup( 
            f'<a href="/admin/message/list?search={model.tg_id}" ' 
            f'class="btn btn-primary btn-sm">' 
            f'📂 Открыть переписку ({count})</a>' 
        ) 
 
    def _format_clicks_link(model, context): 
        count = len(model.clicks) if hasattr(model, 'clicks') else 0 
        return Markup( 
            f'<a href="/admin/user-click/list?search={model.tg_id}" ' 
            f'class="btn btn-secondary btn-sm">' 
            f'🖱️ Открыть клики ({count})</a>' 
        ) 
 
    # Подключаем форматтеры 
    column_formatters = { 
        "msg_count": _format_msg_count, 
        "clicks_count": _format_clicks_count, 
        "last_active": _format_last_active,
        User.created_at: lambda m, a: m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else ""
    } 
    
    # Для детального просмотра нужны те же форматтеры + ссылки 
    column_formatters_detail = { 
        "msg_count": _format_msg_count, 
        "clicks_count": _format_clicks_count, 
        "last_active": _format_last_active, 
        "history_link": _format_history_link, # Подключаем ссылку 1 
        "clicks_link": _format_clicks_link,    # Подключаем ссылку 2 
        User.created_at: lambda m, a: m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else ""
    } 
    
    column_sortable_list = ["tg_id", "username", "created_at"]

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
        Message.content,
        "image_preview"
    ]

    def _format_image(model, context):
        if model.image_path:
            return Markup(f'<img src="/{model.image_path}" width="50" height="50" style="object-fit: cover; border-radius: 4px;">')
        return ""

    # Включаем перенос текста (text-wrap) ---
    column_formatters = {
        Message.content: lambda m, a: Markup(
            f'<div style="white-space: pre-wrap; min-width: 200px; max-width: 400px;">{m.content}</div>'
        ) if m.content else "",
        "image_preview": _format_image
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
    
class UserClickAdmin(ModelView, model=UserClick): 
    identity = "user-click"
    name = "Клик" 
    name_plural = "История кликов" 
    icon = "fa-solid fa-hand-pointer" # Иконка пальца 
    
    column_list = [UserClick.id, UserClick.user_id, UserClick.product_id, UserClick.created_at] 
    
    # ВАЖНО: Добавляем user_id в поиск, чтобы фильтр ?search=123 работал 
    column_searchable_list = [UserClick.user_id] 
    
    column_default_sort = ("created_at", True) # Свежие сверху 
    
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
    name = "Товар" 
    name_plural = "Товары" 
    icon = "fa-solid fa-box" 
    identity = "product"

    # --- 1. ПОДКЛЮЧАЕМ НАШ ШАБЛОН --- 
    list_template = "product_list.html" 
    
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
 
    # --- 2. ЛОГИКА СИНХРОНИЗАЦИИ --- 
    @expose("/sync_google", methods=["POST"]) 
    async def sync_google(self, request: Request): 
        try: 
            # А. Подключение к Google 
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"] 
            # Убедитесь, что файл google_creds.json лежит в корне (рядом с main.py и app.db) 
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope) 
            client = gspread.authorize(creds) 
 
            # Б. Чтение таблицы 
            # ЗАМЕНИТЕ НА ВАШУ ССЫЛКУ ИЛИ ID 
            sheet_url = "https://docs.google.com/spreadsheets/d/1d4sBMQWBIPMn02EZPOrQnzo6JlfzEDDmP0lxCYO90G4" 
            sheet = client.open_by_url(sheet_url).sheet1 
             
            # Получаем все записи. Ожидаем заголовки: name, keywords, ad_text, link, target_assistants 
            records = sheet.get_all_records() 
 
            async with AsyncSessionLocal() as session: 
                count_added = 0 
                count_updated = 0 
                 
                for row in records: 
                    link = row.get('link') 
                    if not link: 
                         continue 
                         
                    # В. Проверяем: товар уже есть? (Ищем по ссылке) 
                    # Используем select().where(...) 
                    result = await session.execute(select(Product).where(Product.link == link)) 
                    existing_product = result.scalars().first() 
 
                    if existing_product: 
                         # ОБНОВЛЯЕМ существующий 
                         existing_product.name = row['name'] 
                         existing_product.keywords = row['keywords'] 
                         existing_product.ad_text = row['ad_text'] 
                         existing_product.target_assistants = str(row['target_assistants']) # Приводим к строке 
                         # existing_product.is_active = True # Можно раскомментировать, если надо "воскрешать" товары 
                         count_updated += 1 
                    else: 
                         # СОЗДАЕМ новый 
                         new_product = Product( 
                             name=row['name'], 
                             keywords=row['keywords'], 
                             ad_text=row['ad_text'], 
                             link=link, 
                             target_assistants=str(row['target_assistants']), 
                             is_active=True 
                         ) 
                         session.add(new_product) 
                         count_added += 1 
                 
                await session.commit() 
             
            # Сообщение об успехе (можно вывести в лог или через flash-message, если настроено) 
            print(f"Sync complete: {count_added} added, {count_updated} updated.") 
 
        except Exception as e: 
            # В идеале тут нужно вывести ошибку юзеру, но в MVP просто принтуем 
            print(f"Google Sync Error: {e}") 
 
        # Г. Возвращаемся обратно на список товаров 
        return RedirectResponse(url=request.url_for("admin:list", identity="product"), status_code=303) 

admin.add_view(DashboardAdmin) 
admin.add_view(UserAdmin)
admin.add_view(AssistantAdmin)
admin.add_view(ProductAdmin)
admin.add_view(MessageAdmin)
admin.add_view(UserClickAdmin)

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
        {"role": msg.role, "content": msg.content, "id": msg.id, "image_path": msg.image_path}
        for msg in history
    ]

@app.get("/api/click")
async def track_click(product_id: int, user_id: int = None, db: AsyncSession = Depends(get_db)):
    """
    Эндпоинт для трекинга кликов.
    1. Ищет товар по ID.
    2. Увеличивает счетчик кликов.
    3. Если передан user_id, сохраняет клик пользователя.
    4. Редиректит пользователя на целевую ссылку.
    """
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Общий счетчик
    product.clicks += 1
    
    # Персональный клик
    if user_id:
        click = UserClick(user_id=user_id, product_id=product_id)
        db.add(click)
        
    await db.commit()
    
    return RedirectResponse(url=product.link)

@app.post("/api/chat")
async def chat(
    request: Request, 
    assistant_slug: str = Form(...),
    text: str = Form(...),
    file: UploadFile = File(None),
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

    # 2.1 Обработка файла
    saved_image_path = None
    if file:
        os.makedirs("static/uploads", exist_ok=True)
        filename = f"{uuid.uuid4()}.jpg" # Всегда сохраняем в JPG (экономит место)
        saved_image_path = f"static/uploads/{filename}"
        
        # --- ОПТИМИЗАЦИЯ ---
        # 1. Читаем файл в память
        content = await file.read()
        image = Image.open(io.BytesIO(content))
        
        # 2. Конвертируем в RGB (если был PNG с прозрачностью, иначе упадет)
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
            
        # 3. Ресайз (если больше 1024px по широкой стороне)
        max_size = (1024, 1024)
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # 4. Сохраняем с качеством 70% (визуально не видно, вес падает в 5-10 раз)
        image.save(saved_image_path, "JPEG", quality=70, optimize=True)

    # 3. Загрузка истории
    history_q = await db.execute(
        select(Message)
        .where(Message.user_id == user_id, Message.assistant_slug == assistant_slug)
        .order_by(Message.id.desc())
        .limit(10)
    )
    history = history_q.scalars().all()[::-1]

    # 4. Ответ ИИ
    ai_answer = await get_ai_response(text, assistant_slug, history, db, user_id=user_id, image_path=saved_image_path)

    # 5. Сохранение
    msg_user = Message(
        user_id=user_id, 
        assistant_slug=assistant_slug, 
        role="user", 
        content=text,
        image_path=saved_image_path
    )
    msg_ai = Message(user_id=user_id, assistant_slug=assistant_slug, role="assistant", content=ai_answer)
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