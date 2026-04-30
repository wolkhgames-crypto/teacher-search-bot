"""
Модуль для работы с PostgreSQL (Neon)
Хранит: авторизацию, moodle логин/пароль, cookies
"""

import asyncpg
import json
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_4Koc0bUIMqSn@ep-raspy-mode-allolz2g-pooler.c-3.eu-central-1.aws.neon.tech/neondb?sslmode=require"
)

pool: asyncpg.Pool = None


async def init_db():
    """Создаёт пул соединений и таблицу users"""
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                authorized BOOLEAN DEFAULT FALSE,
                moodle_login TEXT,
                moodle_password TEXT,
                moodle_cookies TEXT
            )
        """)


async def get_user(user_id: str) -> dict | None:
    """Получает данные пользователя из БД"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1", user_id
        )
        if not row:
            return None
        return {
            "authorized": row["authorized"],
            "moodle_login": row["moodle_login"],
            "moodle_password": row["moodle_password"],
            "moodle_cookies": json.loads(row["moodle_cookies"]) if row["moodle_cookies"] else None,
        }


async def save_user_auth(user_id: str):
    """Сохраняет авторизацию пользователя"""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, authorized)
            VALUES ($1, TRUE)
            ON CONFLICT (user_id) DO UPDATE SET authorized = TRUE
        """, user_id)


async def save_moodle_data(user_id: str, login: str, password: str, cookies: dict):
    """Сохраняет логин, пароль и cookies от Moodle"""
    cookies_json = json.dumps(cookies)
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, authorized, moodle_login, moodle_password, moodle_cookies)
            VALUES ($1, TRUE, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET
                moodle_login = $2,
                moodle_password = $3,
                moodle_cookies = $4
        """, user_id, login, password, cookies_json)


async def update_cookies(user_id: str, cookies: dict):
    """Обновляет только cookies"""
    cookies_json = json.dumps(cookies)
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE users SET moodle_cookies = $2 WHERE user_id = $1
        """, user_id, cookies_json)


async def close_db():
    """Закрывает пул соединений"""
    global pool
    if pool:
        await pool.close()
