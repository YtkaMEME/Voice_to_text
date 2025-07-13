import os
import subprocess
from pydub import AudioSegment
import ffmpeg


def get_true_format(input_path: str) -> str:
    try:
        probe = ffmpeg.probe(input_path)
        fmt = probe["format"]["format_name"]
        # если это что-то вроде "aac", "ogg", "mp3"
        return fmt.split(',')[0]
    except Exception as e:
        raise RuntimeError(f"Не удалось определить формат: {e}")


def convert_to_mp3(input_path: str) -> str:
    try:
        real_format = get_true_format(input_path)
        print(f"📦 Определён формат: {real_format}")
        audio = AudioSegment.from_file(input_path, format=real_format)
    except Exception as e:
        raise RuntimeError(f"Ошибка при чтении файла {input_path}: {e}")

    output_path = input_path.rsplit(".", 1)[0] + ".mp3"
    audio.export(output_path, format="mp3")
    return output_path