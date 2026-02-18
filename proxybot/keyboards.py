from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .database import Plan


EMOJI_SHIELD = "5407025283456835913"
EMOJI_GEM = "5330319637156479518"
EMOJI_DEV = "5418063924933173277"
EMOJI_BOX = "5298975240708187753"
EMOJI_GLASSES = "5474385437403395055"


def _button(
    *,
    text: str,
    callback_data: str,
    style: str | None = None,
    icon_custom_emoji_id: str | None = None,
) -> InlineKeyboardButton:
    kwargs: dict[str, str] = {}
    if style:
        kwargs["style"] = style
    if icon_custom_emoji_id:
        kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id
    return InlineKeyboardButton(text=text, callback_data=callback_data, **kwargs)


def _device_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "устройство"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "устройства"
    return "устройств"


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    text="Тарифы",
                    callback_data="menu:plans",
                    style="primary",
                    icon_custom_emoji_id=EMOJI_SHIELD,
                ),
                _button(
                    text="Мои прокси",
                    callback_data="menu:links",
                    style="success",
                    icon_custom_emoji_id=EMOJI_GEM,
                ),
            ],
            [
                _button(
                    text="Статус подписки",
                    callback_data="menu:status",
                    icon_custom_emoji_id=EMOJI_BOX,
                )
            ],
        ]
    )


def plans_keyboard(plans: list[Plan]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for plan in plans:
        button_style = "primary"
        button_icon = EMOJI_DEV
        if plan.devices_count == 5:
            button_style = "success"
            button_icon = EMOJI_GEM
        if plan.devices_count >= 15:
            button_style = "primary"
            button_icon = EMOJI_BOX
        rows.append(
            [
                _button(
                    text=f"{plan.devices_count} {_device_word(plan.devices_count)} • {plan.price_rub}₽/мес",
                    callback_data=f"buy:{plan.code}",
                    style=button_style,
                    icon_custom_emoji_id=button_icon,
                )
            ]
        )
    rows.append(
        [
            _button(
                text="Открыть мои прокси",
                callback_data="menu:links",
                icon_custom_emoji_id=EMOJI_GLASSES,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    text="Подтвердить оплату",
                    callback_data=f"pay:{payment_id}",
                    style="success",
                    icon_custom_emoji_id=EMOJI_GEM,
                )
            ],
            [
                _button(
                    text="Отменить",
                    callback_data=f"cancelpay:{payment_id}",
                    style="danger",
                    icon_custom_emoji_id=EMOJI_GLASSES,
                )
            ],
        ]
    )
