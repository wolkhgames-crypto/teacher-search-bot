"""
Telegram бот для СРМК - Расписание, Оценки, Поиск преподавателей
Запуск: python bot.py
"""

import asyncio
import logging
import random
import string
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ForceReply
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import ClientSession, TCPConnector, ClientTimeout
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import os

from db import init_db, get_user, save_user_auth, save_moodle_data, update_cookies, close_db

load_dotenv()

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_PASSWORD = "Km9pL2xQ7wAb"  # Новый 12-символьный пароль

# Разрешённые Telegram ID (добавляй свои через запятую)
ALLOWED_USERS = ["2035205294"]  # Твой Telegram ID

# Moodle настройки
BASE_URL = "https://rmk.stavedu.ru:8010/moodle"
LOGIN_URL = f"{BASE_URL}/login/index.php"
DIARY_URL = f"{BASE_URL}/eioswork/diaries/studentsdiary.php"
TIMETABLE_URL = f"{BASE_URL}/eioswork/timetable/watchstudent.php"

# Группа (жёстко задано)
GROUP_ID = "238"  # ID группы П-31

dp = Dispatcher(storage=MemoryStorage())

# ==================== СОСТОЯНИЯ ====================
class AuthStates(StatesGroup):
    waiting_password = State()
    waiting_moodle_login = State()
    waiting_moodle_password = State()
    waiting_teacher_name = State()

# ==================== КЛАВИАТУРЫ ====================
def main_keyboard():
    """Кнопки под полем ввода (ReplyKeyboard)"""
    buttons = [
        [KeyboardButton(text="📊 Оценки"), KeyboardButton(text="📋 Расписание")],
        [KeyboardButton(text="👁️ Поиск преподавателя")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def cancel_keyboard():
    """Кнопка отмены"""
    buttons = [[KeyboardButton(text="❌ Отмена")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ==================== ХРАНИЛИЩЕ ====================
# Временное хранение данных пользователя
user_data = {}

# ==================== MOODLE ФУНКЦИИ ====================
async def get_login_token(session: ClientSession) -> str:
    """Получает logintoken с формы Moodle"""
    try:
        async with session.get(LOGIN_URL, allow_redirects=True, max_redirects=5) as resp:
            html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")
        token = soup.find("input", {"name": "logintoken"})
        return token["value"] if token else ""
    except:
        return ""

async def moodle_login(username: str, password: str) -> dict | None:
    """Вход в Moodle, возвращает cookies или None"""
    timeout = ClientTimeout(total=30, connect=10)
    connector = TCPConnector(ssl=False, force_close=True)

    try:
        async with ClientSession(connector=connector, timeout=timeout) as session:
            token = await get_login_token(session)

            data = {
                "anchor": "",
                "logintoken": token,
                "username": username,
                "password": password,
            }

            async with session.post(LOGIN_URL, data=data, allow_redirects=True) as resp:
                final_url = str(resp.url)

                if "login" in final_url:
                    return None

                cookies = {}
                for name, cookie in session.cookie_jar.filter_cookies(LOGIN_URL).items():
                    cookies[name] = cookie.value
                return cookies if cookies else None
    except:
        return None

async def fetch_grades(cookies: dict) -> str:
    """Получает оценки за текущий месяц"""
    now = datetime.now()
    url = f"{DIARY_URL}?year={now.year}&month={now.month}"

    timeout = ClientTimeout(total=30, connect=10)
    connector = TCPConnector(ssl=False, force_close=True)

    try:
        async with ClientSession(connector=connector, cookies=cookies, timeout=timeout) as session:
            async with session.get(url) as resp:
                final_url = str(resp.url)
                html = await resp.text()

                if "login" in final_url or "403" in html:
                    return None

        return parse_grades(html, now.year, now.month)
    except:
        return "❌ Сервер недоступен. Попробуй позже."

def parse_grades(html: str, year: int, month: int) -> str:
    """Парсит оценки из HTML"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tblgrades") or soup.find("table")

    if not table:
        return "❌ Таблица оценок не найдена"

    months_ru = {
        1: "январь", 2: "февраль", 3: "март", 4: "апрель",
        5: "май", 6: "июнь", 7: "июль", 8: "август",
        9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"
    }

    result = [f"📊 *Оценки за {months_ru[month]} {year}*\n"]
    total_grades = 0
    grade_sum = 0

    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if not cells:
            continue

        subject_div = cells[0].find("div", class_="table-button")
        subject = subject_div.get_text(strip=True) if subject_div else cells[0].get_text(strip=True)

        if not subject:
            continue

        grades = []
        attestation = None
        first_item_processed = False

        for cell in cells[1:]:
            b = cell.find("b")
            if b:
                text = b.get_text(strip=True)
                if text:
                    for char in text:
                        if not first_item_processed:
                            if char.isdigit():
                                attestation = char
                                grade_sum += int(char)
                                total_grades += 1
                            elif char.lower() == 'н':
                                attestation = 'н/а'
                            first_item_processed = True
                        else:
                            if char.isdigit():
                                grades.append(char)
                                grade_sum += int(char)
                                total_grades += 1
                            elif char.lower() == 'н':
                                grades.append('н')

        if attestation or grades:
            subject_line = f"📚 *{subject}*\n"
            if attestation:
                subject_line += f"   Аттестация: `{attestation}`\n"
            if grades:
                subject_line += f"   Оценки: `{'|'.join(grades)}`\n"
            result.append(subject_line)

    if total_grades > 0:
        avg = grade_sum / total_grades
        result.append("━━━━━━━━━━━━━━━━")
        result.append(f"📈 *Средний балл:* `{avg:.2f}`")
        result.append(f"📝 *Всего оценок:* `{total_grades}`")

    return "\n".join(result) if len(result) > 1 else "📭 Оценок за этот месяц нет"

async def fetch_timetable_public() -> str:
    """Получает расписание для группы П-31 (без авторизации)"""
    now = datetime.now()
    url = f"{TIMETABLE_URL}?year={now.year}&month={now.month}&group={GROUP_ID}"

    timeout = ClientTimeout(total=30, connect=10)
    connector = TCPConnector(ssl=False, force_close=True)

    try:
        async with ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(url) as resp:
                html = await resp.text()
        return parse_timetable(html)
    except:
        return "❌ Сервер недоступен. Попробуй позже."

async def fetch_timetable(cookies: dict) -> str:
    """Получает расписание для группы П-31 (с авторизацией)"""
    now = datetime.now()
    url = f"{TIMETABLE_URL}?year={now.year}&month={now.month}&group={GROUP_ID}"

    timeout = ClientTimeout(total=30, connect=10)
    connector = TCPConnector(ssl=False, force_close=True)

    try:
        async with ClientSession(connector=connector, cookies=cookies, timeout=timeout) as session:
            async with session.get(url) as resp:
                final_url = str(resp.url)
                html = await resp.text()

                if "login" in final_url or "403" in html:
                    return None

        return parse_timetable(html)
    except:
        return "❌ Сервер недоступен. Попробуй позже."

def parse_timetable(html: str) -> str:
    """Парсит расписание из HTML"""
    soup = BeautifulSoup(html, "html.parser")
    day_tables = soup.find_all("table", class_="daytable")

    if not day_tables:
        return "❌ Расписание не найдено"

    # Извлекаем заголовок группы из HTML или используем П-31 по умолчанию
    header_tag = soup.find(["h1", "h2", "h3", "h4"])
    group_title = header_tag.get_text(strip=True) if header_tag else "П-31"

    result = [f"📅 *Расписание занятий ({group_title})*\n"]

    times = {
        "1": "8:00 - 9:30",
        "2": "9:40 - 11:10",
        "3": "11:40 - 13:10",
        "4": "13:20 - 14:50",
        "5": "15:00 - 16:30",
        "6": "16:50 - 18:20",
        "7": "18:30 - 20:00"
    }

    for day_table in day_tables:
        day_header = day_table.find("td", class_="thead")
        if day_header:
            day_text = day_header.get_text(strip=True)
            result.append(f"\n*{day_text}:*")

        rows = day_table.find_all("tr")[1:]

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            pair_num = cells[0].get_text(strip=True)
            rowtable = cells[1].find("table", class_="rowtable")
            if not rowtable:
                continue

            pair_rows = rowtable.find_all("tr")
            if not pair_rows:
                continue

            first_row = pair_rows[0]
            pair_cells = first_row.find_all("td")
            if not pair_cells:
                continue

            pair_info = pair_cells[0].get_text(strip=True)

            if "—" in pair_info and pair_info.count("—") >= 2:
                continue

            parts = pair_info.split("|")
            if len(parts) >= 2:
                subject = parts[0].strip()
                teacher = parts[1].strip()
                cabinet = pair_cells[1].get_text(strip=True) if len(pair_cells) > 1 else "—"

                result.append(f"{pair_num}) {subject}")
                result.append(f"├ Время: `{times.get(pair_num, '—')}`")
                result.append(f"├ Преподаватель: {teacher}")
                result.append(f"└ Кабинет: {cabinet}")

                # Проверяем подгруппы
                if len(pair_rows) > 1:
                    second_row = pair_rows[1]
                    second_cells = second_row.find_all("td")
                    if second_cells:
                        second_info = second_cells[0].get_text(strip=True)
                        if "—" not in second_info or second_info.count("—") < 2:
                            second_parts = second_info.split("|")
                            if len(second_parts) >= 2:
                                second_teacher = second_parts[1].strip()
                                second_cabinet = second_cells[1].get_text(strip=True) if len(second_cells) > 1 else "—"
                                result.append(f"└ Подгруппа 2: {second_teacher} | {second_cabinet}")

    return "\n".join(result) if len(result) > 1 else "📭 Расписание не найдено"

# ==================== ПОИСК ПРЕПОДАВАТЕЛЕЙ ====================
async def search_teacher_public(teacher_name: str) -> str:
    """Ищет преподавателя во всех группах (без авторизации)"""
    from groups import GROUPS

    schedule_by_day = {}
    times = {
        "1": "8:00-9:30", "2": "9:40-11:10", "3": "11:40-13:10",
        "4": "13:20-14:50", "5": "15:00-16:30", "6": "16:50-18:20", "7": "18:30-20:00"
    }

    all_groups = list(GROUPS.items())
    group_names = {gid: gname for gname, gid in all_groups}
    all_results = {}

    connector = TCPConnector(ssl=False, force_close=True, limit=200, limit_per_host=50)
    timeout = ClientTimeout(total=15, connect=5)

    async with ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [fetch_timetable_html_public(session, gid) for gname, gid in all_groups]
        results = await asyncio.gather(*tasks)

        for group_id, html in results:
            all_results[group_id] = html

        failed_groups = [gid for gid, html in results if not html]

        retry_count = 0
        max_retries = 5

        while failed_groups and retry_count < max_retries:
            retry_count += 1
            tasks = [fetch_timetable_html_public(session, gid) for gid in failed_groups]
            results = await asyncio.gather(*tasks)

            still_failed = []
            for group_id, html in results:
                all_results[group_id] = html
                if not html:
                    still_failed.append(group_id)

            failed_groups = still_failed
            if failed_groups:
                await asyncio.sleep(1)

    for group_id, html in all_results.items():
        group_name = group_names.get(group_id, group_id)
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
            day_tables = soup.find_all("table", class_="daytable")

            if not day_tables:
                continue

            for day_table in day_tables:
                day_header = day_table.find("td", class_="thead")
                if not day_header:
                    continue

                day_text = day_header.get_text(strip=True)
                rows = day_table.find_all("tr")[1:]

                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) < 2:
                        continue

                    pair_num = cells[0].get_text(strip=True)
                    rowtable = cells[1].find("table", class_="rowtable")
                    if not rowtable:
                        continue

                    pair_rows = rowtable.find_all("tr")
                    if not pair_rows:
                        continue

                    first_row = pair_rows[0]
                    pair_cells = first_row.find_all("td")
                    if not pair_cells:
                        continue

                    pair_info = pair_cells[0].get_text(strip=True)

                    if "—" in pair_info and pair_info.count("—") >= 2:
                        continue

                    parts = pair_info.split("|")
                    if len(parts) >= 2:
                        subject = parts[0].strip()
                        teacher = parts[1].strip()
                        cabinet = pair_cells[1].get_text(strip=True) if len(pair_cells) > 1 else "—"

                        if teacher_name.lower() in teacher.lower():
                            if cabinet == '—':
                                continue

                            if day_text not in schedule_by_day:
                                schedule_by_day[day_text] = []

                            schedule_by_day[day_text].append({
                                'group': group_name,
                                'pair_num': int(pair_num),
                                'subject': subject,
                                'cabinet': cabinet
                            })

                        if len(pair_rows) > 1:
                            second_row = pair_rows[1]
                            second_cells = second_row.find_all("td")
                            if second_cells:
                                second_info = second_cells[0].get_text(strip=True)
                                if "—" not in second_info or second_info.count("—") < 2:
                                    second_parts = second_info.split("|")
                                    if len(second_parts) >= 2:
                                        second_teacher = second_parts[1].strip()
                                        second_cabinet = second_cells[1].get_text(strip=True) if len(second_cells) > 1 else "—"

                                        if teacher_name.lower() in second_teacher.lower():
                                            if second_cabinet == '—':
                                                continue

                                            if day_text not in schedule_by_day:
                                                schedule_by_day[day_text] = []

                                            schedule_by_day[day_text].append({
                                                'group': group_name,
                                                'pair_num': int(pair_num),
                                                'subject': subject,
                                                'cabinet': second_cabinet
                                            })
        except:
            continue

    if not schedule_by_day:
        return f"❌ Преподаватель '{teacher_name}' не найден в расписании"

    result = [f"🔍 Поиск преподавателя: {teacher_name}\n"]

    def get_day_sort_key(day_str):
        try:
            day_num = int(day_str.split()[0])
            now = datetime.now()
            if day_num < now.day:
                return day_num + 100
            return day_num
        except:
            return 999

    sorted_days = sorted(schedule_by_day.keys(), key=get_day_sort_key)

    for day in sorted_days:
        result.append(f"\n{day}:")
        pairs = sorted(schedule_by_day[day], key=lambda x: x['pair_num'])
        for pair in pairs:
            time_str = times.get(str(pair['pair_num']), '—')
            line = f"{pair['group']} | {pair['pair_num']} пара | {time_str} | {pair['subject']} | {pair['cabinet']}"
            result.append(line)

    unique_groups = set()
    for day_pairs in schedule_by_day.values():
        for pair in day_pairs:
            unique_groups.add(pair['group'])

    result.append("\n" + "=" * 50)
    result.append(f"Найдено групп: {len(unique_groups)}")

    return "\n".join(result)

async def fetch_timetable_html_public(session: ClientSession, group_id: str) -> tuple:
    """Получает HTML расписания без авторизации"""
    now = datetime.now()
    url = f"{TIMETABLE_URL}?year={now.year}&month={now.month}&group={group_id}"

    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                return (group_id, await resp.text())
            return (group_id, None)
    except:
        return (group_id, None)

# ==================== ОБРАБОТЧИКИ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)

    # Загружаем данные из БД
    db_user = await get_user(user_id)

    if db_user and db_user.get("authorized"):
        # Восстанавливаем данные в память
        user_data[user_id] = {
            "authorized": True,
            "moodle_login": db_user.get("moodle_login"),
            "moodle_password": db_user.get("moodle_password"),
            "moodle_cookies": db_user.get("moodle_cookies"),
        }
        await message.answer(
            "✅ Ты уже авторизован!\n\n"
            "Выбери действие:",
            reply_markup=main_keyboard()
        )
    elif user_id in user_data and user_data[user_id].get("authorized"):
        await message.answer(
            "✅ Ты уже авторизован!\n\n"
            "Выбери действие:",
            reply_markup=main_keyboard()
        )
    else:
        await message.answer(
            "🔐 <b>Введи пароль для доступа</b>\n\n"
            "<i>Отправь пароль следующим сообщением</i>",
            parse_mode="HTML"
        )
        await message.answer("Введи пароль:", reply_markup=cancel_keyboard())
        await state.set_state(AuthStates.waiting_password)

@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_keyboard())

@dp.message()
async def handle_message(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    text = message.text.strip()
    current_state = await state.get_state()

    # Проверка белого списка пользователей
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        await message.answer("❌ Доступ запрещён. Ваш Telegram ID не в белом списке.")
        return

    # Проверка пароля админа
    if current_state == AuthStates.waiting_password.state:
        if text == ADMIN_PASSWORD:
            user_data[user_id] = {"authorized": True}
            await save_user_auth(user_id)
            await state.clear()
            await message.answer(
                "✅ <b>Пароль верный! Доступ разрешён.</b>\n\n"
                "Теперь ты можешь использовать все функции бота.",
                parse_mode="HTML",
                reply_markup=main_keyboard()
            )
        else:
            await message.answer("❌ Неверный пароль. Попробуй ещё раз:", reply_markup=cancel_keyboard())
        return

    # Загружаем из БД если нет в памяти
    if user_id not in user_data:
        db_user = await get_user(user_id)
        if db_user and db_user.get("authorized"):
            user_data[user_id] = {
                "authorized": True,
                "moodle_login": db_user.get("moodle_login"),
                "moodle_password": db_user.get("moodle_password"),
                "moodle_cookies": db_user.get("moodle_cookies"),
            }

    # Проверка авторизации
    if not (user_id in user_data and user_data[user_id].get("authorized")):
        await message.answer("Сначала введи пароль! Отправь /start")
        return

    # Обработка кнопок главного меню
    if text == "📊 Оценки":
        # Проверяем, есть ли cookies от Moodle
        ud = user_data.get(user_id, {})
        if ud.get("moodle_cookies"):
            await message.answer("⏳ Загружаю оценки...")
            result = await fetch_grades(ud["moodle_cookies"])
            if result:
                await message.answer(result, parse_mode="Markdown")
            else:
                # Cookies истекли — пробуем ре-логин
                ml_user = ud.get("moodle_login")
                ml_pass = ud.get("moodle_password")
                if ml_user and ml_pass:
                    await message.answer("🔄 Сессия истекла, переподключаюсь...")
                    new_cookies = await moodle_login(ml_user, ml_pass)
                    if new_cookies:
                        user_data[user_id]["moodle_cookies"] = new_cookies
                        await update_cookies(user_id, new_cookies)
                        result = await fetch_grades(new_cookies)
                        if result:
                            await message.answer(result, parse_mode="Markdown")
                        else:
                            await message.answer("❌ Не удалось загрузить оценки.")
                    else:
                        await message.answer("❌ Не удалось войти. Введи логин от Moodle:")
                        await state.set_state(AuthStates.waiting_moodle_login)
                else:
                    await message.answer("❌ Сессия истекла. Введи логин от Moodle:")
                    await state.set_state(AuthStates.waiting_moodle_login)
        else:
            await message.answer(
                "📚 <b>Вход в электронный дневник</b>\n\n"
                "Введи свой логин от Moodle (СРМК):",
                parse_mode="HTML",
                reply_markup=cancel_keyboard()
            )
            await state.set_state(AuthStates.waiting_moodle_login)
        return

    if text == "📋 Расписание":
        # Расписание доступно без авторизации (группа П-21)
        await message.answer("⏳ Загружаю расписание...")
        result = await fetch_timetable_public()
        if result:
            await message.answer(result, parse_mode="Markdown")
        else:
            await message.answer("❌ Не удалось загрузить расписание. Попробуй позже.")
        return

    if text == "👁️ Поиск преподавателя":
        await message.answer(
            "👁️ <b>Поиск преподавателя</b>\n\n"
            "Введи фамилию преподавателя:",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.set_state(AuthStates.waiting_teacher_name)
        return

    # Обработка ввода логина Moodle
    if current_state == AuthStates.waiting_moodle_login.state:
        user_data.setdefault(user_id, {})["moodle_login"] = text
        await message.answer("Теперь введи пароль от Moodle:", reply_markup=cancel_keyboard())
        await state.set_state(AuthStates.waiting_moodle_password)
        return

    # Обработка ввода пароля Moodle
    if current_state == AuthStates.waiting_moodle_password.state:
        await message.answer("⏳ Авторизация в Moodle...")

        ml_user = user_data.get(user_id, {}).get("moodle_login")
        ml_pass = text

        cookies = await moodle_login(ml_user, ml_pass)

        if cookies:
            user_data.setdefault(user_id, {})["moodle_cookies"] = cookies
            await save_moodle_data(user_id, ml_user, ml_pass, cookies)
            await state.clear()
            await message.answer(
                "✅ Успешный вход в Moodle!\n\n"
                "Теперь можно получить оценки или расписание.",
                reply_markup=main_keyboard()
            )

            # Автоматически запрашиваем то, что пользователь хотел изначально
            await message.answer("⏳ Загружаю данные...")
            grades_result = await fetch_grades(cookies)
            if grades_result:
                await message.answer(grades_result, parse_mode="Markdown")
        else:
            await message.answer(
                "❌ Неверный логин или пароль от Moodle.\n"
                "Попробуй ещё раз или отправь /start",
                reply_markup=main_keyboard()
            )
            await state.clear()
        return

    # Обработка поиска преподавателя
    if current_state == AuthStates.waiting_teacher_name.state:
        await message.answer("⏳ Ищу преподавателя по всем группам...\nЭто займёт 10-30 секунд.")

        result = await search_teacher_public(text)
        await state.clear()
        await message.answer(result, reply_markup=main_keyboard())
        return

    # Неизвестная команда
    await message.answer("Выбери действие из меню:", reply_markup=main_keyboard())

# ==================== ЗАПУСК ====================
async def main():
    logging.basicConfig(level=logging.INFO)

    if not BOT_TOKEN:
        print("❌ Ошибка: BOT_TOKEN не найден в .env файле!")
        return

    # Подключаем БД
    await init_db()
    print("✅ БД подключена!")

    bot = Bot(token=BOT_TOKEN, session=AiohttpSession())

    try:
        print("✅ Бот запущен!")
        await dp.start_polling(bot)
    finally:
        await close_db()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
