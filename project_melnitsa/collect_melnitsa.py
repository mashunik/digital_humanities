import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
import sqlite3
import time
from pathlib import Path


DB_NAME = "melnitsa.db"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://genius.com/",
}


connection = sqlite3.connect(DB_NAME)
cursor = connection.cursor()


# 1. СОБИРАЕМ АЛЬБОМЫ

cursor.execute("""
    CREATE TABLE IF NOT EXISTS albums (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        year INTEGER,
        url TEXT UNIQUE
    )
""")

url = "https://genius.com/artists/Melnitsa/albums"

response = requests.get(url, headers=headers)
soup = BeautifulSoup(response.text, "html.parser")

albums = []

for link in soup.find_all("a", href=True):
    href = link["href"]

    if "/albums/Melnitsa/" in href:
        title = link.get_text(strip=True)
        album_url = urljoin("https://genius.com", href)

        album = {
            "title_raw": title,
            "url": album_url
        }

        if album not in albums:
            albums.append(album)


for album in albums:
    title_raw = album["title_raw"]
    url = album["url"]

    # Пропускаем эти альбомы, так как они не полноценные
    if "Рождественские песни" in title_raw or "Master of the Mill" in title_raw:
        continue

    # Оставляем только русское название до скобки
    title = title_raw.split("(")[0].strip()

    # Достаем год из строки
    year_match = re.search(r"20\d{2}", title_raw)
    year = int(year_match.group())

    # Сохраняем альбом в базу
    cursor.execute("""
        INSERT OR IGNORE INTO albums (title, year, url)
        VALUES (?, ?, ?)
    """, (title, year, url))


connection.commit()


# 2. СОБИРАЕМ ПЕСНИ И ТЕКСТЫ

cursor.execute("""
    CREATE TABLE IF NOT EXISTS songs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        album_id INTEGER,
        title TEXT,
        track_number INTEGER,
        url TEXT UNIQUE,
        lyrics_path TEXT,
        FOREIGN KEY (album_id) REFERENCES albums(id)
    )
""")


# Достаем все альбомы из базы
cursor.execute("SELECT id, title, year, url FROM albums")
albums = cursor.fetchall()


# Создаем папку, куда будут сохраняться тексты
lyrics_root = Path("lyrics")
lyrics_root.mkdir(exist_ok=True)


for album in albums:
    album_id = album[0]
    album_title = album[1]
    album_year = album[2]
    album_url = album[3]

    # Скачиваем страницу альбома
    response = requests.get(album_url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")

    # Создаем папку для конкретного альбома
    folder_name = f"{album_year}_{album_title}"
    album_folder = lyrics_root / folder_name
    album_folder.mkdir(exist_ok=True)

    # Ищем ссылки на песни внутри альбома
    song_links = []

    for link in soup.find_all("a", href=True):
        href = link["href"]

        song_url = urljoin("https://genius.com", href)

        # Берем только страницы песен Мельницы
        if "genius.com/Melnitsa-" not in song_url:
            continue
        if "-lyrics" not in song_url:
            continue

        song_title = link.get_text(" ", strip=True)

        # Убираем Lyrics в конце названия
        song_title = re.sub(r"\s*Lyrics$", "", song_title).strip()

        # Оставляем русское название до скобки
        song_title = song_title.split("(")[0].strip()
        song_links.append((song_title, song_url))

    # Заходим на страницу каждой песни
    for number, song in enumerate(song_links, start=1):
        song_title = song[0]
        song_url = song[1]

        song_response = requests.get(song_url, headers=headers)
        song_soup = BeautifulSoup(song_response.text, "html.parser")

        lyrics_blocks = song_soup.select('div[data-lyrics-container="true"]')
        lyrics_parts = []

        for block in lyrics_blocks:
            for br in block.find_all("br"):
                br.replace_with("\n")

            text = block.get_text("", strip=False)
            lyrics_parts.append(text)

        lyrics = "\n".join(lyrics_parts)

        # Чистим пробелы и переносы
        lyrics = re.sub(r"[ \t]+", " ", lyrics)
        lyrics = re.sub(r" *\n *", "\n", lyrics)
        lyrics = re.sub(r"\n{2,}", "\n", lyrics)
        lyrics = lyrics.strip()

        # Убираем приклеенную шапку
        lyrics = re.sub(
            r"^\d+\s*Contributors?.*?Lyrics",
            "",
            lyrics,
            count=1,
            flags=re.DOTALL
        ).strip()

        # Если в тексте почти нет русских слов, не сохраняем песню
        russian_words = re.findall(r"[А-Яа-яЁё]+", lyrics)
        if len(russian_words) < 3:
            continue

        file_name = f"{number:02d}_{song_title}.txt"
        file_name = re.sub(r"\s+", "_", file_name)

        lyrics_path = album_folder / file_name

        # Сохраняем текст песни в файл
        lyrics_path.write_text(lyrics, encoding="utf-8")

        # Сохраняем информацию о песне в базу
        cursor.execute("""
            INSERT OR IGNORE INTO songs (album_id, title, track_number, url, lyrics_path)
            VALUES (?, ?, ?, ?, ?)
        """, (album_id, song_title, number, song_url, str(lyrics_path)))

        connection.commit()
        time.sleep(1)


connection.close()
