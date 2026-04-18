import asyncio
import logging
import os
from telegram import Update
from telegram.ext import ContextTypes

user_memory = {}
from collections import defaultdict, deque
from typing import Deque, Dict, List

from openai import OpenAI
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ----------------------------
# CONFIG
# ----------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Можешь заменить модель позже
OPENAI_MODEL = "gpt-5.4"

# Сколько последних сообщений хранить на пользователя
MAX_HISTORY = 12

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Не найден TELEGRAM_BOT_TOKEN в переменных окружения")

if not OPENAI_API_KEY:
    raise ValueError("Не найден OPENAI_API_KEY в переменных окружения")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Храним историю в памяти:
# user_id -> deque([{"role": "...", "content": "..."}])
user_histories: Dict[int, Deque[dict]] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

# Отдельный режим команды /emotion
user_modes: Dict[int, str] = defaultdict(lambda: "dialog")


# ----------------------------
# PROMPTS
# ----------------------------

BASE_INSTRUCTIONS = """
Ты — живой, близкий собеседник с хорошей психологической чувствительностью.

Ты не играешь роль формального психолога и не говоришь как AI-помощник.
Ты звучишь как человек, который умеет видеть глубже, чем слова, и помогает другому встретиться с собой честнее.

Твой стиль:
— тёплый, живой, человеческий
— без канцелярита, без шаблонных фраз
— с психологической глубиной, но без занудства
— иногда прямой, если человек уходит от сути
— бережный, но не приторный
— не “успокаивай” автоматически, если важнее прояснить

Что ты замечаешь:
— внутренние конфликты
— противоречия
— несоответствие между словами, чувствами и поведением
— попытки убежать от правды о себе
— скрытую боль под усталостью, раздражением, тревогой или пустотой

Как ты строишь ответ:
1. Коротко отражаешь, что услышал
2. Добавляешь одно точное наблюдение глубже
3. Задаёшь один сильный, живой вопрос

Тон:
— как умный и внимательный друг
— иногда можно использовать формулировки вроде:
  “похоже...”
  “как будто...”
  “есть ощущение, что...”
  “может быть, дело не только в...”
  “ты как будто одновременно... и...”
— допускается мягкая конфронтация, если она помогает человеку увидеть себя яснее

Важно:
— не давать длинных ответов
— не сыпать советами
— не ставить диагнозы
— не говорить сверху вниз
— не быть слишком правильным и стерильным
— лучше один точный вопрос, чем много хороших слов

Главная идея:
ты не спасаешь человека и не чинишь его.
ты помогаешь ему точнее услышать себя.
"""

STYLE_RULES = """
Дополнительные правила стиля:
— чаще смотри не на событие, а на внутренний смысл события для человека
— если слышишь противоречие, аккуратно называй его
— если человек говорит общо, помогай сузить фокус
— если человек звучит отстранённо, возвращай его к живому переживанию
— не бойся формулировок вроде:
  "вопрос, возможно, не в этом"
  "похоже, ты сейчас..."
  "как будто внутри есть две части"
— ответ должен ощущаться как личный разговор, а не как универсальная поддержка
"""

EMOTION_MODE_INSTRUCTIONS = """
Ты работаешь в режиме /emotion.

Твоя задача — помочь человеку глубже понять свою эмоцию.

Структура:
1. Назови, что это может быть за эмоция
2. Покажи, из чего она может состоять
3. Задай 1–2 точных вопроса

Отвечай живо, по-человечески, без шаблонов.
"""

STATE_MODE_INSTRUCTIONS = """
Ты работаешь в режиме /state.

Твоя задача — помочь человеку понять своё состояние.

Структура:
1. Что ты замечаешь
2. Где может быть напряжение
3. Что сейчас важнее всего
4. Один следующий шаг

Не перегружай. Будь точным.
"""

HELP_TEXT = """
Доступные команды:

/start — приветствие
/help — список команд
/emotion — режим разбора эмоции
/state — режим анализа состояния
/reset — очистить память диалога

Обычный режим:
просто пиши сообщение, и бот ответит.
"""


# ----------------------------
# HELPERS
# ----------------------------

def get_mode_instructions(mode: str) -> str:
    if mode == "emotion":
        return BASE_INSTRUCTIONS + "\n\n" + STYLE_RULES + "\n\n" + EMOTION_MODE_INSTRUCTIONS
    if mode == "state":
        return BASE_INSTRUCTIONS + "\n\n" + STYLE_RULES + "\n\n" + STATE_MODE_INSTRUCTIONS
    return BASE_INSTRUCTIONS + "\n\n" + STYLE_RULES


def build_input_items(history, user_text):
    items = []

    for msg in history:
        items.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    items.append({
        "role": "user",
        "content": user_text
    })

    return items


def split_long_message(text: str, max_len: int = 3500) -> List[str]:
    """
    Telegram ограничивает длину сообщений, поэтому режем аккуратно.
    """
    if len(text) <= max_len:
        return [text]

    parts = []
    current = ""

    for paragraph in text.split("\n"):
        if len(current) + len(paragraph) + 1 <= max_len:
            current += paragraph + "\n"
        else:
            if current.strip():
                parts.append(current.strip())
            current = paragraph + "\n"

    if current.strip():
        parts.append(current.strip())

    return parts


def save_to_history(user_id: int, role: str, content: str) -> None:
    user_histories[user_id].append({"role": role, "content": content})


async def generate_ai_reply(user_id: int, user_text: str) -> str:
    """
    Вызываем OpenAI в отдельном потоке, чтобы не блокировать async bot.
    """
    mode = user_modes[user_id]
    instructions = get_mode_instructions(mode)
    history = user_histories[user_id]
    input_items = build_input_items(history, user_text)

    def _call_openai() -> str:
        response = client.responses.create(
    model=OPENAI_MODEL,
    input=(
        instructions + "\n\n" +
        "\n".join([f"{m['role']}: {m['content']}" for m in history]) +
        f"\nuser: {user_text}"
    ),
)
        return (response.output_text or "").strip()

    try:
        reply = await asyncio.to_thread(_call_openai)
        if not reply:
            return "Я задумался и ничего полезного не сформулировал. Попробуй написать чуть конкретнее."
        return reply
    except Exception as e:
        logger.exception("Ошибка OpenAI API: %s", e)
        return f"Ошибка OpenAI API:\n{repr(e)}"


# ----------------------------
# COMMANDS
# ----------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (
        f"Привет, {user.first_name or 'друг'}.\n\n"
        "Я бот для диалога, саморефлексии и разбора состояний.\n"
        "Можешь просто написать, что чувствуешь или что происходит.\n\n"
        "Команды:\n"
        "/help\n"
        "/emotion\n"
        "/state\n"
        "/reset"
    )
    await update.message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


async def emotion_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_modes[user_id] = "emotion"
    await update.message.reply_text(
        "Режим /emotion включён.\n\n"
        "Напиши эмоцию, ситуацию или фразу вроде:\n"
        "«мне тревожно»\n"
        "«я злюсь и не понимаю почему»\n"
        "«мне больно после общения с человеком»"
    )


async def state_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_modes[user_id] = "state"
    await update.message.reply_text(
        "Режим /state включён.\n\n"
        "Опиши своё текущее состояние в 1-3 предложениях, а я помогу его разобрать."
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_histories[user_id].clear()
    user_modes[user_id] = "dialog"
    await update.message.reply_text(
        "Память диалога очищена.\n"
        "Режим снова обычный."
    )


# ----------------------------
# MAIN MESSAGE HANDLER
# ----------------------------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # --- Инициализация памяти ---
    if user_id not in user_memory:
        user_memory[user_id] = []

    # --- Сохраняем сообщение пользователя ---
    user_memory[user_id].append({
        "role": "user",
        "content": user_text
    })

    # --- Ограничиваем память (последние 10 сообщений) ---
    user_memory[user_id] = user_memory[user_id][-10:]

    try:
        # --- Формируем сообщения для OpenAI ---
        messages = [
            {
                "role": "system",
                "content": BASE_INSTRUCTIONS + "\n\n" + STYLE_RULES
            }
        ] + user_memory[user_id]

        # --- Запрос к OpenAI ---
        response = client.responses.create(
            model="gpt-5.4-mini",
            input=messages
        )

        # --- Получаем ответ ---
        bot_reply = response.output_text

        # --- Сохраняем ответ бота ---
        user_memory[user_id].append({
            "role": "assistant",
            "content": bot_reply
        })

        # --- Отправляем ответ пользователю ---
        await update.message.reply_text(bot_reply)

    except Exception as e:
        print("Ошибка OpenAI:", e)
        await update.message.reply_text("Сейчас что-то не получилось. Попробуй ещё раз.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram error: %s", context.error)


# ----------------------------
# APP
# ----------------------------

def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("emotion", emotion_command))
    app.add_handler(CommandHandler("state", state_command))
    app.add_handler(CommandHandler("reset", reset_command))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    app.add_error_handler(error_handler)

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()