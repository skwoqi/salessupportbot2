from __future__ import annotations

import json
import os
import random
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime
from typing import Any

import vk_api
from dotenv import load_dotenv
from openai import OpenAI
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.longpoll import VkEventType, VkLongPoll

load_dotenv()

VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN", "")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL", "").strip().rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
PORTFOLIO_URL = os.getenv("PORTFOLIO_URL", "https://interinc.ru/portfolio/")
COMPANY_NAME = os.getenv("COMPANY_NAME", "Interinc")
DB_PATH = os.getenv("DB_PATH", "/app/data/bot.db")

MAIN_MENU = "MAIN_MENU"
CATALOG_STATE = "CATALOG"
CATALOG_CATEGORY = "CATALOG_CATEGORY"
CATALOG_CONFIRM_ADD = "CATALOG_CONFIRM_ADD"
ORDER_REVIEW = "ORDER_REVIEW"
ORDER_REVIEW_REMOVE = "ORDER_REVIEW_REMOVE"
ORDER_NAME = "ORDER_NAME"
ORDER_PHONE = "ORDER_PHONE"
ORDER_EMAIL = "ORDER_EMAIL"
ORDER_COMMENT = "ORDER_COMMENT"
AI_WAITING_BRIEF = "AI_WAITING_BRIEF"
AI_POST_RESULT = "AI_POST_RESULT"
SUPPORT_DESCRIPTION = "SUPPORT_DESCRIPTION"
SUPPORT_NAME = "SUPPORT_NAME"
SUPPORT_PHONE = "SUPPORT_PHONE"
SUPPORT_EMAIL = "SUPPORT_EMAIL"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_ALLOWED_RE = re.compile(r"^[\d\+\-\(\)\s]{7,25}$")

CATALOG = {
    "Продвижение": {
        "Поисковое продвижение": (
            "Помогаем сайту стабильно расти в поиске Яндекс и Google: устраняем "
            "технические ошибки, усиливаем структуру и контент, чтобы вы получали "
            "больше целевых обращений из органики."
        ),
        "Продвижение в соцсетях": (
            "Строим системное присутствие бренда в соцсетях: стратегия, контент, "
            "визуал и коммуникация с аудиторией. Цель - узнаваемость, доверие и "
            "заявки из социальных каналов."
        ),
        "Контекстная реклама": (
            "Запускаем и ведем рекламные кампании в Яндекс Директ и Google Ads: "
            "от настройки до регулярной оптимизации, чтобы снижать стоимость "
            "заявки и повышать отдачу от бюджета."
        ),
    },
    "Битрикс24": {
        "Базовый": (
            "Быстрый старт с Битрикс24: подключаем ключевые инструменты, "
            "настраиваем базовые процессы и помогаем команде начать работать "
            "в единой CRM-системе."
        ),
        "Стандартный": (
            "Полноценное внедрение Битрикс24 под задачи компании: настройка CRM, "
            "воронок и автоматизаций, интеграции и сопровождение запуска, чтобы "
            "команда работала без хаоса."
        ),
        "Максимальный": (
            "Глубокая автоматизация и оптимизация бизнес-процессов: аудит, "
            "сложные доработки, интеграции и управленческие сценарии для роста "
            "эффективности продаж и сервиса."
        ),
    },
    "Веб-разработка": {
        "Интернет-магазин": (
            "Создаем интернет-магазин на 1С-Битрикс с удобным управлением, "
            "продуманной структурой и интеграциями (CRM, платежи, учет), "
            "чтобы сайт был готов к масштабированию продаж."
        ),
        "Корпоративный сайт": (
            "Разрабатываем современный корпоративный сайт на 1С-Битрикс: "
            "адаптивный, понятный для клиентов и готовый к базовому SEO-"
            "продвижению и дальнейшему развитию."
        ),
        "Landing page": (
            "Делаем посадочную страницу под конкретный продукт или услугу: "
            "четкий оффер, логичный сценарий и сильные акценты, чтобы вести "
            "посетителя к заявке."
        ),
    },
    "Дизайн": {
        "Дизайн/редизайн сайта": (
            "Проектируем современный и функциональный дизайн, который "
            "поддерживает цели бизнеса: удобная структура, понятные интерфейсы "
            "и визуал, который помогает конверсии."
        ),
        "Разработка логотипа": (
            "Создаем логотип как основу визуальной идентичности бренда: "
            "узнаваемый, уместный в вашей нише и удобный для использования "
            "во всех каналах."
        ),
        "Разработка фирменного стиля": (
            "Формируем целостный визуальный стиль компании: от базовых элементов "
            "до системного применения в digital и офлайн, чтобы бренд выглядел "
            "едино и профессионально."
        ),
    },
}

ALL_SERVICES = {service for services in CATALOG.values() for service in services}


def ensure_db() -> None:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                request_type TEXT NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_context(user_id: int) -> tuple[str, dict[str, Any]]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT state, payload_json FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        set_context(user_id, MAIN_MENU, {})
        return MAIN_MENU, {}
    return row[0], json.loads(row[1] or "{}")


def set_context(user_id: int, state: str, payload: dict[str, Any]) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, state, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                state = excluded.state,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (user_id, state, json.dumps(payload, ensure_ascii=False), datetime.utcnow().isoformat()),
        )
        conn.commit()


def create_request(user_id: int, request_type: str, details: dict[str, Any], status: str = "NEW") -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            """
            INSERT INTO requests (user_id, request_type, status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                request_type,
                status,
                json.dumps(details, ensure_ascii=False),
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def set_request_status(request_id: int, status: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("UPDATE requests SET status = ? WHERE id = ?", (status, request_id))
        conn.commit()


def keyboard_main() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Каталог", color=VkKeyboardColor.PRIMARY)
    kb.add_button("Портфолио", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Помощь с выбором", color=VkKeyboardColor.POSITIVE)
    kb.add_button("Консультация/техподдержка", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def keyboard_catalog() -> str:
    kb = VkKeyboard(one_time=False)
    for index, category in enumerate(CATALOG, 1):
        kb.add_button(category, color=VkKeyboardColor.PRIMARY)
        if index % 2 == 0:
            kb.add_line()
    kb.add_button("Главное меню", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def keyboard_category(category: str) -> str:
    kb = VkKeyboard(one_time=False)
    for index, service in enumerate(CATALOG[category], 1):
        kb.add_button(service, color=VkKeyboardColor.PRIMARY)
        if index % 2 == 0:
            kb.add_line()
    kb.add_line()
    kb.add_button("Оформить заявку", color=VkKeyboardColor.POSITIVE)
    kb.add_button("Назад в каталог", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Главное меню", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def keyboard_confirm_add() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Добавить в заявку", color=VkKeyboardColor.POSITIVE)
    kb.add_button("Вернуться назад", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Главное меню", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def keyboard_review() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Подтвердить заявку", color=VkKeyboardColor.POSITIVE)
    kb.add_button("Удалить услугу", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("Назад в каталог", color=VkKeyboardColor.SECONDARY)
    kb.add_button("Главное меню", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def keyboard_ai_result() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Оставить заявку", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("Перейти в каталог", color=VkKeyboardColor.PRIMARY)
    kb.add_button("Главное меню", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def keyboard_portfolio() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Перейти в каталог", color=VkKeyboardColor.PRIMARY)
    kb.add_button("Главное меню", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def normalize_phone(raw: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", raw.strip())
    if cleaned.startswith("8") and len(cleaned) == 11:
        return "+7" + cleaned[1:]
    if cleaned.startswith("7") and len(cleaned) == 11:
        return "+" + cleaned
    return cleaned


def is_valid_phone(raw: str) -> bool:
    if not PHONE_ALLOWED_RE.match(raw.strip()):
        return False
    digits = re.sub(r"\D", "", raw)
    return 10 <= len(digits) <= 15


def bitrix_post(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not BITRIX_WEBHOOK_URL:
        return {"error": "BITRIX_WEBHOOK_URL is empty"}
    url = f"{BITRIX_WEBHOOK_URL}/{method}.json"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = ""
    for attempt in range(1, 4):
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return {"error": f"http_{exc.code}", "error_description": raw}
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            last_error = str(exc)
            if attempt < 3:
                time.sleep(1.5 * attempt)
    return {"error": f"request_failed_after_3_attempts: {last_error}"}


def create_bitrix_lead(title: str, fields: dict[str, Any], comments: str) -> tuple[bool, str]:
    payload = {"fields": {"TITLE": title, "COMMENTS": comments, **fields}}
    result = bitrix_post("crm.lead.add", payload)
    if result.get("error"):
        return False, f"{result.get('error')}: {result.get('error_description', '')}".strip(": ")
    return True, str(result.get("result", ""))


def suggest_with_openai(user_brief: str) -> dict[str, object]:
    if not OPENAI_API_KEY:
        return fallback_advice()
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты консультант digital-агентства. Рекомендуй только услуги из списка. "
                        "Не обещай цены, сроки, гарантии и конкретное количество заявок. "
                        "Верни JSON: recommended_services (list[str], max 3), reason, message_to_client."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Услуги: {sorted(ALL_SERVICES)}\nЗапрос клиента: {user_brief}",
                },
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        rec = parsed.get("recommended_services", [])
        parsed["recommended_services"] = [service for service in rec if service in ALL_SERVICES][:3]
        parsed["reason"] = str(parsed.get("reason", "")).strip()
        parsed["message_to_client"] = str(parsed.get("message_to_client", "")).strip()
        return parsed
    except Exception as exc:
        print(f"[OpenAI] {type(exc).__name__}: {exc}")
        return fallback_advice()


def fallback_advice() -> dict[str, object]:
    return {
        "recommended_services": ["Консультация специалиста"],
        "reason": "Автоматический подбор временно недоступен.",
        "message_to_client": "Оставьте заявку, и специалист поможет подобрать подходящее решение.",
    }


class Bot:
    def __init__(self) -> None:
        if not VK_GROUP_TOKEN:
            raise RuntimeError("VK_GROUP_TOKEN is empty")
        ensure_db()
        self.vk_session = vk_api.VkApi(token=VK_GROUP_TOKEN)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkLongPoll(self.vk_session)

    def send(self, user_id: int, message: str, keyboard: str | None = None) -> None:
        self.vk.messages.send(
            user_id=user_id,
            message=message,
            random_id=random.randint(1, 2_000_000_000),
            keyboard=keyboard,
        )

    def show_main(self, user_id: int) -> None:
        set_context(user_id, MAIN_MENU, {})
        self.send(
            user_id,
            f"Здравствуйте! Я помощник {COMPANY_NAME}. Помогу выбрать услугу, показать портфолио и оформить заявку.",
            keyboard_main(),
        )

    def start(self) -> None:
        print("VK bot started.")
        for event in self.longpoll.listen():
            if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                self.handle(event.user_id, (event.text or "").strip())

    def handle(self, user_id: int, text: str) -> None:
        lower = text.lower()
        if lower in {"/start", "start", "начать", "привет", "/menu"}:
            self.show_main(user_id)
            return
        if lower in {"/cancel", "отмена"}:
            self.send(user_id, "Текущий сценарий отменен.", keyboard_main())
            self.show_main(user_id)
            return

        state, payload = get_context(user_id)

        if text == "Главное меню":
            self.show_main(user_id)
            return
        if text == "Каталог":
            selected = payload.get("selected_services", [])
            set_context(user_id, CATALOG_STATE, {"selected_services": selected})
            self.send(user_id, "Выберите категорию услуг:", keyboard_catalog())
            return
        if text == "Портфолио":
            self.send(user_id, f"Посмотреть примеры работ можно здесь: {PORTFOLIO_URL}", keyboard_portfolio())
            return
        if text == "Перейти в каталог":
            set_context(user_id, CATALOG_STATE, payload)
            self.send(user_id, "Выберите категорию услуг:", keyboard_catalog())
            return
        if text == "Помощь с выбором":
            set_context(user_id, AI_WAITING_BRIEF, payload)
            self.send(user_id, "Опишите вашу компанию и цель. Я подберу подходящие услуги.", keyboard_main())
            return
        if text == "Консультация/техподдержка":
            set_context(user_id, SUPPORT_DESCRIPTION, {"request_type": "support"})
            self.send(user_id, "Опишите проблему или вопрос, с которым нужна помощь.", keyboard_main())
            return

        if state in {CATALOG_STATE, CATALOG_CATEGORY, CATALOG_CONFIRM_ADD}:
            self.handle_catalog(user_id, text, payload)
        elif state in {ORDER_REVIEW, ORDER_REVIEW_REMOVE}:
            self.handle_review(user_id, text, payload, state)
        elif state in {ORDER_NAME, ORDER_PHONE, ORDER_EMAIL, ORDER_COMMENT}:
            self.handle_order_form(user_id, text, payload, state)
        elif state == AI_WAITING_BRIEF:
            self.handle_ai(user_id, text, payload)
        elif state == AI_POST_RESULT:
            self.handle_ai_result(user_id, text, payload)
        elif state in {SUPPORT_DESCRIPTION, SUPPORT_NAME, SUPPORT_PHONE, SUPPORT_EMAIL}:
            self.handle_support(user_id, text, payload, state)
        else:
            self.send(user_id, "Выберите раздел в меню.", keyboard_main())

    def handle_catalog(self, user_id: int, text: str, payload: dict[str, Any]) -> None:
        if text in CATALOG:
            payload["category"] = text
            payload.setdefault("selected_services", [])
            set_context(user_id, CATALOG_CATEGORY, payload)
            self.send(user_id, f"Раздел: {text}. Выберите услугу:", keyboard_category(text))
            return
        if text == "Назад в каталог":
            set_context(user_id, CATALOG_STATE, payload)
            self.send(user_id, "Выберите категорию услуг:", keyboard_catalog())
            return

        category = payload.get("category")
        if payload.get("pending_service") and text == "Вернуться назад":
            payload.pop("pending_service", None)
            set_context(user_id, CATALOG_CATEGORY, payload)
            self.send(user_id, "Выберите услугу или нажмите 'Оформить заявку'.", keyboard_category(category))
            return
        if payload.get("pending_service") and text == "Добавить в заявку":
            service = payload.pop("pending_service", None)
            selected = payload.setdefault("selected_services", [])
            if service and service not in selected:
                selected.append(service)
            set_context(user_id, CATALOG_CATEGORY, payload)
            self.send(
                user_id,
                "Услуга добавлена в заявку. Можете выбрать еще одну или нажать 'Оформить заявку'.",
                keyboard_category(category),
            )
            return
        if category in CATALOG and text in CATALOG[category]:
            payload["pending_service"] = text
            set_context(user_id, CATALOG_CONFIRM_ADD, payload)
            self.send(
                user_id,
                f"{text}\n\n{CATALOG[category][text]}\n\nХотели бы добавить услугу в заявку?",
                keyboard_confirm_add(),
            )
            return
        if text == "Оформить заявку":
            selected = payload.get("selected_services", [])
            if not selected:
                self.send(user_id, "Вы пока не выбрали услуги. Добавьте хотя бы одну.", keyboard_catalog())
                return
            set_context(user_id, ORDER_REVIEW, payload)
            self.show_review(user_id, payload)
            return
        self.send(user_id, "Выберите категорию или услугу с клавиатуры.", keyboard_catalog())

    def show_review(self, user_id: int, payload: dict[str, Any]) -> None:
        selected = payload.get("selected_services", [])
        lines = [f"{index}. {name}" for index, name in enumerate(selected, 1)]
        self.send(user_id, "В заявке сейчас:\n" + "\n".join(lines) + "\n\nПодтвердить или удалить услугу?", keyboard_review())

    def handle_review(self, user_id: int, text: str, payload: dict[str, Any], state: str) -> None:
        selected = payload.get("selected_services", [])
        if not selected:
            set_context(user_id, CATALOG_STATE, payload)
            self.send(user_id, "Список услуг пуст. Выберите услуги в каталоге.", keyboard_catalog())
            return
        if state == ORDER_REVIEW:
            if text == "Подтвердить заявку":
                payload["request_type"] = "catalog"
                set_context(user_id, ORDER_NAME, payload)
                self.send(user_id, "Оформляем заявку.\nКак вас зовут?", keyboard_main())
                return
            if text == "Удалить услугу":
                set_context(user_id, ORDER_REVIEW_REMOVE, payload)
                self.send(user_id, "Введите номер услуги для удаления.", keyboard_review())
                return
            if text == "Назад в каталог":
                set_context(user_id, CATALOG_STATE, payload)
                self.send(user_id, "Выберите категорию услуг:", keyboard_catalog())
                return
            self.show_review(user_id, payload)
            return
        try:
            index = int(text.strip()) - 1
            if index < 0 or index >= len(selected):
                raise ValueError
        except ValueError:
            self.send(user_id, "Введите корректный номер услуги.", keyboard_review())
            return
        removed = selected.pop(index)
        payload["selected_services"] = selected
        if not selected:
            set_context(user_id, CATALOG_STATE, payload)
            self.send(user_id, f"Услуга '{removed}' удалена. Список пуст.", keyboard_catalog())
            return
        set_context(user_id, ORDER_REVIEW, payload)
        self.send(user_id, f"Удалено: {removed}")
        self.show_review(user_id, payload)

    def handle_order_form(self, user_id: int, text: str, payload: dict[str, Any], state: str) -> None:
        if state == ORDER_NAME:
            payload["name"] = text
            set_context(user_id, ORDER_PHONE, payload)
            self.send(user_id, "Введите номер телефона:")
        elif state == ORDER_PHONE:
            if not is_valid_phone(text):
                self.send(user_id, "Введите корректный телефон, например: +7 999 123-45-67")
                return
            payload["phone"] = normalize_phone(text)
            set_context(user_id, ORDER_EMAIL, payload)
            self.send(user_id, "Введите email:")
        elif state == ORDER_EMAIL:
            if not EMAIL_RE.match(text):
                self.send(user_id, "Введите корректный email, например: name@example.com")
                return
            payload["email"] = text
            set_context(user_id, ORDER_COMMENT, payload)
            self.send(user_id, "Добавьте комментарий (или напишите 'нет'):")
        elif state == ORDER_COMMENT:
            payload["comment"] = "" if text.lower() == "нет" else text
            self.submit_request(user_id, payload)

    def handle_ai(self, user_id: int, text: str, payload: dict[str, Any]) -> None:
        advice = suggest_with_openai(text)
        rec = advice.get("recommended_services", [])
        payload["ai_client_brief"] = text
        payload["ai_advice"] = advice
        payload["selected_services"] = [service for service in rec if service in ALL_SERVICES]
        set_context(user_id, AI_POST_RESULT, payload)
        rec_block = "\n".join(f"- {item}" for item in rec) or "- Консультация специалиста"
        self.send(
            user_id,
            f"Рекомендации:\n{rec_block}\n\nПочему: {advice.get('reason')}\n\n{advice.get('message_to_client')}",
            keyboard_ai_result(),
        )

    def handle_ai_result(self, user_id: int, text: str, payload: dict[str, Any]) -> None:
        if text == "Оставить заявку":
            payload["request_type"] = "ai_help"
            set_context(user_id, ORDER_NAME, payload)
            self.send(user_id, "Хорошо, оформим заявку. Как вас зовут?", keyboard_main())
            return
        if text == "Перейти в каталог":
            set_context(user_id, CATALOG_STATE, payload)
            self.send(user_id, "Выберите категорию услуг:", keyboard_catalog())
            return
        self.send(user_id, "Выберите действие с кнопок.", keyboard_ai_result())

    def handle_support(self, user_id: int, text: str, payload: dict[str, Any], state: str) -> None:
        if state == SUPPORT_DESCRIPTION:
            payload["support_description"] = text
            set_context(user_id, SUPPORT_NAME, payload)
            self.send(user_id, "Как вас зовут?")
        elif state == SUPPORT_NAME:
            payload["name"] = text
            set_context(user_id, SUPPORT_PHONE, payload)
            self.send(user_id, "Введите номер телефона:")
        elif state == SUPPORT_PHONE:
            if not is_valid_phone(text):
                self.send(user_id, "Введите корректный телефон, например: +7 999 123-45-67")
                return
            payload["phone"] = normalize_phone(text)
            set_context(user_id, SUPPORT_EMAIL, payload)
            self.send(user_id, "Введите email:")
        elif state == SUPPORT_EMAIL:
            if not EMAIL_RE.match(text):
                self.send(user_id, "Введите корректный email, например: name@example.com")
                return
            payload["email"] = text
            self.submit_support(user_id, payload)

    def submit_request(self, user_id: int, payload: dict[str, Any]) -> None:
        selected = payload.get("selected_services", [])
        req_type = payload.get("request_type", "catalog")
        details = {
            "user_id": user_id,
            "request_type": req_type,
            "name": payload.get("name", ""),
            "phone": payload.get("phone", ""),
            "email": payload.get("email", ""),
            "selected_services": selected,
            "comment": payload.get("comment", ""),
            "ai_client_brief": payload.get("ai_client_brief", ""),
            "ai_advice": payload.get("ai_advice", {}),
        }
        request_id = create_request(user_id, req_type, details)
        comments = [
            f"Тип заявки: {req_type}",
            f"VK user id: {user_id}",
            f"Имя: {details['name']}",
            f"Телефон: {details['phone']}",
            f"Email: {details['email']}",
            "Выбранные услуги:",
            *[f"- {item}" for item in selected],
            f"Комментарий: {details['comment']}",
        ]
        if req_type == "ai_help":
            comments += [
                "",
                "Блок ИИ:",
                f"Запрос клиента: {details['ai_client_brief']}",
                f"Ответ ИИ: {details['ai_advice']}",
            ]
        ok, result = create_bitrix_lead(
            f"VK: {details['name']} ({req_type})",
            {
                "NAME": details["name"],
                "PHONE": [{"VALUE": details["phone"], "VALUE_TYPE": "WORK"}],
                "EMAIL": [{"VALUE": details["email"], "VALUE_TYPE": "WORK"}],
            },
            "\n".join(comments),
        )
        set_request_status(request_id, "SENT" if ok else "FAILED")
        if not ok:
            print(f"Bitrix send error for request {request_id}: {result}")
        self.send(user_id, "Спасибо! Заявка принята. Специалист свяжется с вами.", keyboard_main())
        set_context(user_id, MAIN_MENU, {})

    def submit_support(self, user_id: int, payload: dict[str, Any]) -> None:
        details = {
            "user_id": user_id,
            "request_type": "support",
            "name": payload.get("name", ""),
            "phone": payload.get("phone", ""),
            "email": payload.get("email", ""),
            "description": payload.get("support_description", ""),
        }
        request_id = create_request(user_id, "support", details)
        comments = (
            f"Тип заявки: support\nVK user id: {user_id}\nИмя: {details['name']}\n"
            f"Телефон: {details['phone']}\nEmail: {details['email']}\nОписание: {details['description']}"
        )
        ok, result = create_bitrix_lead(
            f"VK Support: {details['name']}",
            {
                "NAME": details["name"],
                "PHONE": [{"VALUE": details["phone"], "VALUE_TYPE": "WORK"}],
                "EMAIL": [{"VALUE": details["email"], "VALUE_TYPE": "WORK"}],
            },
            comments,
        )
        set_request_status(request_id, "SENT" if ok else "FAILED")
        if not ok:
            print(f"Bitrix send error for support request {request_id}: {result}")
        self.send(user_id, "Спасибо! Обращение передано специалисту.", keyboard_main())
        set_context(user_id, MAIN_MENU, {})


if __name__ == "__main__":
    Bot().start()
