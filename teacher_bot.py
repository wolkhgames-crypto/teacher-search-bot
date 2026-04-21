"""
Telegram бот для поиска преподавателей
Запуск: python bot.py
"""

import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv
import os

from search_teacher_cli import search_teacher_public

load_dotenv()

dp = Dispatcher()
bot = Bot(token=os.getenv("BOT_TOKEN"))

ADMIN_PASSWORD = "Wa9n8d77!!"

# Храним пользователей, прошедших авторизацию
authorized_users = set()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id

    if user_id in authorized_users:
        await message.answer("Ты уже авторизован! Просто отправь фамилию преподавателя.")
    else:
        await message.answer("Введи пароль для доступа:")

@dp.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    # Проверяем пароль
    if user_id not in authorized_users:
        if text == ADMIN_PASSWORD:
            authorized_users.add(user_id)
            await message.answer("✅ Пароль верный! Теперь отправь фамилию преподавателя.")
        else:
            await message.answer("❌ Неверный пароль. Введи правильный пароль.")
        return

    # Ищем преподавателя
    await message.answer("⏳ Ищу преподавателя...")

    try:
        result = await search_teacher_public(text)

        if "не найден" in result.lower():
            await message.answer(result)
        else:
            await message.answer(result)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

async def main():
    logging.basicConfig(level=logging.INFO)

    bot = Bot(token=os.getenv("BOT_TOKEN"), session=AiohttpSession())

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())