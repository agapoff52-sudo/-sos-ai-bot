import asyncio
import logging
import os
import json
from datetime import datetime
from telegram import Update
from telegram import Update
from telegram.ext import ContextTypes

user_memory = {}
user_profiles = {}
analytics = {
    "total_messages": 0,
    "total_users": 0
}

DATA_FILE = "bot_data.json"
ADMIN_ID = 221532110  # сюда вставишь свой Telegram user_id
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
— структура НЕ фиксированная
— иногда это 1 короткое предложение
— иногда 2–3 абзаца
— иногда только вопрос
— ориентируйся на состояние человека, а не на шаблон

Важно:
— не делай ответы одинаковой длины

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
— не уходи от разговора фразами вроде "если хочешь можем не говорить"
— если человек пришёл с состоянием, помогай его прояснить, а не избегать
— мягкость не должна превращаться в уклонение
— если смысл неочевиден, предложи 2 возможные интерпретации состояния
— делай это коротко и точно
— не превращай это в длинный список
— если человек задаёт короткий вопрос ("почему", "и что", "зачем"),
  обязательно связывай ответ с предыдущим сообщением
— не начинай новый смысл с нуля

Если человек говорит о суициде, желании исчезнуть или сильной безысходности:

— не игнорируй это
— не обесценивай
— не давай жёстких инструкций
— сначала отрази состояние человека
— мягко уточни уровень опасности ("ты сейчас просто говоришь или есть мысли навредить себе?")
— предложи обратиться к живому человеку (друг, близкий, специалист)

Тон:
— спокойный
— бережный
— без паники
— без давления

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
— если человек задаёт короткий вопрос (например: "почему", "и что", "зачем"), 
  интерпретируй его строго в контексте предыдущего сообщения, 
  не расширяй тему и не уходи в обобщения
— не усложняй, если человек говорит просто
— отвечай ближе к живому разговору, а не как эксперт
— если видишь очевидный смысл, говори его прямо, без размазывания
— не бойся формулировок вроде:
  "вопрос, возможно, не в этом"
  "похоже, ты сейчас..."
  "как будто внутри есть две части"
— отвечай как внимательный человек, который умеет видеть глубже слов
— если видишь очевидный смысл, говори его прямо, но без грубости
— ответ должен ощущаться как личный разговор, а не как универсальная поддержка
— не повторяй одинаковые структуры ответов
— избегай одинаковых формулировок в начале ответа
— чередуй стиль: иногда через наблюдение, иногда через вопрос, иногда через прямую мысль
— не используй одни и те же слова и конструкции несколько сообщений подряд
— не используй одинаковые вводные фразы ("похоже", "как будто", "слышу") подряд
— заменяй их синонимами или убирай полностью
— допускается иногда говорить прямо, без вводных конструкций
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

MIRROR_MODE_INSTRUCTIONS = """
Ты работаешь в режиме /mirror.

Твоя задача — быть честным зеркалом.
Не утешать автоматически и не сглаживать.

Что делать:
1. Коротко назвать, что ты видишь
2. Указать на возможное внутреннее противоречие
3. Задать один точный вопрос, от которого сложно уйти

Тон:
— прямой
— тёплый
— честный
— без грубости
"""

HELP_TEXT = """
Доступные команды:

/start — приветствие
/help — список команд
/emotion — режим разбора эмоции
/state — режим анализа состояния
/mirror — режим честного зеркала
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
    if mode == "mirror":
        return BASE_INSTRUCTIONS + "\n\n" + STYLE_RULES + "\n\n" + MIRROR_MODE_INSTRUCTIONS
    return BASE_INSTRUCTIONS + "\n\n" + STYLE_RULES


def load_data():
    global user_memory, user_profiles, analytics

    if not os.path.exists(DATA_FILE):
        return

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        user_memory = {int(k): v for k, v in data.get("user_memory", {}).items()}
        user_profiles = {int(k): v for k, v in data.get("user_profiles", {}).items()}
        analytics = data.get("analytics", {
            "total_messages": 0,
            "total_users": 0
        })
    except Exception as e:
        print("Ошибка загрузки данных:", e)


def save_data():
    try:
        data = {
            "user_memory": user_memory,
            "user_profiles": user_profiles,
            "analytics": analytics
        }

        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Ошибка сохранения данных:", e)

def update_user_profile(user_id: int, user_text: str):
    if user_id not in user_profiles:
        user_profiles[user_id] = {
            "summary": "",
            "themes": [],
            "last_seen": "",
            "message_count": 0,
            "username": "",
            "first_name": ""
        }

    profile = user_profiles[user_id]

    profile["last_seen"] = datetime.utcnow().isoformat()
    profile["message_count"] += 1

    text_lower = user_text.lower()

    possible_themes = {
        "усталость": ["устал", "вымотан", "нет сил"],
        "тревога": ["тревога", "тревожно", "страшно"],
        "одиночество": ["одиноко", "одиночество", "никто"],
        "пустота": ["пусто", "пустота", "ничего не чувствую"],
        "отношения": ["отношения", "партнёр", "любовь", "расставание"],
        "смысл": ["смысл", "зачем", "для чего"],
        "самооценка": ["недостаточно", "неуверенность", "не ценят"]
    }

    found_themes = []

    for theme, keywords in possible_themes.items():
        for keyword in keywords:
            if keyword in text_lower:
                found_themes.append(theme)
                break

    current_themes = set(profile.get("themes", []))
    current_themes.update(found_themes)
    profile["themes"] = list(current_themes)[:10]

    if not profile["summary"]:
        profile["summary"] = f"Пользователь часто пишет о темах: {', '.join(profile['themes'])}" if profile["themes"] else "Профиль пока пуст."
    else:
        if profile["themes"]:
            profile["summary"] = f"Повторяющиеся темы: {', '.join(profile['themes'])}"

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


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


async def mirror_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_modes[user_id] = "mirror"
    await update.message.reply_text(
        "Режим /mirror включён.\n\n"
        "Можешь написать мысль, состояние или ситуацию, а я отвечу как честное зеркало — прямее и глубже."
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_histories[user_id].clear()
    user_modes[user_id] = "dialog"
    await update.message.reply_text(
        "Память диалога очищена.\n"
        "Режим снова обычный."
    )

async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("Нет доступа.")
        return

    text = (
        f"Статистика бота:\n\n"
        f"Всего пользователей: {len(user_profiles)}\n"
        f"Всего сообщений: {analytics.get('total_messages', 0)}\n"
        f"Активных диалогов в памяти: {len(user_memory)}"
    )
    await update.message.reply_text(text)


async def admin_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("Нет доступа.")
        return

    if not user_profiles:
        await update.message.reply_text("Пользователей пока нет.")
        return

    lines = []

    for uid, profile in user_profiles.items():
        line = (
            f"ID: {uid}\n"
            f"Имя: {profile.get('first_name', '')}\n"
            f"Username: @{profile.get('username', '')}\n"
            f"Сообщений: {profile.get('message_count', 0)}\n"
            f"Темы: {', '.join(profile.get('themes', []))}\n"
        )
        lines.append(line)

    text = "\n---\n".join(lines)
    await update.message.reply_text(text[:4000])


async def admin_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("Нет доступа.")
        return

    if not context.args:
        await update.message.reply_text("Используй: /admin_history user_id")
        return

    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id должен быть числом.")
        return

    if target_user_id not in user_memory or not user_memory[target_user_id]:
        await update.message.reply_text("История для этого пользователя не найдена.")
        return

    lines = []

    for msg in user_memory[target_user_id]:
        role = "Пользователь" if msg["role"] == "user" else "Бот"
        lines.append(f"{role}: {msg['content']}")

    text = "\n\n".join(lines)
    await update.message.reply_text(text[:4000])


async def admin_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("Нет доступа.")
        return

    if not context.args:
        await update.message.reply_text("Используй: /admin_profile user_id")
        return

    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id должен быть числом.")
        return

    profile = user_profiles.get(target_user_id)

    if not profile:
        await update.message.reply_text("Профиль не найден.")
        return

    text = (
        f"Профиль пользователя {target_user_id}:\n\n"
        f"Имя: {profile.get('first_name', '')}\n"
        f"Username: @{profile.get('username', '')}\n"
        f"Сообщений: {profile.get('message_count', 0)}\n"
        f"Последняя активность: {profile.get('last_seen', '')}\n"
        f"Темы: {', '.join(profile.get('themes', []))}\n"
        f"Summary: {profile.get('summary', '')}"
    )

    await update.message.reply_text(text[:4000])


# ----------------------------
# MAIN MESSAGE HANDLER
# ----------------------------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    user_text = update.message.text.strip()

    if not user_text:
        return

    if user_id not in user_memory:
        user_memory[user_id] = []

    if user_id not in user_profiles:
        user_profiles[user_id] = {
            "summary": "",
            "themes": [],
            "last_seen": "",
            "message_count": 0,
            "username": user.username or "",
            "first_name": user.first_name or ""
        }

    user_profiles[user_id]["username"] = user.username or ""
    user_profiles[user_id]["first_name"] = user.first_name or ""

    user_memory[user_id].append({
        "role": "user",
        "content": user_text
    })

    user_memory[user_id] = user_memory[user_id][-20:]

    analytics["total_messages"] = analytics.get("total_messages", 0) + 1
    analytics["total_users"] = len(user_profiles)

    update_user_profile(user_id, user_text)
    save_data()

    try:
        messages = [
            {
                "role": "system",
                "content": BASE_INSTRUCTIONS + "\n\n" + STYLE_RULES
            }
        ] + user_memory[user_id]

        response = client.responses.create(
            model="gpt-5.4-mini",
            input=messages
        )

        bot_reply = (response.output_text or "").strip()

        if not bot_reply:
            bot_reply = "Сейчас у меня не получилось нормально сформулировать ответ. Попробуй сказать это чуть подробнее."

        user_memory[user_id].append({
            "role": "assistant",
            "content": bot_reply
        })

        user_memory[user_id] = user_memory[user_id][-20:]
        save_data()

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
    load_data()


    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("emotion", emotion_command))
    app.add_handler(CommandHandler("state", state_command))
    app.add_handler(CommandHandler("mirror", mirror_command))
    app.add_handler(CommandHandler("reset", reset_command))


    app.add_handler(CommandHandler("admin_stats", admin_stats_command))
    app.add_handler(CommandHandler("admin_users", admin_users_command))
    app.add_handler(CommandHandler("admin_history", admin_history_command))
    app.add_handler(CommandHandler("admin_profile", admin_profile_command))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    app.add_error_handler(error_handler)

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()