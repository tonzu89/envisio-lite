from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, Text, Boolean, BigInteger, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.database import Base

def get_current_time():
    return datetime.now(timezone(timedelta(hours=3))).replace(microsecond=0)

class User(Base):
    __tablename__ = "users"
    tg_id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String, nullable=True)
    created_at = Column(DateTime, default=get_current_time)  
    messages = relationship("Message", back_populates="user", lazy="select")

class Assistant(Base):
    __tablename__ = "assistants"
    slug = Column(String, primary_key=True) # id: "medic", "pushkin"
    name = Column(String)
    description = Column(String)
    icon_emoji = Column(String)
    openrouter_preset = Column(String) # "@preset/..."
    welcome_message = Column(Text)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    keywords = Column(String) # "спина, боль, суставы"
    target_assistants = Column(String, nullable=True) # "medic, fitness"
    ad_text = Column(Text)
    link = Column(String)
    is_active = Column(Boolean, default=True)
    
    # CTR metrics
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.tg_id"))
    assistant_slug = Column(String, ForeignKey("assistants.slug"))
    role = Column(String) # user / assistant
    content = Column(Text)
    created_at = Column(DateTime, default=get_current_time)

    user = relationship("User", back_populates="messages")