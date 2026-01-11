from sqlalchemy import select, func, text, case
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
from app.models import User, Message, Assistant

class DashboardMetrics:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_dau(self) -> int:
        """Unique users who sent a message in the last 24h"""
        one_day_ago = datetime.utcnow() - timedelta(days=1)
        result = await self.session.execute(
            select(func.count(func.distinct(Message.user_id)))
            .where(Message.created_at >= one_day_ago)
        )
        return result.scalar() or 0

    async def get_mau(self) -> int:
        """Unique users who sent a message in the last 30 days"""
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        result = await self.session.execute(
            select(func.count(func.distinct(Message.user_id)))
            .where(Message.created_at >= thirty_days_ago)
        )
        return result.scalar() or 0

    async def get_retention(self) -> dict:
        """
        Retention: % of users who returned after 1, 7, 30 days.
        Formula: (Users active after N days / Users registered at least N days ago) * 100
        """
        retention_days = [1, 7, 30]
        results = {}

        for days in retention_days:
            # 1. Denominator: Users registered at least 'days' ago
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            # SQLite uses string comparison for dates in ISO format, which works fine
            total_users_res = await self.session.execute(
                select(func.count(User.tg_id))
                .where(User.created_at <= cutoff_date)
            )
            total_users = total_users_res.scalar() or 0

            if total_users == 0:
                results[f"{days}_day"] = 0.0
                continue

            # 2. Numerator: Users from that group who sent a message AFTER (created_at + days)
            # In SQLite: datetime(created_at, '+N days')
            
            # We need to join Messages and Users
            # Find users where EXISTS a message with created_at > user.created_at + days
            
            # Using a subquery or join
            # SELECT count(DISTINCT user.tg_id)
            # FROM users
            # JOIN messages ON messages.user_id = users.tg_id
            # WHERE users.created_at <= cutoff_date
            # AND messages.created_at >= datetime(users.created_at, f'+{days} days')
            
            stmt = (
                select(func.count(func.distinct(User.tg_id)))
                .join(Message, Message.user_id == User.tg_id)
                .where(User.created_at <= cutoff_date)
                .where(text(f"messages.created_at >= datetime(users.created_at, '+{days} days')"))
            )
            
            retained_users_res = await self.session.execute(stmt)
            retained_users = retained_users_res.scalar() or 0
            
            results[f"{days}_day"] = round((retained_users / total_users) * 100, 2)

        return results

    async def get_assistant_popularity(self) -> list:
        """Distribution of messages by assistant_slug"""
        result = await self.session.execute(
            select(Message.assistant_slug, func.count(Message.id))
            .group_by(Message.assistant_slug)
            .order_by(func.count(Message.id).desc())
        )
        return [{"name": row[0], "count": row[1]} for row in result.all()]

    async def get_message_volume(self) -> list:
        """Total messages per day (last 7 days)"""
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        # SQLite specific date function
        date_func = func.date(Message.created_at)
        
        result = await self.session.execute(
            select(date_func, func.count(Message.id))
            .where(Message.created_at >= seven_days_ago)
            .group_by(date_func)
            .order_by(date_func)
        )
        return [{"date": str(row[0]), "count": row[1]} for row in result.all()]

    async def get_conversion_rate(self) -> float:
        """
        % of created users who have message_count > 0.
        Users with messages / Total users
        """
        total_users_res = await self.session.execute(select(func.count(User.tg_id)))
        total_users = total_users_res.scalar() or 0
        
        if total_users == 0:
            return 0.0

        active_users_res = await self.session.execute(
            select(func.count(func.distinct(Message.user_id)))
        )
        active_users = active_users_res.scalar() or 0

        return round((active_users / total_users) * 100, 2)
