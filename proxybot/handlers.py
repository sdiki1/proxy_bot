from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlencode, urlparse

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, User as TelegramUser

from .database import Database, Plan
from .keyboards import main_menu_keyboard, payment_stub_keyboard, plans_keyboard


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


def build_plans_text(plans: list[Plan]) -> str:
    lines = [
        "Тарифы:",
        "",
    ]
    for plan in plans:
        lines.append(f"• {plan.title}: <b>{plan.price_rub}₽ / мес</b>")
    lines.extend(
        [
            "",
            "Срок действия каждой покупки: <b>30 дней</b>.",
            "Оплата сейчас работает в режиме <b>заглушки</b>.",
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


async def send_links_list(
    *,
    db: Database,
    bot_chat_id: int,
    bot,
    user_id: int,
    user_proxy_label: str,
) -> None:
    links = await db.get_active_links_for_user(user_id)
    if not links:
        await bot.send_message(
            bot_chat_id,
            "У вас пока нет активных прокси-ссылок.\nВыберите тариф через /buy или кнопку «Тарифы и покупка».",
            reply_markup=main_menu_keyboard(),
        )
        return

    await bot.send_message(bot_chat_id, "Ваши активные прокси:")
    sent_count = 0
    for index, row in enumerate(links, start=1):
        parsed = parse_socks5_url(str(row["link"]))
        if parsed is None:
            continue
        host, port, username, password = parsed
        tg_link = telegram_socks_link(host, port, username, password)
        text = f"PROXY-{index}-{user_proxy_label}\n\n{tg_link}"
        await bot.send_message(bot_chat_id, text, parse_mode=None)
        sent_count += 1
    if sent_count == 0:
        await bot.send_message(bot_chat_id, "Не удалось подготовить ссылки для Telegram из сохраненных прокси.")


async def send_status(
    *,
    db: Database,
    bot_chat_id: int,
    bot,
    user_id: int,
) -> None:
    subscriptions = await db.get_active_subscriptions_for_user(user_id)
    if not subscriptions:
        await bot.send_message(
            bot_chat_id,
            "У вас нет активной подписки.\nОформите тариф через /buy.",
            reply_markup=main_menu_keyboard(),
        )
        return

    lines = ["Активные подписки:", ""]
    for sub in subscriptions:
        expires_at = int(sub["expires_at"])
        lines.append(
            f"• #{sub['id']} — {sub['plan_title']} — до {format_ts(expires_at)} "
            f"(осталось {format_remaining(expires_at)})"
        )
    await bot.send_message(bot_chat_id, "\n".join(lines))


def create_router(db: Database, proxy_public_host: str) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        if message.from_user is None:
            return
        await ensure_user(db, message.from_user)
        await message.answer(
            (
                "ProxyBot выдает персональные SOCKS5-прокси, привязанные к вашему Telegram-профилю.\n"
                "Каждая покупка действует 30 дней.\n"
                "Оплата сейчас работает как заглушка: подтверждение вручную кнопкой."
            ),
            reply_markup=main_menu_keyboard(),
        )

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(
            "/start — главное меню\n"
            "/plans — показать тарифы\n"
            "/buy — выбрать тариф\n"
            "/my_links — активные ссылки\n"
            "/status — состояние подписки"
        )

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
            user_proxy_label=profile_label(message.from_user),
        )

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if message.from_user is None:
            return
        user_id = await ensure_user(db, message.from_user)
        await send_status(db=db, bot_chat_id=message.chat.id, bot=message.bot, user_id=user_id)

    @router.callback_query(F.data == "menu:plans")
    async def cb_plans(callback: CallbackQuery) -> None:
        user_id = await ensure_user(db, callback.from_user)
        if user_id <= 0:
            await callback.answer("Ошибка профиля", show_alert=True)
            return
        plans = await db.get_plans()
        await callback.bot.send_message(
            callback.from_user.id,
            build_plans_text(plans),
            reply_markup=plans_keyboard(plans),
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
            user_proxy_label=profile_label(callback.from_user),
        )
        await callback.answer()

    @router.callback_query(F.data == "menu:status")
    async def cb_status(callback: CallbackQuery) -> None:
        user_id = await ensure_user(db, callback.from_user)
        await send_status(db=db, bot_chat_id=callback.from_user.id, bot=callback.bot, user_id=user_id)
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
        await callback.bot.send_message(
            callback.from_user.id,
            (
                "Заявка на оплату создана.\n\n"
                f"Тариф: <b>{plan.title}</b>\n"
                f"Сумма: <b>{plan.price_rub}₽</b>\n"
                f"ID платежа: <code>{payment_id}</code>\n\n"
                "Сейчас это заглушка: нажмите «Оплатил», чтобы симулировать успешный платеж."
            ),
            reply_markup=payment_stub_keyboard(payment_id),
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
            await callback.bot.send_message(callback.from_user.id, "Платеж отменен.")
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
            await callback.bot.send_message(
                callback.from_user.id,
                (
                    "Не удалось активировать тариф.\n"
                    f"Свободных SOCKS5-прокси в пуле: <b>{free_count}</b>.\n"
                    "Проверьте сервис прокси в docker-compose."
                ),
            )
            await callback.answer("Нет свободных прокси", show_alert=True)
            return
        subscription_id, created_proxies = activated

        await callback.bot.send_message(
            callback.from_user.id,
            (
                "Оплата подтверждена (заглушка).\n"
                f"Подписка #{subscription_id} активна до {format_ts(expires_at)}."
            ),
        )

        user_proxy_label = profile_label(callback.from_user)
        for index, proxy in enumerate(created_proxies, start=1):
            tg_link = telegram_socks_link(
                proxy_public_host,
                int(proxy["port"]),
                str(proxy["username"]),
                str(proxy["password"]),
            )
            text = f"PROXY-{index}-{user_proxy_label}\n\n{tg_link}"
            await callback.bot.send_message(callback.from_user.id, text, parse_mode=None)

        await callback.bot.send_message(
            callback.from_user.id,
            "Ссылки привязаны к вашему Telegram-профилю и действуют 30 дней.",
        )
        await callback.answer("Готово")

    return router
