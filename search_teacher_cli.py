"""
Скрипт для поиска преподавателей через терминал (без авторизации)
Использование: python search_teacher_cli.py [Фамилия]
"""

import asyncio
import sys
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime
from groups import GROUPS

# Настройка кодировки для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    sys.stdin = codecs.getreader('utf-8')(sys.stdin.buffer, 'strict')

TIMETABLE_URL = "https://rmk.stavedu.ru:8010/moodle/eioswork/timetable/watchstudent.php"

# Отключаем буферизацию вывода
import functools
print = functools.partial(print, flush=True)

async def fetch_timetable_html_public(session: aiohttp.ClientSession, group_id: str, year: int = None, month: int = None) -> tuple[str, str | None]:
    """Получает HTML расписания для группы без авторизации"""
    if year is None:
        year = datetime.now().year
    if month is None:
        month = datetime.now().month

    url = f"{TIMETABLE_URL}?year={year}&month={month}&group={group_id}"

    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                html = await resp.text()
                return (group_id, html)
            else:
                return (group_id, None)
    except:
        return (group_id, None)

async def search_teacher_public(teacher_name: str) -> str:
    """Ищет преподавателя во всех группах - параллельно с retry для ошибок"""

    schedule_by_day = {}

    times = {
        "1": "8:00-9:30",
        "2": "9:40-11:10",
        "3": "11:40-13:10",
        "4": "13:20-14:50",
        "5": "15:00-16:30",
        "6": "16:50-18:20",
        "7": "18:30-20:00"
    }

    all_groups = list(GROUPS.items())
    total_groups = len(all_groups)
    group_names = {gid: gname for gname, gid in all_groups}

    # Словарь для хранения результатов
    all_results = {}

    # Параллельные запросы
    connector = aiohttp.TCPConnector(ssl=False, force_close=True, limit=200, limit_per_host=50)
    timeout = aiohttp.ClientTimeout(total=15, connect=5)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:

        # Первый проход - все запросы параллельно
        tasks = [fetch_timetable_html_public(session, gid) for gname, gid in all_groups]
        results = await asyncio.gather(*tasks)

        # Сохраняем результаты первого прохода
        for group_id, html in results:
            all_results[group_id] = html

        # Собираем кто не прошел
        failed_groups = [gid for gid, html in results if not html]

        # Повторяем неудачные запросы
        retry_count = 0
        max_retries = 5

        while failed_groups and retry_count < max_retries:
            retry_count += 1
            print(f"\nПовторная проверка {len(failed_groups)} групп (попытка {retry_count}/{max_retries})...")

            tasks = [fetch_timetable_html_public(session, gid) for gid in failed_groups]
            results = await asyncio.gather(*tasks)

            # Сохраняем результаты retry
            still_failed = []
            for group_id, html in results:
                all_results[group_id] = html
                if not html:
                    still_failed.append(group_id)

            print(f"  Успешно: {len(failed_groups) - len(still_failed)}, ещё не прошли: {len(still_failed)}")
            failed_groups = still_failed

            if failed_groups:
                await asyncio.sleep(1)

    # Обрабатываем все результаты
    successful = total_groups - len(failed_groups)

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

    print(f"\nСтатистика:")
    print(f"  Успешно: {successful}")
    print(f"  Ошибок: {len(failed_groups)}")
    print(f"  Всего: {total_groups}\n")

    if not schedule_by_day:
        return f"Преподаватель '{teacher_name}' не найден в расписании"

    result = []
    result.append(f"Поиск преподавателя: {teacher_name}\n")

    def get_day_sort_key(day_str):
        try:
            day_num = int(day_str.split()[0])
            now = datetime.now()
            current_day = now.day
            if day_num < current_day:
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

async def main():
    print("=" * 50)
    print("ПОИСК ПРЕПОДАВАТЕЛЯ В РАСПИСАНИИ")
    print("=" * 50)
    print()

    if len(sys.argv) > 1:
        teacher_name = " ".join(sys.argv[1:]).strip()
    else:
        teacher_name = input("Введи фамилию преподавателя для поиска: ").strip()

    if not teacher_name:
        print("Ошибка: Фамилия не может быть пустой!")
        return

    print(f"Ищу преподавателя '{teacher_name}' во всех группах...")
    print(f"Всего групп: {len(GROUPS)}\n")

    result = await search_teacher_public(teacher_name)

    print("=" * 50)
    print(result)
    print("=" * 50)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nПоиск прерван")
        sys.exit(0)
