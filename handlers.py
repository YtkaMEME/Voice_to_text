import os
import time
import aiofiles
import requests
import asyncio
import shutil

from aiogram import Router, types, F
from aiogram.types import FSInputFile

from config import ASSEMBLY_API_KEY
from convert import convert_to_mp3

router = Router()
user_tasks = {}

DOWNLOADS_DIR = "downloads"


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


@router.message(lambda msg: msg.audio or msg.voice)
async def handle_audio(message: types.Message):
    user_id = message.from_user.id
    file = message.audio or message.voice
    file_name = f"{file.file_unique_id}"
    input_path = os.path.join(DOWNLOADS_DIR, file_name)
    mp3_path = f"{input_path}.mp3"
    txt_path = f"{input_path}.txt"

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    await message.bot.download(file, destination=input_path)

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

            if not os.path.exists(txt_path):
                await message.answer("⚠️ Не удалось найти файл для отправки.")
                return

            await message.answer_document(
                FSInputFile(txt_path),
                caption="✅ Расшифровка завершена"
            )

        except asyncio.CancelledError:
            await message.answer("⚠️ Обработка была отменена.")
        except Exception as e:
            await message.answer(f"Ошибка при распознавании: {e}")
        finally:
            clear_downloads_dir()
            user_tasks.pop(user_id, None)

    task = asyncio.create_task(process())
    user_tasks[user_id] = task