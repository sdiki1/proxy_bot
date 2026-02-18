from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Iterable
from urllib.parse import unquote, urlencode, urlparse

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User as TelegramUser

from .database import Database, Plan
from .keyboards import (
    EMOJI_BOX,
    EMOJI_DEV,
    EMOJI_GEM,
    EMOJI_SHIELD,
    admin_cancel_keyboard,
    admin_panel_keyboard,
    back_to_menu_keyboard,
    main_menu_keyboard,
    payment_keyboard,
    plans_keyboard,
)

logger = logging.getLogger(__name__)

PROXY_FOOTER = "Made with @proxy_sdiki1_bot"
TEMP_KIND_PROXY_OUTPUT = "proxy_output"
MAX_ACTIVE_PROXIES_PER_USER = 5
BLOCKED_TG_USER_ID = 1664076316
BLOCKED_USER_TEXT = "ЛАВРЕНТ ИДИ НАХУЙ, СУКА!\n\nЗа 25₽ мне на карту ты помилован"
DEFAULT_BAN_TEXT = "Доступ к боту ограничен администратором."


class AdminStates(StatesGroup):
    broadcast_all = State()
    broadcast_user = State()
    ban_user = State()
    unban_user = State()
    user_configs = State()
    grant_proxies = State()
    remove_proxies = State()


@dataclass(frozen=True)
class UserProfile:
    id: int
    tg_user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None


def format_ts(timestamp: int) -> str:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.strftime("%d.%m.%Y %H:%M UTC")


def format_remaining(expires_at: int) -> str:
    delta = expires_at - int(datetime.now(tz=timezone.utc).timestamp())
    if delta <= 0:
        return "истекло"
    days, rest = divmod(delta, 86400)
    hours, _ = divmod(rest, 3600)
    if days > 0:
        return f"{days} д. {hours} ч."
    return f"{hours} ч."


def tg_emoji(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def build_welcome_text() -> str:
    return (
        f"{tg_emoji(EMOJI_SHIELD, '🛡')} <b>ProxyBot</b> выдает персональные SOCKS5-прокси,\n"
        "привязанные к вашему Telegram-профилю.\n\n"
        f"{tg_emoji(EMOJI_GEM, '💎')} Каждая покупка действует <b>30 дней</b>.\n"
        f"{tg_emoji(EMOJI_DEV, '📱')} Подключение в Telegram — в пару кликов."
    )


def build_help_text() -> str:
    return (
        f"{tg_emoji(EMOJI_SHIELD, '🛡')} <b>Команды бота</b>\n\n"
        "/start — главное меню\n"
        "/plans — тарифы\n"
        "/buy — купить тариф\n"
        "/my_links — мои прокси\n"
        "/status — подписка\n"
        "/help — помощь"
    )


def build_plans_text(plans: list[Plan]) -> str:
    lines = [
        f"{tg_emoji(EMOJI_SHIELD, '🛡')} <b>Тарифы ProxyBot</b>",
        "",
        "Выберите подходящий план на <b>30 дней</b>:",
        "",
    ]
    for plan in plans:
        lines.append(f"• <b>{plan.title}</b> — <b>{plan.price_rub}₽ / мес</b>")
    lines.extend(
        [
            "",
            f"{tg_emoji(EMOJI_GEM, '💎')} После подтверждения оплаты прокси выдаются сразу.",
        ]
    )
    return "\n".join(lines)


def build_admin_panel_text() -> str:
    return (
        f"{tg_emoji(EMOJI_SHIELD, '🛡')} <b>Админ-панель</b>\n\n"
        "Выберите действие из меню ниже."
    )


def normalize_user_profile(row: dict) -> UserProfile:
    return UserProfile(
        id=int(row["id"]),
        tg_user_id=int(row["tg_user_id"]),
        username=str(row["username"]) if row.get("username") else None,
        first_name=str(row["first_name"]) if row.get("first_name") else None,
        last_name=str(row["last_name"]) if row.get("last_name") else None,
    )


def user_proxy_label_from_profile(profile: UserProfile) -> str:
    if profile.username:
        return f"{profile.username}/{profile.tg_user_id}"
    return str(profile.tg_user_id)


def user_display_name(profile: UserProfile) -> str:
    parts = [item for item in [profile.first_name, profile.last_name] if item]
    if parts:
        return " ".join(parts)
    if profile.username:
        return f"@{profile.username}"
    return str(profile.tg_user_id)


def chunk_lines(lines: Iterable[str], max_len: int = 3500) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > max_len:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def is_admin(tg_user_id: int, admin_tg_ids: set[int]) -> bool:
    return tg_user_id in admin_tg_ids


def extract_text_payload(message: Message) -> str | None:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    return None


def parse_int(raw: str) -> int | None:
    try:
        return int(raw.strip())
    except ValueError:
        return None


async def ensure_user(
    db: Database,
    telegram_user: TelegramUser,
    *,
    bot=None,
    admin_tg_ids: set[int] | None = None,
) -> int:
    existed = await db.get_user_by_tg_user_id(telegram_user.id)
    user_id = await db.upsert_user(
        tg_user_id=telegram_user.id,
        username=telegram_user.username,
        first_name=telegram_user.first_name,
        last_name=telegram_user.last_name,
    )
    if existed is None and bot is not None and admin_tg_ids:
        username = f"@{telegram_user.username}" if telegram_user.username else "без username"
        full_name = " ".join(
            item for item in [telegram_user.first_name, telegram_user.last_name] if item
        ).strip() or "без имени"
        text = (
            "Новый пользователь в боте.\n"
            f"ID: {telegram_user.id}\n"
            f"Username: {username}\n"
            f"Имя: {full_name}"
        )
        for admin_id in admin_tg_ids:
            if admin_id == telegram_user.id:
                continue
            try:
                await bot.send_message(admin_id, text, parse_mode=None)
            except (TelegramBadRequest, TelegramForbiddenError):
                logger.warning("Could not send new-user notification to admin %s", admin_id)
    return user_id


async def blocked_text_for_user(db: Database, tg_user_id: int) -> str | None:
    if tg_user_id == BLOCKED_TG_USER_ID:
        return BLOCKED_USER_TEXT
    ban = await db.get_user_ban(tg_user_id)
    if ban is None:
        return None
    reason = str(ban.get("reason") or "").strip()
    return reason or DEFAULT_BAN_TEXT


async def handle_blocked_message(db: Database, message: Message) -> bool:
    if message.from_user is None:
        return False
    blocked_text = await blocked_text_for_user(db, message.from_user.id)
    if blocked_text is None:
        return False
    await message.answer(blocked_text)
    return True


async def handle_blocked_callback(db: Database, callback: CallbackQuery) -> bool:
    blocked_text = await blocked_text_for_user(db, callback.from_user.id)
    if blocked_text is None:
        return False
    if callback.message is not None:
        try:
            await callback.message.edit_text(blocked_text, reply_markup=None, parse_mode=None)
        except TelegramBadRequest:
            await callback.bot.send_message(callback.from_user.id, blocked_text)
    else:
        await callback.bot.send_message(callback.from_user.id, blocked_text)
    await callback.answer()
    return True


def profile_label(telegram_user: TelegramUser) -> str:
    if telegram_user.username:
        return f"{telegram_user.username}/{telegram_user.id}"
    return str(telegram_user.id)


def telegram_socks_link(server: str, port: int, username: str, password: str) -> str:
    query = urlencode(
        {
            "server": server,
            "port": port,
            "user": username,
            "pass": password,
        }
    )
    return f"https://t.me/socks?{query}"


def parse_socks5_url(link: str) -> tuple[str, int, str, str] | None:
    parsed = urlparse(link)
    if parsed.scheme != "socks5":
        return None
    if parsed.hostname is None or parsed.port is None:
        return None
    if parsed.username is None or parsed.password is None:
        return None
    return parsed.hostname, parsed.port, unquote(parsed.username), unquote(parsed.password)


def build_proxy_block(*, proxy_index: int, user_proxy_label: str, proxy_id: int, tg_link: str) -> str:
    return (
        f"PROXY-{proxy_index}-{user_proxy_label}\n"
        f"Proxy ID: {proxy_id}\n\n"
        f"{tg_link}\n\n"
        f"{PROXY_FOOTER}"
    )


def build_proxy_limit_text(*, active_count: int, requested_count: int) -> str:
    remaining = max(0, MAX_ACTIVE_PROXIES_PER_USER - active_count)
    return (
        f"Лимит на пользователя: не более {MAX_ACTIVE_PROXIES_PER_USER} прокси.\n"
        f"Сейчас активно: {active_count}.\n"
        f"Этот тариф добавляет: {requested_count}.\n"
        f"Доступно для выдачи: {remaining}."
    )


async def log_proxy_delivery(
    *,
    db: Database,
    proxy_id: int,
    user_id: int,
    tg_user_id: int,
    user_proxy_label: str,
    subscription_id: int | None,
    device_number: int | None,
    delivery_source: str,
    tg_link: str,
) -> None:
    await db.log_proxy_delivery(
        proxy_link_id=proxy_id,
        user_id=user_id,
        tg_user_id=tg_user_id,
        user_label=user_proxy_label,
        subscription_id=subscription_id,
        device_number=device_number,
        delivery_source=delivery_source,
        proxy_url=tg_link,
    )
    logger.info(
        "Delivered proxy: tg_user_id=%s user_id=%s proxy_id=%s subscription_id=%s source=%s url=%s",
        tg_user_id,
        user_id,
        proxy_id,
        subscription_id,
        delivery_source,
        tg_link,
    )


async def cleanup_proxy_output_messages(*, db: Database, bot, user_id: int) -> None:
    rows = await db.pop_temp_messages(user_id=user_id, kind=TEMP_KIND_PROXY_OUTPUT)
    for row in rows:
        try:
            await bot.delete_message(int(row["tg_user_id"]), int(row["message_id"]))
        except TelegramBadRequest:
            pass


async def edit_or_send(
    callback: CallbackQuery,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    parse_mode: str | None,
) -> None:
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=reply_markup, parse_mode=parse_mode)


async def send_links_list(
    *,
    db: Database,
    bot_chat_id: int,
    bot,
    user_id: int,
    tg_user_id: int,
    user_proxy_label: str,
    source_message: Message | None = None,
) -> None:
    links = await db.get_active_links_for_user(user_id)
    if not links:
        text = (
            f"{tg_emoji(EMOJI_DEV, '📱')} У вас пока нет активных прокси.\n"
            "Выберите тариф через /buy или кнопку «Тарифы»."
        )
        if source_message is not None:
            await source_message.edit_text(text, reply_markup=main_menu_keyboard())
        else:
            await bot.send_message(bot_chat_id, text, reply_markup=main_menu_keyboard())
        return

    proxies: list[dict[str, int | str | None]] = []
    for index, row in enumerate(links, start=1):
        parsed = parse_socks5_url(str(row["link"]))
        if parsed is None:
            continue
        host, port, username, password = parsed
        tg_link = telegram_socks_link(host, port, username, password)
        proxy_id = int(row["id"])
        proxies.append(
            {
                "index": index,
                "proxy_id": proxy_id,
                "tg_link": tg_link,
                "subscription_id": int(row["subscription_id"]),
                "device_number": int(row["device_number"]),
            }
        )

    await send_proxy_sequence(
        db=db,
        bot=bot,
        bot_chat_id=bot_chat_id,
        user_id=user_id,
        tg_user_id=tg_user_id,
        user_proxy_label=user_proxy_label,
        proxies=proxies,
        delivery_source="my_links",
        source_message=source_message,
    )


async def send_proxy_sequence(
    *,
    db: Database,
    bot,
    bot_chat_id: int,
    user_id: int,
    tg_user_id: int,
    user_proxy_label: str,
    proxies: list[dict[str, int | str | None]],
    delivery_source: str,
    source_message: Message | None = None,
) -> None:
    await cleanup_proxy_output_messages(db=db, bot=bot, user_id=user_id)

    if source_message is not None:
        try:
            await source_message.delete()
        except TelegramBadRequest:
            pass

    if not proxies:
        await bot.send_message(
            bot_chat_id,
            "Не удалось подготовить ссылки для Telegram из сохраненных прокси.",
            reply_markup=main_menu_keyboard(),
        )
        return

    for item in proxies:
        text = build_proxy_block(
            proxy_index=int(item["index"]),
            user_proxy_label=user_proxy_label,
            proxy_id=int(item["proxy_id"]),
            tg_link=str(item["tg_link"]),
        )
        sent = await bot.send_message(bot_chat_id, text, parse_mode=None)
        await db.add_temp_message(
            user_id=user_id,
            tg_user_id=tg_user_id,
            message_id=sent.message_id,
            kind=TEMP_KIND_PROXY_OUTPUT,
        )
        await log_proxy_delivery(
            db=db,
            proxy_id=int(item["proxy_id"]),
            user_id=user_id,
            tg_user_id=tg_user_id,
            user_proxy_label=user_proxy_label,
            subscription_id=int(item["subscription_id"]) if item["subscription_id"] is not None else None,
            device_number=int(item["device_number"]) if item["device_number"] is not None else None,
            delivery_source=delivery_source,
            tg_link=str(item["tg_link"]),
        )

    control = await bot.send_message(
        bot_chat_id,
        "Перейти в главное меню:",
        reply_markup=back_to_menu_keyboard(),
    )
    await db.add_temp_message(
        user_id=user_id,
        tg_user_id=tg_user_id,
        message_id=control.message_id,
        kind=TEMP_KIND_PROXY_OUTPUT,
    )


async def send_status(
    *,
    db: Database,
    bot_chat_id: int,
    bot,
    user_id: int,
    edit_message: Message | None = None,
) -> None:
    subscriptions = await db.get_active_subscriptions_for_user(user_id)
    if not subscriptions:
        text = f"{tg_emoji(EMOJI_BOX, '📦')} У вас нет активной подписки.\nОформите тариф через /buy."
        if edit_message is not None:
            await edit_message.edit_text(text, reply_markup=main_menu_keyboard())
        else:
            await bot.send_message(bot_chat_id, text, reply_markup=main_menu_keyboard())
        return

    lines = [f"{tg_emoji(EMOJI_BOX, '📦')} <b>Активные подписки</b>", ""]
    for sub in subscriptions:
        expires_at = int(sub["expires_at"])
        lines.append(
            f"• #{sub['id']} — {sub['plan_title']} — до {format_ts(expires_at)} "
            f"(осталось {format_remaining(expires_at)})"
        )

    text = "\n".join(lines)
    if edit_message is not None:
        await edit_message.edit_text(text, reply_markup=main_menu_keyboard())
    else:
        await bot.send_message(bot_chat_id, text, reply_markup=main_menu_keyboard())


def create_router(db: Database, proxy_public_host: str, admin_tg_ids: tuple[int, ...] = ()) -> Router:
    router = Router()
    admin_ids = set(admin_tg_ids)

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        if await handle_blocked_message(db, message):
            return
        if message.from_user is None:
            return
        await ensure_user(
            db,
            message.from_user,
            bot=message.bot,
            admin_tg_ids=admin_ids,
        )
        await message.answer(build_welcome_text(), reply_markup=main_menu_keyboard())

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        if await handle_blocked_message(db, message):
            return
        if message.from_user is not None:
            await ensure_user(
                db,
                message.from_user,
                bot=message.bot,
                admin_tg_ids=admin_ids,
            )
        await message.answer(build_help_text())

    @router.message(Command("plans"))
    @router.message(Command("buy"))
    async def cmd_plans(message: Message) -> None:
        if await handle_blocked_message(db, message):
            return
        if message.from_user is None:
            return
        await ensure_user(
            db,
            message.from_user,
            bot=message.bot,
            admin_tg_ids=admin_ids,
        )
        plans = await db.get_plans()
        await message.answer(build_plans_text(plans), reply_markup=plans_keyboard(plans))

    @router.message(Command("my_links"))
    async def cmd_links(message: Message) -> None:
        if await handle_blocked_message(db, message):
            return
        if message.from_user is None:
            return
        user_id = await ensure_user(
            db,
            message.from_user,
            bot=message.bot,
            admin_tg_ids=admin_ids,
        )
        await send_links_list(
            db=db,
            bot_chat_id=message.chat.id,
            bot=message.bot,
            user_id=user_id,
            tg_user_id=message.from_user.id,
            user_proxy_label=profile_label(message.from_user),
        )

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if await handle_blocked_message(db, message):
            return
        if message.from_user is None:
            return
        user_id = await ensure_user(
            db,
            message.from_user,
            bot=message.bot,
            admin_tg_ids=admin_ids,
        )
        await send_status(db=db, bot_chat_id=message.chat.id, bot=message.bot, user_id=user_id)

    @router.message(Command("admin"))
    async def cmd_admin(message: Message, state: FSMContext) -> None:
        if await handle_blocked_message(db, message):
            return
        if message.from_user is None:
            return
        await ensure_user(
            db,
            message.from_user,
            bot=message.bot,
            admin_tg_ids=admin_ids,
        )
        if not is_admin(message.from_user.id, admin_ids):
            await message.answer("Доступ запрещен.")
            return
        await state.clear()
        await message.answer(build_admin_panel_text(), reply_markup=admin_panel_keyboard(), parse_mode="HTML")

    async def ensure_admin_message_access(message: Message, state: FSMContext) -> bool:
        if message.from_user is None:
            return False
        await ensure_user(
            db,
            message.from_user,
            bot=message.bot,
            admin_tg_ids=admin_ids,
        )
        if not is_admin(message.from_user.id, admin_ids):
            await state.clear()
            await message.answer("Доступ запрещен.")
            return False
        return True

    async def ensure_admin_callback_access(callback: CallbackQuery, state: FSMContext) -> bool:
        await ensure_user(
            db,
            callback.from_user,
            bot=callback.bot,
            admin_tg_ids=admin_ids,
        )
        if not is_admin(callback.from_user.id, admin_ids):
            await state.clear()
            await callback.answer("Доступ запрещен.", show_alert=True)
            return False
        return True

    @router.callback_query(F.data == "admin:menu")
    async def cb_admin_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.clear()
        await edit_or_send(
            callback,
            text=build_admin_panel_text(),
            reply_markup=admin_panel_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:cancel")
    async def cb_admin_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.clear()
        await edit_or_send(
            callback,
            text=build_admin_panel_text(),
            reply_markup=admin_panel_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer("Отменено")

    @router.callback_query(F.data == "admin:close")
    async def cb_admin_close(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.clear()
        if callback.message is not None:
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
        await callback.answer("Закрыто")

    @router.callback_query(F.data == "admin:broadcast_all")
    async def cb_admin_broadcast_all(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.set_state(AdminStates.broadcast_all)
        await edit_or_send(
            callback,
            text="Отправьте текст для рассылки всем пользователям.",
            reply_markup=admin_cancel_keyboard(),
            parse_mode=None,
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:broadcast_user")
    async def cb_admin_broadcast_user(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.set_state(AdminStates.broadcast_user)
        await edit_or_send(
            callback,
            text="Формат: <tg_user_id> <текст>\nПример: 123456789 Тестовое сообщение",
            reply_markup=admin_cancel_keyboard(),
            parse_mode=None,
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:ban")
    async def cb_admin_ban(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.set_state(AdminStates.ban_user)
        await edit_or_send(
            callback,
            text=(
                "Формат: <tg_user_id> [текст блокировки]\n"
                "Пример: 123456789 Доступ к боту ограничен."
            ),
            reply_markup=admin_cancel_keyboard(),
            parse_mode=None,
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:unban")
    async def cb_admin_unban(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.set_state(AdminStates.unban_user)
        await edit_or_send(
            callback,
            text="Формат: <tg_user_id>\nПример: 123456789",
            reply_markup=admin_cancel_keyboard(),
            parse_mode=None,
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:list_users")
    async def cb_admin_list_users(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.clear()
        rows = await db.list_users_with_stats(limit=500, offset=0)
        if not rows:
            await edit_or_send(
                callback,
                text="Пользователей пока нет.",
                reply_markup=admin_panel_keyboard(),
                parse_mode=None,
            )
            await callback.answer()
            return

        lines = [f"Пользователи: {len(rows)}", ""]
        for row in rows:
            username = f"@{row['username']}" if row.get("username") else "без username"
            active_count = int(row.get("active_proxies") or 0)
            banned_flag = int(row.get("is_banned") or 0) == 1 or int(row["tg_user_id"]) == BLOCKED_TG_USER_ID
            banned = "да" if banned_flag else "нет"
            lines.append(
                f"tg:{row['tg_user_id']} | {username} | активных:{active_count} | бан:{banned}"
            )

        chunks = chunk_lines(lines)
        await edit_or_send(
            callback,
            text=chunks[0],
            reply_markup=admin_panel_keyboard(),
            parse_mode=None,
        )
        for chunk in chunks[1:]:
            await callback.bot.send_message(callback.from_user.id, chunk)
        await callback.answer()

    @router.callback_query(F.data == "admin:user_configs")
    async def cb_admin_user_configs(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.set_state(AdminStates.user_configs)
        await edit_or_send(
            callback,
            text="Введите tg_user_id пользователя для просмотра конфигов.",
            reply_markup=admin_cancel_keyboard(),
            parse_mode=None,
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:grant_proxies")
    async def cb_admin_grant_proxies(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.set_state(AdminStates.grant_proxies)
        await edit_or_send(
            callback,
            text=(
                "Формат: <tg_user_id> <кол-во> [дней]\n"
                "Кол-во поддерживается только 1, 5 или 15."
            ),
            reply_markup=admin_cancel_keyboard(),
            parse_mode=None,
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:remove_proxies")
    async def cb_admin_remove_proxies(callback: CallbackQuery, state: FSMContext) -> None:
        if await handle_blocked_callback(db, callback):
            return
        if not await ensure_admin_callback_access(callback, state):
            return
        await state.set_state(AdminStates.remove_proxies)
        await edit_or_send(
            callback,
            text=(
                "Формат: <tg_user_id> <proxy_id|all>\n"
                "Пример: 123456789 42 или 123456789 all"
            ),
            reply_markup=admin_cancel_keyboard(),
            parse_mode=None,
        )
        await callback.answer()

    @router.message(AdminStates.broadcast_all)
    async def admin_state_broadcast_all(message: Message, state: FSMContext) -> None:
        if await handle_blocked_message(db, message):
            return
        if not await ensure_admin_message_access(message, state):
            return
        payload = extract_text_payload(message)
        if payload is None:
            await message.answer("Отправьте текстовое сообщение.")
            return

        targets = await db.get_all_tg_user_ids()
        sent_ok = 0
        sent_fail = 0
        for tg_user_id in targets:
            try:
                await message.bot.send_message(tg_user_id, payload, parse_mode=None)
                sent_ok += 1
            except (TelegramBadRequest, TelegramForbiddenError):
                sent_fail += 1

        await state.clear()
        await message.answer(
            f"Рассылка завершена.\nУспешно: {sent_ok}\nОшибок: {sent_fail}",
            reply_markup=admin_panel_keyboard(),
        )

    @router.message(AdminStates.broadcast_user)
    async def admin_state_broadcast_user(message: Message, state: FSMContext) -> None:
        if await handle_blocked_message(db, message):
            return
        if not await ensure_admin_message_access(message, state):
            return
        payload = extract_text_payload(message)
        if payload is None:
            await message.answer("Отправьте текст в формате: <tg_user_id> <текст>.")
            return
        parts = payload.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Неверный формат. Пример: 123456789 Тест")
            return
        tg_user_id = parse_int(parts[0])
        if tg_user_id is None:
            await message.answer("tg_user_id должен быть числом.")
            return
        text = parts[1].strip()
        if not text:
            await message.answer("Текст рассылки пустой.")
            return

        try:
            await message.bot.send_message(tg_user_id, text, parse_mode=None)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            await message.answer(f"Не удалось отправить сообщение: {exc}")
            return

        await state.clear()
        await message.answer("Сообщение отправлено.", reply_markup=admin_panel_keyboard())

    @router.message(AdminStates.ban_user)
    async def admin_state_ban_user(message: Message, state: FSMContext) -> None:
        if await handle_blocked_message(db, message):
            return
        if not await ensure_admin_message_access(message, state):
            return
        payload = extract_text_payload(message)
        if payload is None:
            await message.answer("Неверный формат.")
            return
        parts = payload.split(maxsplit=1)
        tg_user_id = parse_int(parts[0])
        if tg_user_id is None:
            await message.answer("tg_user_id должен быть числом.")
            return
        reason = parts[1].strip() if len(parts) > 1 else DEFAULT_BAN_TEXT
        reason = reason or DEFAULT_BAN_TEXT
        await db.ban_user(tg_user_id=tg_user_id, reason=reason, blocked_by=message.from_user.id)

        try:
            await message.bot.send_message(tg_user_id, reason, parse_mode=None)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

        await state.clear()
        await message.answer(
            f"Пользователь {tg_user_id} заблокирован.",
            reply_markup=admin_panel_keyboard(),
        )

    @router.message(AdminStates.unban_user)
    async def admin_state_unban_user(message: Message, state: FSMContext) -> None:
        if await handle_blocked_message(db, message):
            return
        if not await ensure_admin_message_access(message, state):
            return
        payload = extract_text_payload(message)
        if payload is None:
            await message.answer("Неверный формат.")
            return
        tg_user_id = parse_int(payload)
        if tg_user_id is None:
            await message.answer("tg_user_id должен быть числом.")
            return
        if tg_user_id == BLOCKED_TG_USER_ID:
            await message.answer("Этого пользователя нельзя разбанить из панели.")
            return
        changed = await db.unban_user(tg_user_id)
        await state.clear()
        if changed:
            await message.answer(
                f"Пользователь {tg_user_id} разблокирован.",
                reply_markup=admin_panel_keyboard(),
            )
        else:
            await message.answer(
                f"Пользователь {tg_user_id} не был в бане.",
                reply_markup=admin_panel_keyboard(),
            )

    @router.message(AdminStates.user_configs)
    async def admin_state_user_configs(message: Message, state: FSMContext) -> None:
        if await handle_blocked_message(db, message):
            return
        if not await ensure_admin_message_access(message, state):
            return
        payload = extract_text_payload(message)
        if payload is None:
            await message.answer("Введите tg_user_id.")
            return
        tg_user_id = parse_int(payload)
        if tg_user_id is None:
            await message.answer("tg_user_id должен быть числом.")
            return

        user_row = await db.get_user_by_tg_user_id(tg_user_id)
        if user_row is None:
            await message.answer("Пользователь не найден.")
            return
        profile = normalize_user_profile(user_row)
        ban = await db.get_user_ban(profile.tg_user_id)
        links = await db.get_all_links_for_user(profile.id)

        lines = [
            f"Пользователь: {user_display_name(profile)}",
            f"tg_user_id: {profile.tg_user_id}",
            f"username: @{profile.username}" if profile.username else "username: -",
            f"Бан: {'да' if ban is not None or profile.tg_user_id == BLOCKED_TG_USER_ID else 'нет'}",
            f"Всего конфигов: {len(links)}",
            "",
        ]
        if not links:
            lines.append("Конфиги отсутствуют.")
        else:
            for row in links:
                lines.append(
                    f"ID:{row['id']} | sub:{row['subscription_id']} | device:{row['device_number']} | "
                    f"status:{row['status']} | exp:{format_ts(int(row['expires_at']))}"
                )
                lines.append(str(row["link"]))
                lines.append("")

        await state.clear()
        chunks = chunk_lines(lines)
        await message.answer(chunks[0], reply_markup=admin_panel_keyboard())
        for chunk in chunks[1:]:
            await message.bot.send_message(message.from_user.id, chunk)

    @router.message(AdminStates.grant_proxies)
    async def admin_state_grant_proxies(message: Message, state: FSMContext) -> None:
        if await handle_blocked_message(db, message):
            return
        if not await ensure_admin_message_access(message, state):
            return
        payload = extract_text_payload(message)
        if payload is None:
            await message.answer("Неверный формат.")
            return
        parts = payload.split()
        if len(parts) < 2:
            await message.answer("Формат: <tg_user_id> <кол-во> [дней]")
            return

        tg_user_id = parse_int(parts[0])
        devices_count = parse_int(parts[1])
        days = parse_int(parts[2]) if len(parts) > 2 else 30
        if tg_user_id is None or devices_count is None or days is None:
            await message.answer("tg_user_id, кол-во и дни должны быть числами.")
            return
        if devices_count not in (1, 5, 15):
            await message.answer("Поддерживаются только 1, 5 или 15 прокси.")
            return
        if days < 1 or days > 3650:
            await message.answer("Дни должны быть в диапазоне 1..3650.")
            return

        user_row = await db.get_user_by_tg_user_id(tg_user_id)
        if user_row is None:
            await message.answer("Пользователь не найден.")
            return
        profile = normalize_user_profile(user_row)

        active_count = len(await db.get_active_links_for_user(profile.id))
        if active_count + devices_count > MAX_ACTIVE_PROXIES_PER_USER:
            await message.answer(
                build_proxy_limit_text(active_count=active_count, requested_count=devices_count),
            )
            return

        plans = await db.get_plans()
        plan = next((item for item in plans if item.devices_count == devices_count), None)
        if plan is None:
            await message.answer("Не найден подходящий тариф для выбранного количества.")
            return

        payment_id = await db.create_payment(
            user_id=profile.id,
            plan_code=plan.code,
            amount_rub=0,
        )
        expires_at = int((datetime.now(tz=timezone.utc) + timedelta(days=days)).timestamp())
        activated = await db.activate_payment_and_create_subscription_from_pool(
            payment_id=payment_id,
            user_id=profile.id,
            plan_code=plan.code,
            expires_at=expires_at,
            devices_count=devices_count,
            proxy_public_host=proxy_public_host,
        )
        if activated is None:
            free_count = await db.count_free_pool()
            await message.answer(
                f"Не удалось начислить прокси. Свободно в пуле: {free_count}.",
            )
            return
        subscription_id, created_proxies = activated

        proxies: list[dict[str, int | str | None]] = []
        for index, proxy in enumerate(created_proxies, start=1):
            tg_link = telegram_socks_link(
                proxy_public_host,
                int(proxy["port"]),
                str(proxy["username"]),
                str(proxy["password"]),
            )
            proxies.append(
                {
                    "index": index,
                    "proxy_id": int(proxy["proxy_id"]),
                    "tg_link": tg_link,
                    "subscription_id": subscription_id,
                    "device_number": int(proxy["device_number"]),
                }
            )

        await send_proxy_sequence(
            db=db,
            bot=message.bot,
            bot_chat_id=profile.tg_user_id,
            user_id=profile.id,
            tg_user_id=profile.tg_user_id,
            user_proxy_label=user_proxy_label_from_profile(profile),
            proxies=proxies,
            delivery_source="purchase",
        )

        await state.clear()
        await message.answer(
            f"Начислено {devices_count} прокси пользователю {profile.tg_user_id}.",
            reply_markup=admin_panel_keyboard(),
        )

    @router.message(AdminStates.remove_proxies)
    async def admin_state_remove_proxies(message: Message, state: FSMContext) -> None:
        if await handle_blocked_message(db, message):
            return
        if not await ensure_admin_message_access(message, state):
            return
        payload = extract_text_payload(message)
        if payload is None:
            await message.answer("Неверный формат.")
            return
        parts = payload.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Формат: <tg_user_id> <proxy_id|all>")
            return
        tg_user_id = parse_int(parts[0])
        if tg_user_id is None:
            await message.answer("tg_user_id должен быть числом.")
            return
        user_row = await db.get_user_by_tg_user_id(tg_user_id)
        if user_row is None:
            await message.answer("Пользователь не найден.")
            return
        profile = normalize_user_profile(user_row)
        token = parts[1].strip().lower()

        removed_count = 0
        if token == "all":
            removed_count = await db.revoke_all_active_links_for_user(profile.id)
        else:
            proxy_id = parse_int(token)
            if proxy_id is None:
                await message.answer("proxy_id должен быть числом или all.")
                return
            removed = await db.revoke_proxy_link_for_user(profile.id, proxy_id)
            removed_count = 1 if removed else 0

        if removed_count > 0:
            try:
                await message.bot.send_message(
                    profile.tg_user_id,
                    "Часть ваших прокси была деактивирована администратором.",
                )
            except (TelegramBadRequest, TelegramForbiddenError):
                pass

        await state.clear()
        await message.answer(
            f"Удалено прокси: {removed_count}.",
            reply_markup=admin_panel_keyboard(),
        )

    @router.callback_query(F.data == "menu:home_clear")
    async def cb_home_clear(callback: CallbackQuery) -> None:
        if await handle_blocked_callback(db, callback):
            return
        user_id = await ensure_user(
            db,
            callback.from_user,
            bot=callback.bot,
            admin_tg_ids=admin_ids,
        )
        await cleanup_proxy_output_messages(db=db, bot=callback.bot, user_id=user_id)
        if callback.message is not None:
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
        await callback.bot.send_message(
            callback.from_user.id,
            build_welcome_text(),
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()

    @router.callback_query(F.data == "menu:plans")
    async def cb_plans(callback: CallbackQuery) -> None:
        if await handle_blocked_callback(db, callback):
            return
        user_id = await ensure_user(
            db,
            callback.from_user,
            bot=callback.bot,
            admin_tg_ids=admin_ids,
        )
        if user_id <= 0:
            await callback.answer("Ошибка профиля", show_alert=True)
            return
        plans = await db.get_plans()
        await edit_or_send(
            callback,
            text=build_plans_text(plans),
            reply_markup=plans_keyboard(plans),
            parse_mode="HTML",
        )
        await callback.answer()

    @router.callback_query(F.data == "menu:links")
    async def cb_links(callback: CallbackQuery) -> None:
        if await handle_blocked_callback(db, callback):
            return
        user_id = await ensure_user(
            db,
            callback.from_user,
            bot=callback.bot,
            admin_tg_ids=admin_ids,
        )
        await send_links_list(
            db=db,
            bot_chat_id=callback.from_user.id,
            bot=callback.bot,
            user_id=user_id,
            tg_user_id=callback.from_user.id,
            user_proxy_label=profile_label(callback.from_user),
            source_message=callback.message,
        )
        await callback.answer()

    @router.callback_query(F.data == "menu:status")
    async def cb_status(callback: CallbackQuery) -> None:
        if await handle_blocked_callback(db, callback):
            return
        user_id = await ensure_user(
            db,
            callback.from_user,
            bot=callback.bot,
            admin_tg_ids=admin_ids,
        )
        await send_status(
            db=db,
            bot_chat_id=callback.from_user.id,
            bot=callback.bot,
            user_id=user_id,
            edit_message=callback.message,
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("buy:"))
    async def cb_buy(callback: CallbackQuery) -> None:
        if await handle_blocked_callback(db, callback):
            return
        plan_code = callback.data.split(":", maxsplit=1)[1]
        user_id = await ensure_user(
            db,
            callback.from_user,
            bot=callback.bot,
            admin_tg_ids=admin_ids,
        )
        plan = await db.get_plan(plan_code)
        if plan is None:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        active_count = len(await db.get_active_links_for_user(user_id))
        if active_count + plan.devices_count > MAX_ACTIVE_PROXIES_PER_USER:
            await edit_or_send(
                callback,
                text=build_proxy_limit_text(active_count=active_count, requested_count=plan.devices_count),
                reply_markup=main_menu_keyboard(),
                parse_mode=None,
            )
            await callback.answer("Превышен лимит прокси")
            return

        payment_id = await db.create_payment(user_id=user_id, plan_code=plan.code, amount_rub=plan.price_rub)
        await edit_or_send(
            callback,
            text=(
                f"{tg_emoji(EMOJI_GEM, '💎')} <b>Заявка на оплату создана</b>\n\n"
                f"Тариф: <b>{plan.title}</b>\n"
                f"Сумма: <b>{plan.price_rub}₽</b>\n"
                f"ID платежа: <code>{payment_id}</code>\n\n"
                "Нажмите «Подтвердить оплату», чтобы активировать тариф."
            ),
            reply_markup=payment_keyboard(payment_id),
            parse_mode="HTML",
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("cancelpay:"))
    async def cb_cancel_payment(callback: CallbackQuery) -> None:
        if await handle_blocked_callback(db, callback):
            return
        payment_id_raw = callback.data.split(":", maxsplit=1)[1]
        if not payment_id_raw.isdigit():
            await callback.answer("Некорректный платеж", show_alert=True)
            return

        user_id = await ensure_user(
            db,
            callback.from_user,
            bot=callback.bot,
            admin_tg_ids=admin_ids,
        )
        cancelled = await db.cancel_pending_payment(int(payment_id_raw), user_id)
        if cancelled:
            await edit_or_send(
                callback,
                text="Платеж отменен.",
                reply_markup=main_menu_keyboard(),
                parse_mode="HTML",
            )
            await callback.answer("Отменено")
        else:
            await callback.answer("Платеж уже обработан", show_alert=True)

    @router.callback_query(F.data.startswith("pay:"))
    async def cb_pay(callback: CallbackQuery) -> None:
        if await handle_blocked_callback(db, callback):
            return
        payment_id_raw = callback.data.split(":", maxsplit=1)[1]
        if not payment_id_raw.isdigit():
            await callback.answer("Некорректный платеж", show_alert=True)
            return

        payment_id = int(payment_id_raw)
        user_id = await ensure_user(
            db,
            callback.from_user,
            bot=callback.bot,
            admin_tg_ids=admin_ids,
        )
        payment = await db.get_payment_for_user(payment_id=payment_id, user_id=user_id)
        if payment is None:
            await callback.answer("Платеж не найден", show_alert=True)
            return

        if payment["status"] != "pending":
            await callback.answer("Платеж уже обработан", show_alert=True)
            return

        plan = await db.get_plan(payment["plan_code"])
        if plan is None:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        active_count = len(await db.get_active_links_for_user(user_id))
        if active_count + plan.devices_count > MAX_ACTIVE_PROXIES_PER_USER:
            await edit_or_send(
                callback,
                text=build_proxy_limit_text(active_count=active_count, requested_count=plan.devices_count),
                reply_markup=main_menu_keyboard(),
                parse_mode=None,
            )
            await callback.answer("Превышен лимит прокси")
            return

        expires_at = int((datetime.now(tz=timezone.utc) + timedelta(days=plan.duration_days)).timestamp())
        activated = await db.activate_payment_and_create_subscription_from_pool(
            payment_id=payment_id,
            user_id=user_id,
            plan_code=plan.code,
            expires_at=expires_at,
            devices_count=plan.devices_count,
            proxy_public_host=proxy_public_host,
        )
        if activated is None:
            free_count = await db.count_free_pool()
            await edit_or_send(
                callback,
                text=(
                    "Сейчас в пуле недостаточно свободных прокси для этого тарифа.\n"
                    f"Свободно прямо сейчас: {free_count}.\n"
                    "Выберите меньший тариф или попробуйте позже."
                ),
                reply_markup=main_menu_keyboard(),
                parse_mode=None,
            )
            await callback.answer("Недостаточно свободных прокси")
            return
        subscription_id, created_proxies = activated

        user_proxy_label = profile_label(callback.from_user)
        proxies: list[dict[str, int | str | None]] = []
        for index, proxy in enumerate(created_proxies, start=1):
            tg_link = telegram_socks_link(
                proxy_public_host,
                int(proxy["port"]),
                str(proxy["username"]),
                str(proxy["password"]),
            )
            proxy_id = int(proxy["proxy_id"])
            proxies.append(
                {
                    "index": index,
                    "proxy_id": proxy_id,
                    "tg_link": tg_link,
                    "subscription_id": subscription_id,
                    "device_number": int(proxy["device_number"]),
                }
            )

        await send_proxy_sequence(
            db=db,
            bot=callback.bot,
            bot_chat_id=callback.from_user.id,
            user_id=user_id,
            tg_user_id=callback.from_user.id,
            user_proxy_label=user_proxy_label,
            proxies=proxies,
            delivery_source="purchase",
            source_message=callback.message,
        )
        await callback.answer("Готово")

    return router
