import asyncio
from logging import INFO, basicConfig, getLogger

import adaptix
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.media_group import MediaGroupBuilder
from aiogram_album import AlbumMessage
import json
from aiogram_album.count_check_middleware import CountCheckAlbumMiddleware
from aiogram.enums import InputMediaType
from dataclasses import dataclass
from pathlib import Path

TOKEN = "8500606025:AAFDP_LxcENQWfq7xXGMfF3aFnE0zgXQ8iQ"
ADMIN_IDS = {644838712, 356182498, 6677740706, 1084764610}

router = Router(name=__name__)
CountCheckAlbumMiddleware(latency=0.3, router=router)
logger = getLogger(name=__name__)
DATA = Path("data.json")
data_lock = asyncio.Lock()
SOURCES = Path("sources.json")
sources_lock = asyncio.Lock()


@dataclass
class MessageMedia:
    file_id: str
    type: InputMediaType


@dataclass
class SourceMessage:
    text: str
    media: list[MessageMedia]


@router.channel_post(F.media_group_id)
async def source_channel_post_album(message: AlbumMessage) -> None:
    if message.chat.id not in (await load_sources()):
        return
    async with data_lock:
        with DATA.open(mode="r", encoding="utf-8") as f:
            data = adaptix.load(json.load(f), dict[str, SourceMessage])
    key = str(len(data.keys()) + 1)
    await message.edit_caption(caption=message.html_text + f"\n\n<code>#{key}</code>")

    text = message.html_text or next(
        (m.html_text for m in message.messages if m.html_text), ""
    )
    album = message.as_input_media()
    media = [MessageMedia(file_id=m.media, type=m.type) for m in album]
    data[key] = SourceMessage(text=text, media=media)
    async with data_lock:
        DATA.write_text(json.dumps(adaptix.dump(data, dict[str, SourceMessage])))


@router.channel_post()
async def source_channel_post(message: Message) -> None:
    if message.chat.id not in (await load_sources()):
        return
    async with data_lock:
        with DATA.open(mode="r", encoding="utf-8") as f:
            data = adaptix.load(json.load(f), dict[str, SourceMessage])
    key = str(len(data) + 1)
    if not (message.text or message.caption):
        await message.reply(text=f"<code>#{key}</code>")
    else:
        await message.edit_text(text=message.html_text + f"\n\n<code>#{key}</code>")
    content_types_map = {
        ContentType.ANIMATION: InputMediaType.VIDEO,
        ContentType.AUDIO: InputMediaType.AUDIO,
        ContentType.DOCUMENT: InputMediaType.DOCUMENT,
        ContentType.PHOTO: InputMediaType.PHOTO,
        ContentType.VIDEO: InputMediaType.VIDEO,
    }
    content_media_map = {
        InputMediaType.AUDIO: F.audio.file_id.resolve(message),
        InputMediaType.DOCUMENT: F.document.file_id.resolve(message),
        InputMediaType.VIDEO: (F.video.file_id | F.animation.file_id).resolve(message),
        InputMediaType.PHOTO: F.photo[-1].file_id.resolve(message),
    }
    media_type = content_types_map.get(message.content_type)
    if not media_type:
        data[key] = SourceMessage(text=message.html_text, media=[])
    else:
        data[key] = SourceMessage(
            text=message.html_text,
            media=[
                MessageMedia(file_id=content_media_map[media_type], type=media_type)
            ],
        )
    async with data_lock:
        DATA.write_text(json.dumps(adaptix.dump(data, dict[str, SourceMessage])))


@router.message(F.is_automatic_forward)
async def target_group_post(message: Message, bot: Bot) -> None:
    text = message.text or message.caption
    if not text:
        return
    media_hashtags = [
        line for line in text.splitlines() if line.strip().startswith("#")
    ]
    if not media_hashtags:
        logger.info("message %d without media hashtag", message.message_id)
        return
    keys = [hashtag.removeprefix("#") for hashtag in media_hashtags]
    async with data_lock:
        with DATA.open(mode="r", encoding="utf-8") as f:
            data = adaptix.load(json.loads(f.read()), dict[str, SourceMessage])
    new_caption = message.html_text
    for key in keys:
        if key not in data:
            logger.warning("skipped not existings key - %s", key)
            continue
        msg = data[key]
        builder = MediaGroupBuilder(caption=msg.text)
        for media in msg.media:
            builder.add(type=media.type, media=media.file_id, has_spoiler=True)
        await message.reply_media_group(media=builder.build(), protect_content=True)
        new_caption = new_caption.replace("#" + key, "").replace("<code></code>", "")
    if not new_caption:
        return
    try:
        await bot.edit_message_caption(
            chat_id=message.forward_origin.chat.id,
            message_id=message.forward_origin.message_id,
            caption=new_caption,
        )
    except Exception as e1:
        try:
            await bot.edit_message_text(
                text=new_caption,
                chat_id=message.forward_origin.chat.id,
                message_id=message.forward_origin.message_id,
            )
        except Exception as e2:
            logger.exception("%s - %s", e1, e2)
            raise


@router.message(Command("add"))
async def add_source(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text.strip().split()[1:]:
        return await message.reply(
            "Укажите ID канала.\nНапример: <code>/add -1001234567890</code>"
        )
    id_ = message.text.split()[1]
    if not id_.startswith("-100"):
        id_ = "-100" + id_
    try:
        channel_id = int(id_)
    except ValueError:
        return await message.reply("ID канала должен быть числом.")

    sources = await load_sources()
    if channel_id in sources:
        return await message.reply("Этот канал уже есть в списке источников.")

    sources.append(channel_id)
    await save_sources(sources)

    await message.reply(f"Источник добавлен:\n<code>{channel_id}</code>")


@router.message(Command("del"))
async def del_source(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    if not message.text.strip().split()[1:]:
        return await message.reply(
            "Укажите ID канала.\nНапример: <code>/del -1001234567890</code>"
        )

    try:
        channel_id = int(message.text.split()[1])
    except ValueError:
        return await message.reply("ID канала должен быть числом.")

    sources = await load_sources()

    if channel_id not in sources:
        return await message.reply("Такого источника нет в списке.")

    sources.remove(channel_id)
    await save_sources(sources)

    await message.reply(f"Источник удалён:\n<code>{channel_id}</code>")


@router.message(Command("list"))
async def list_sources(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return

    sources = await load_sources()

    if not sources:
        return await message.reply("Список источников пуст.")

    lines = ["<b>Список каналов-источников:</b>\n"]

    for channel_id in sources:
        try:
            chat = await bot.get_chat(channel_id)
            title = chat.title or "Без названия"

            mention = f'<a href="https://t.me/c/{str(channel_id).removeprefix("-100")}">{title}</a>'

            lines.append(f"{mention} <code>{channel_id}</code>")

        except Exception as e:
            lines.append(
                f"<i>Не удалось получить данные для</i> "
                f"<code>{channel_id}</code>\nОшибка: {e}\n"
            )

    await message.reply("\n".join(lines))


async def load_sources() -> list[int]:
    async with sources_lock:
        if not SOURCES.exists():
            SOURCES.write_text("[]")
        return json.loads(SOURCES.read_text())


async def save_sources(sources: list[int]) -> None:
    async with sources_lock:
        SOURCES.write_text(json.dumps(sources))


def main() -> None:
    basicConfig(level=INFO)

    default = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot = Bot(token=TOKEN, default=default)
    dispatcher = Dispatcher()
    dispatcher.include_routers(router)
    dispatcher.run_polling(bot)


if __name__ == "__main__":
    main()
