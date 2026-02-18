from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .database import Plan


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Тарифы и покупка", callback_data="menu:plans")],
            [InlineKeyboardButton(text="Мои ссылки", callback_data="menu:links")],
            [InlineKeyboardButton(text="Мой статус", callback_data="menu:status")],
        ]
    )


def plans_keyboard(plans: list[Plan]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.button(
            text=f"{plan.devices_count} устр. • {plan.price_rub}₽/мес",
            callback_data=f"buy:{plan.code}",
        )
    builder.button(text="Мои ссылки", callback_data="menu:links")
    builder.adjust(1)
    return builder.as_markup()


def payment_stub_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Оплатил (заглушка)", callback_data=f"pay:{payment_id}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancelpay:{payment_id}")],
        ]
    )

