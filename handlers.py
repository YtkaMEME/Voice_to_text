import os
import aiofiles
import requests
import asyncio
import re

from aiogram import Router, types, F
from aiogram.types import FSInputFile

from config import ASSEMBLY_API_KEY, BOT_TOKEN
from convert import convert_to_mp3

router = Router()
user_tasks = {}

DOWNLOADS_DIR = "downloads"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def clear_downloads_dir():
    if os.path.exists(DOWNLOADS_DIR):
        for f in os.listdir(DOWNLOADS_DIR):
            try:
                file_path = os.path.join(DOWNLOADS_DIR, f)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except Exception as e:
                print(f"⚠️ Не удалось удалить {f}: {e}")


async def upload_file(file_path: str):
    with open(file_path, "rb") as f:
        response = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"authorization": ASSEMBLY_API_KEY},
            files={"file": f}
        )
    return response.json()["upload_url"]


async def transcribe(audio_url: str):
    response = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        json={"audio_url": audio_url, "speaker_labels": True, "language_code": "ru"},
        headers={"authorization": ASSEMBLY_API_KEY}
    )
    return response.json()["id"]


async def wait_for_completion(transcript_id: str):
    url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
    while True:
        response = requests.get(url, headers={"authorization": ASSEMBLY_API_KEY})
        result = response.json()
        if result["status"] == "completed":
            return result
        elif result["status"] == "error":
            raise RuntimeError(result["error"])
        await asyncio.sleep(3)


async def manual_download(bot, file_id: str, destination: str, bot_token: str):
    file_info = await bot.get_file(file_id)
    file_path = file_info.file_path

    if not file_path:
        raise RuntimeError("Файл нельзя скачать. Попробуйте отправить его как 📎 документ напрямую.")

    url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    response = requests.get(url, stream=True)

    if response.status_code == 200:
        with open(destination, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    else:
        raise RuntimeError(f"Ошибка скачивания файла: код {response.status_code}")


def download_from_url(url: str, destination: str):
    if "drive.google.com" in url:
        file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
        if not file_id_match:
            raise RuntimeError("Не удалось извлечь ID файла из ссылки Google Drive.")
        file_id = file_id_match.group(1)
        file_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    else:
        file_url = url

    response = requests.get(file_url, stream=True)
    if response.status_code == 200:
        with open(destination, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    elif response.status_code == 403:
        raise RuntimeError("Google Drive: доступ запрещён. Включи общий доступ к файлу.")
    else:
        raise RuntimeError(f"Ошибка скачивания файла: код {response.status_code}")


@router.message(F.text == "/cancel")
async def cancel_process(message: types.Message):
    uid = message.from_user.id
    task = user_tasks.pop(uid, None)
    if task and not task.done():
        task.cancel()
        await message.answer("❌ Обработка отменена по команде /cancel")
    else:
        await message.answer("Нет активной задачи для отмены.")
    clear_downloads_dir()


@router.message(F.text.regexp(r"https?://"))
async def handle_url(message: types.Message):
    url = message.text.strip()
    user_id = message.from_user.id
    input_path = os.path.join(DOWNLOADS_DIR, "external_input.mp3")
    mp3_path = input_path
    txt_path = input_path.replace(".mp3", ".txt")

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    try:
        await message.answer("🔗 Получена ссылка. Пытаюсь скачать...")
        await asyncio.to_thread(download_from_url, url, input_path)
    except Exception as e:
        await message.answer(f"⚠️ Не удалось скачать файл: {e}")
        return

    await run_processing(message, user_id, input_path, mp3_path, txt_path)


@router.message(lambda msg: msg.document and msg.document.mime_type.startswith("audio/"))
async def handle_audio_file(message: types.Message):
    user_id = message.from_user.id
    file = message.document
    file_name = file.file_name or f"{file.file_unique_id}.audio"
    input_path = os.path.join(DOWNLOADS_DIR, file_name)
    mp3_path = f"{input_path}.mp3"
    txt_path = f"{input_path}.txt"

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    try:
        if file.file_size > MAX_FILE_SIZE:
            await message.answer("⚠️ Файл слишком большой. Пожалуйста, загрузите его по ссылке Google Drive.")
            return

        await message.answer(
            f"ℹ️ Получен файл: {file.file_name}\n"
            f"Размер: {round(file.file_size / (1024 * 1024), 2)} MB"
        )
        await manual_download(message.bot, file.file_id, input_path, BOT_TOKEN)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при скачивании файла: {e}")
        return

    await run_processing(message, user_id, input_path, mp3_path, txt_path)


@router.message(F.voice)
async def handle_voice_message(message: types.Message):
    user_id = message.from_user.id
    file = message.voice
    input_path = os.path.join(DOWNLOADS_DIR, f"{file.file_unique_id}.ogg")
    mp3_path = f"{input_path}.mp3"
    txt_path = f"{input_path}.txt"

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    try:
        await message.answer("🎤 Обрабатываю голосовое сообщение...")
        await manual_download(message.bot, file.file_id, input_path, BOT_TOKEN)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при скачивании голосового сообщения: {e}")
        return

    await run_processing(message, user_id, input_path, mp3_path, txt_path)


async def run_processing(message: types.Message, user_id: int, input_path: str, mp3_path: str, txt_path: str):
    async def process():
        try:
            mp3_path_actual = convert_to_mp3(input_path)
            await message.answer("🧠 Начинаю распознавание...")

            audio_url = await upload_file(mp3_path_actual)
            transcript_id = await transcribe(audio_url)
            result = await wait_for_completion(transcript_id)

            output_text = ""
            for utt in result.get("utterances", []):
                output_text += f"Спикер {utt['speaker']}: {utt['text']}\n"

            async with aiofiles.open(txt_path, "w", encoding="utf-8") as f:
                await f.write(output_text)

            if os.path.getsize(txt_path) <= MAX_FILE_SIZE:
                await message.answer_document(
                    FSInputFile(txt_path),
                    caption="✅ Расшифровка завершена"
                )
            else:
                await message.answer("📎 Файл слишком большой для Telegram. Загрузи его позже вручную или обратись к разработчику.")

        except asyncio.CancelledError:
            await message.answer("⚠️ Обработка была отменена.")
        except Exception as e:
            await message.answer(f"Ошибка при обработке: {e}")
        finally:
            clear_downloads_dir()
            user_tasks.pop(user_id, None)

    task = asyncio.create_task(process())
    user_tasks[user_id] = task