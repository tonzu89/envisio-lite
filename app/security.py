import hmac
import hashlib
import json
from urllib.parse import parse_qsl
from app.config import settings
from fastapi import HTTPException, Header

def validate_telegram_data(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="No init data found")
    
    try:
        parsed_data = dict(parse_qsl(init_data))
    except ValueError:
         raise HTTPException(status_code=401, detail="Invalid init data")

    if "hash" not in parsed_data:
        raise HTTPException(status_code=401, detail="Hash missing")

    hash_check = parsed_data.pop("hash")
    
    # Сортируем ключи по алфавиту (требование Telegram)
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
    
    # Создаем секретный ключ на основе токена бота
    secret_key = hmac.new(b"WebAppData", settings.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    
    # Считаем хеш
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if calculated_hash != hash_check:
        raise HTTPException(status_code=403, detail="Data integrity check failed")

    return json.loads(parsed_data["user"])