from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    OPENROUTER_API_KEY: str
    GROQ_API_KEY: str
    TELEGRAM_BOT_TOKEN: str
    
    # Google Sheets
    GOOGLE_SHEET_URL: str = "https://docs.google.com/spreadsheets/d/1d4sBMQWBIPMn02EZPOrQnzo6JlfzEDDmP0lxCYO90G4"
    GOOGLE_CREDS_FILE: str = "google_creds.json"
    GOOGLE_SHEET_ASSISTANTS_TAB: str = "assistants"
    GOOGLE_SHEET_PRODUCTS_TAB: str = "products"
    GOOGLE_SCOPES: list[str] = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # API & Redirects
    REDIRECT_BASE_URL: str = "http://localhost:8000"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    CHAT_HISTORY_LIMIT: int = 10

    # Paths
    UPLOAD_DIR: str = "static/uploads"
    
    # Admins
    ADMIN_IDS: str = "12346,254913192"
    ADMIN_PASSWORD: str = "admin123"
    SECRET_KEY: str = "super-secret-key-change-me-in-production"

    class Config:
        env_file = ".env"

settings = Settings()