from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from urllib.parse import unquote, urlencode, urlparse

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User as TelegramUser

from .database import Database, Plan
from .keyboards import (
    EMOJI_BOX,
    EMOJI_DEV,
    EMOJI_GEM,
    EMOJI_SHIELD,
    back_to_menu_keyboard,
    main_menu_keyboard,
    payment_keyboard,
    plans_keyboard,
)

logger = logging.getLogger(__name__)

PROXY_FOOTER = "Made with @proxy_sdiki1_bot"


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


async def ensure_user(db: Database, telegram_user: TelegramUser) -> int:
    return await db.upsert_user(
        tg_user_id=telegram_user.id,
        username=telegram_user.username,
        first_name=telegram_user.first_name,
        last_name=telegram_user.last_name,
    )


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
    edit_message: Message | None = None,
) -> None:
    links = await db.get_active_links_for_user(user_id)
    if not links:
        text = (
            f"{tg_emoji(EMOJI_DEV, '📱')} У вас пока нет активных прокси.\n"
            "Выберите тариф через /buy или кнопку «Тарифы»."
        )
        if edit_message is not None:
            await edit_message.edit_text(text, reply_markup=main_menu_keyboard())
        else:
            await bot.send_message(bot_chat_id, text, reply_markup=main_menu_keyboard())
        return

    blocks: list[str] = []
    deliveries: list[dict[str, int | str | None]] = []
    for index, row in enumerate(links, start=1):
        parsed = parse_socks5_url(str(row["link"]))
        if parsed is None:
            continue
        host, port, username, password = parsed
        tg_link = telegram_socks_link(host, port, username, password)
        proxy_id = int(row["id"])

        blocks.append(
            build_proxy_block(
                proxy_index=index,
                user_proxy_label=user_proxy_label,
                proxy_id=proxy_id,
                tg_link=tg_link,
            )
        )
        deliveries.append(
            {
                "proxy_id": proxy_id,
                "subscription_id": int(row["subscription_id"]),
                "device_number": int(row["device_number"]),
                "tg_link": tg_link,
            }
        )

    if not blocks:
        text = "Не удалось подготовить ссылки для Telegram из сохраненных прокси."
        if edit_message is not None:
            await edit_message.edit_text(text, reply_markup=main_menu_keyboard())
        else:
            await bot.send_message(bot_chat_id, text, reply_markup=main_menu_keyboard())
        return

    text = "💎 Ваши активные прокси\n\n" + "\n\n".join(blocks)
    if edit_message is not None:
        await edit_message.edit_text(text, parse_mode=None, reply_markup=back_to_menu_keyboard())
    else:
        await bot.send_message(bot_chat_id, text, parse_mode=None, reply_markup=back_to_menu_keyboard())

    for item in deliveries:
        await log_proxy_delivery(
            db=db,
            proxy_id=int(item["proxy_id"]),
            user_id=user_id,
            tg_user_id=tg_user_id,
            user_proxy_label=user_proxy_label,
            subscription_id=int(item["subscription_id"]),
            device_number=int(item["device_number"]),
            delivery_source="my_links",
            tg_link=str(item["tg_link"]),
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


def create_router(db: Database, proxy_public_host: str) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        if message.from_user is None:
            return
        await ensure_user(db, message.from_user)
        await message.answer(build_welcome_text(), reply_markup=main_menu_keyboard())

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(build_help_text())

    @router.message(Command("plans"))
    @router.message(Command("buy"))
    async def cmd_plans(message: Message) -> None:
        if message.from_user is None:
            return
        await ensure_user(db, message.from_user)
        plans = await db.get_plans()
        await message.answer(build_plans_text(plans), reply_markup=plans_keyboard(plans))

    @router.message(Command("my_links"))
    async def cmd_links(message: Message) -> None:
        if message.from_user is None:
            return
        user_id = await ensure_user(db, message.from_user)
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
        if message.from_user is None:
            return
        user_id = await ensure_user(db, message.from_user)
        await send_status(db=db, bot_chat_id=message.chat.id, bot=message.bot, user_id=user_id)

    @router.callback_query(F.data == "menu:home_clear")
    async def cb_home_clear(callback: CallbackQuery) -> None:
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
        user_id = await ensure_user(db, callback.from_user)
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
        user_id = await ensure_user(db, callback.from_user)
        await send_links_list(
            db=db,
            bot_chat_id=callback.from_user.id,
            bot=callback.bot,
            user_id=user_id,
            tg_user_id=callback.from_user.id,
            user_proxy_label=profile_label(callback.from_user),
            edit_message=callback.message,
        )
        await callback.answer()

    @router.callback_query(F.data == "menu:status")
    async def cb_status(callback: CallbackQuery) -> None:
        user_id = await ensure_user(db, callback.from_user)
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
        plan_code = callback.data.split(":", maxsplit=1)[1]
        user_id = await ensure_user(db, callback.from_user)
        plan = await db.get_plan(plan_code)
        if plan is None:
            await callback.answer("Тариф не найден", show_alert=True)
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
        payment_id_raw = callback.data.split(":", maxsplit=1)[1]
        if not payment_id_raw.isdigit():
            await callback.answer("Некорректный платеж", show_alert=True)
            return

        user_id = await ensure_user(db, callback.from_user)
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
        payment_id_raw = callback.data.split(":", maxsplit=1)[1]
        if not payment_id_raw.isdigit():
            await callback.answer("Некорректный платеж", show_alert=True)
            return

        payment_id = int(payment_id_raw)
        user_id = await ensure_user(db, callback.from_user)
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
                    "Не удалось активировать тариф.\n"
                    f"Свободных SOCKS5-прокси в пуле: {free_count}.\n"
                    "Проверьте сервис прокси в docker-compose."
                ),
                reply_markup=main_menu_keyboard(),
                parse_mode=None,
            )
            await callback.answer("Нет свободных прокси", show_alert=True)
            return
        subscription_id, created_proxies = activated

        user_proxy_label = profile_label(callback.from_user)
        blocks: list[str] = []
        for index, proxy in enumerate(created_proxies, start=1):
            tg_link = telegram_socks_link(
                proxy_public_host,
                int(proxy["port"]),
                str(proxy["username"]),
                str(proxy["password"]),
            )
            proxy_id = int(proxy["proxy_id"])
            blocks.append(
                build_proxy_block(
                    proxy_index=index,
                    user_proxy_label=user_proxy_label,
                    proxy_id=proxy_id,
                    tg_link=tg_link,
                )
            )
            await log_proxy_delivery(
                db=db,
                proxy_id=proxy_id,
                user_id=user_id,
                tg_user_id=callback.from_user.id,
                user_proxy_label=user_proxy_label,
                subscription_id=subscription_id,
                device_number=int(proxy["device_number"]),
                delivery_source="purchase",
                tg_link=tg_link,
            )

        text = (
            f"Оплата подтверждена.\nПодписка #{subscription_id} активна до {format_ts(expires_at)}.\n\n"
            + "\n\n".join(blocks)
        )

        await edit_or_send(
            callback,
            text=text,
            reply_markup=back_to_menu_keyboard(),
            parse_mode=None,
        )
        await callback.answer("Готово")

    return router
