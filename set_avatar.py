import os
import asyncio
from telegram import Bot, InputProfilePhotoStatic

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


async def set_photo(filename: str):
    bot = Bot(token=TOKEN)
    with open(filename, "rb") as f:
        photo = InputProfilePhotoStatic(photo=f)
        await bot.set_my_profile_photo(photo=photo)
    print(f"Аватарка успешно установлена из файла: {filename}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Использование: python set_avatar.py <имя_файла>")
        sys.exit(1)
    filename = sys.argv[1]
    if not os.path.exists(filename):
        print(f"Файл не найден: {filename}")
        sys.exit(1)
    asyncio.run(set_photo(filename))
