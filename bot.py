import os, logging, sqlite3, hashlib, random, asyncio
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters, PreCheckoutQueryHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_USERNAME = "WeirdMeetBot"  # без @

# ---------- Состояния ----------
(CHOOSE_MODE, BIRTH_DATE, GENDER, PHONE_VERIFY, CITY_SELF,
 STRANGE_HABIT, FAVORITE_MEME, SECRET_ACTION,
 DISLIKED_BOOKS, BOOK_REASON,
 CITY, ENERGY_PLACE, HATE_PLACE, WANT_PLACE,
 MICRO_Q1, MICRO_Q2, MICRO_Q3,
 APPEAL_TEXT) = range(19)

# ---------- Фильтры ----------
FORBIDDEN_KEYWORDS = [
    "убью", "взорвать", "теракт", "насилие", "оружие", "бомба",
    "нацист", "фашист", "свастика", "расизм", "наркотик", "спайс",
    "лохотрон", "развод", "суицид", "секта", "хуй", "пизда"
]
TERRORISM_KEYWORDS = ["теракт", "джихад", "шахид", "вербовка", "смертник"]

def is_safe_text(text):
    low = text.lower()
    found = [w for w in FORBIDDEN_KEYWORDS + TERRORISM_KEYWORDS if w in low]
    if "http://" in low or "https://" in low: found.append("ссылка")
    return len(found) == 0, found

def has_terrorism(text): return any(w in text.lower() for w in TERRORISM_KEYWORDS)

# ---------- База данных ----------
def init_db():
    # ← ИСПРАВЛЕНО: создаём папку /data для постоянного хранения
    os.makedirs("/data", exist_ok=True)
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО: путь к БД
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        mode TEXT, birth_date TEXT, registered_at TEXT,
        coins INTEGER DEFAULT 0, subscription_type TEXT, subscription_expiry TEXT,
        likes_today INTEGER DEFAULT 0, last_like_date TEXT,
        gender TEXT DEFAULT '', phone_hash TEXT DEFAULT '',
        city TEXT DEFAULT '', search_city TEXT DEFAULT '')""")
    c.execute("CREATE TABLE IF NOT EXISTS weird_profiles (user_id PRIMARY KEY, strange_habit TEXT, favorite_meme TEXT, secret_action TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS book_profiles (user_id PRIMARY KEY, disliked_books TEXT, book_reason TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS city_profiles (user_id PRIMARY KEY, city TEXT, energy_place TEXT, hate_place TEXT, want_place TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS micro_profiles (user_id PRIMARY KEY, answer_1 TEXT, answer_2 TEXT, answer_3 TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS likes (from_user INTEGER, to_user INTEGER, mode TEXT, timestamp TEXT, PRIMARY KEY(from_user, to_user, mode))")
    c.execute("CREATE TABLE IF NOT EXISTS matches (user1 INTEGER, user2 INTEGER, mode TEXT, match_date TEXT, PRIMARY KEY(user1, user2, mode))")
    c.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id PRIMARY KEY, reason TEXT, banned_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS complaints (id INTEGER PRIMARY KEY AUTOINCREMENT, from_user INTEGER, about_user INTEGER, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS moderation_flags (user_id PRIMARY KEY, flag TEXT, details TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS appeals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS message_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, message_text TEXT, filter_triggered INTEGER, filter_reason TEXT, timestamp TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS active_packs (user_id INTEGER, pack_name TEXT, expiry TEXT, uses_left INTEGER, PRIMARY KEY(user_id, pack_name))")
    c.execute("""CREATE TABLE IF NOT EXISTS referrals (referrer_id INTEGER, referred_id INTEGER PRIMARY KEY, date TEXT, bonus_given INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_profiles (
        user_id INTEGER PRIMARY KEY, mode TEXT, name TEXT,
        active INTEGER DEFAULT 1, gender TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER, target_id INTEGER,
        action TEXT, timestamp TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ai_id INTEGER,
        message_text TEXT, from_ai INTEGER DEFAULT 0, timestamp TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_daily_limit (
        user_id INTEGER, ai_id INTEGER, date TEXT, msg_count INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, ai_id, date))""")
    conn.commit()
    conn.close()

init_db()

# ---------- AI настройки ----------
AI_START_RANGE = 200000
MIN_PROFILES_PER_MODE = {"weird": 100, "books": 100, "city": 100, "micro": 100}

MALE_NAMES_POOL = [
    "Александр","Максим","Артём","Дмитрий","Иван","Кирилл","Никита","Егор","Алексей","Владимир"
]
FEMALE_NAMES_POOL = [
    "Алиса","Мария","Анна","Дарья","Ксения","Ольга","Юлия","Анастасия","Полина","Елена"
]
AI_HABITS = ["Постоянно теряю носки, но нахожу их в холодильнике","Разговариваю с кошкой, когда никто не слышит","Пью чай только из одной кружки"]
AI_BOOKS = ["«1984» — слишком мрачно для меня","«Война и мир» — честно, не осилил","«Мастер и Маргарита» — переоценена"]
AI_BOOK_REASONS = ["Потому что не смог дочитать до конца","Много воды, мало смысла"]
AI_CITIES = ["Москва","Санкт-Петербург","Казань"]
AI_ENERGY_PLACES = ["Парк Горького","Красная площадь"]
AI_HATE_PLACES = ["Пробки на ТТК","Станция метро Выхино"]
AI_WANT_PLACES = ["Смотровая площадка Москва-Сити","Эрмитаж"]

# ---------- Служебные функции ----------
def count_active_profiles():
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE user_id NOT IN (SELECT user_id FROM banned_users)")
    cnt = c.fetchone()[0]
    conn.close()
    return cnt

def is_launch_mode(): return count_active_profiles() < 500

def reset_daily_limits(user_id):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("UPDATE users SET likes_today=0, last_like_date=? WHERE user_id=?", (date.today().isoformat(), user_id))
    conn.commit()
    conn.close()

def can_like(user_id):
    if is_launch_mode(): return True
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT likes_today, last_like_date, subscription_type, subscription_expiry FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row: return False
    likes, last_date, sub_type, sub_exp = row
    if last_date != date.today().isoformat():
        reset_daily_limits(user_id)
        likes = 0
    if sub_type and sub_exp and sub_exp > datetime.now().strftime("%Y-%m-%d %H:%M"):
        return True
    return likes < 15

def is_banned(user_id):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT 1 FROM banned_users WHERE user_id=?", (user_id,))
    return c.fetchone() is not None

def log_message(user_id, chat_id, text, triggered, reason):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("INSERT INTO message_log VALUES (NULL,?,?,?,?,?,?)",
              (user_id, chat_id, text, int(triggered), reason, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def add_complaint(from_user, about_user):
    if about_user >= AI_START_RANGE: return 0
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("INSERT INTO complaints (from_user, about_user, created_at) VALUES (?,?,?)",
              (from_user, about_user, datetime.now().strftime("%Y-%m-%d %H:%M")))
    c.execute("SELECT COUNT(*) FROM complaints WHERE about_user=?", (about_user,))
    count = c.fetchone()[0]
    if count >= 3:
        c.execute("INSERT OR REPLACE INTO moderation_flags VALUES (?,?,?)", (about_user, "under_review", f"Жалоб: {count}"))
    conn.commit()
    conn.close()
    return count

def ban_user(user_id, reason="Нарушение"):
    if user_id >= AI_START_RANGE: return
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO banned_users VALUES (?,?,?)", (user_id, reason, datetime.now().strftime("%Y-%m-%d %H:%M")))
    c.execute("INSERT OR REPLACE INTO moderation_flags VALUES (?,?,?)", (user_id, "banned", reason))
    conn.commit()
    conn.close()

def unban_user(user_id):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
    c.execute("DELETE FROM moderation_flags WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def add_coins(user_id, amount):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def spend_coins(user_id, amount):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("UPDATE users SET coins = coins - ? WHERE user_id=? AND coins >= ?", (amount, user_id, amount))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok

def get_referral_link(user_id):
    token = hashlib.md5(str(user_id).encode()).hexdigest()[:8]
    return f"https://t.me/{BOT_USERNAME}?start=ref{user_id}_{token}"

async def process_referral_bonus(user_id, context):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT referrer_id, bonus_given FROM referrals WHERE referred_id=?", (user_id,))
    ref = c.fetchone()
    if ref and not ref[1]:
        referrer_id = ref[0]
        add_coins(referrer_id, 50)
        c.execute("UPDATE referrals SET bonus_given=1 WHERE referred_id=?", (user_id,))
        conn.commit()
        try: await context.bot.send_message(referrer_id, "🎉 Ваш друг зарегистрировался! Вы получили 50 коинов.")
        except: pass
        c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND bonus_given=1", (referrer_id,))
        cnt = c.fetchone()[0]
        if cnt % 3 == 0:
            expiry = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
            c.execute("INSERT OR REPLACE INTO active_packs VALUES (?, 'referral_boost', ?, 1)", (referrer_id, expiry))
            try: await context.bot.send_message(referrer_id, "🚀 За каждые 3 друга — бесплатный буст на 1 час!")
            except: pass
    conn.close()

# ==================== AI-функции (без аватарок) ====================
def get_available_ai_name(mode, gender):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT name FROM ai_profiles WHERE mode=? AND gender=? AND active=1", (mode, gender))
    used = {r[0] for r in c.fetchall()}
    conn.close()
    pool = MALE_NAMES_POOL if gender == "male" else FEMALE_NAMES_POOL
    available = [n for n in pool if n not in used]
    if not available: available = pool
    return random.choice(available)

def create_single_ai(c, mode, gender=None):
    if gender is None: gender = random.choice(["male", "female"])
    name = get_available_ai_name(mode, gender)
    c.execute("SELECT MAX(user_id) FROM ai_profiles")
    max_id = c.fetchone()[0]
    new_id = max(AI_START_RANGE, (max_id or AI_START_RANGE) + 1)
    c.execute("""INSERT OR IGNORE INTO users (user_id, username, first_name, mode, birth_date, registered_at, subscription_type, gender, city, search_city)
                 VALUES (?, '', ?, ?, ?, ?, 'ai', ?, ?, ?)""",
              (new_id, name, mode, "2000-01-01", datetime.now().strftime("%Y-%m-%d %H:%M"), gender, random.choice(AI_CITIES), random.choice(AI_CITIES)))
    if mode == "weird":
        habit = random.choice(AI_HABITS)
        meme = "люблю мемы с котами"
        secret = random.choice(["Пою в душе","Танцую тайком","Смотрю мультики"])
        c.execute("INSERT OR IGNORE INTO weird_profiles VALUES (?,?,?,?)", (new_id, habit, meme, secret))
    elif mode == "books":
        books = random.choice(AI_BOOKS)
        reason = random.choice(AI_BOOK_REASONS)
        c.execute("INSERT OR IGNORE INTO book_profiles VALUES (?,?,?)", (new_id, books, reason))
    elif mode == "city":
        city = random.choice(AI_CITIES)
        energy = random.choice(AI_ENERGY_PLACES)
        hate = random.choice(AI_HATE_PLACES)
        want = random.choice(AI_WANT_PLACES)
        c.execute("INSERT OR IGNORE INTO city_profiles VALUES (?,?,?,?,?)", (new_id, city, energy, hate, want))
    else:
        if gender == "male":
            answers = random.choice([["morning","coffee","mountains"], ["evening","tea","sea"]])
        else:
            answers = random.choice([["evening","tea","sea"], ["morning","coffee","mountains"]])
        c.execute("INSERT OR IGNORE INTO micro_profiles VALUES (?,?,?,?)", (new_id, answers[0], answers[1], answers[2]))
    c.execute("INSERT OR IGNORE INTO ai_profiles (user_id, mode, name, active, gender) VALUES (?,?,?,1,?)",
              (new_id, mode, name, gender))

def count_real_users_in_mode(mode):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE mode=? AND user_id NOT IN (SELECT user_id FROM ai_profiles) AND user_id NOT IN (SELECT user_id FROM banned_users)", (mode,))
    cnt = c.fetchone()[0]
    conn.close()
    return cnt

def count_active_ai_in_mode(mode):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM ai_profiles WHERE mode=? AND active=1", (mode,))
    cnt = c.fetchone()[0]
    conn.close()
    return cnt

def manage_ai_for_mode(mode):
    real = count_real_users_in_mode(mode)
    current_ai = count_active_ai_in_mode(mode)
    target = MIN_PROFILES_PER_MODE[mode]
    desired_ai = max(0, target - real)
    if desired_ai == current_ai: return
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    if desired_ai > current_ai:
        to_create = desired_ai - current_ai
        create_male = to_create // 2
        create_female = to_create - create_male
        for _ in range(create_male): create_single_ai(c, mode, "male")
        for _ in range(create_female): create_single_ai(c, mode, "female")
    else:
        to_deactivate = current_ai - desired_ai
        c.execute("UPDATE ai_profiles SET active=0 WHERE user_id IN (SELECT user_id FROM ai_profiles WHERE mode=? AND active=1 LIMIT ?)", (mode, to_deactivate))
    conn.commit()
    conn.close()

def manage_all_ai():
    for mode in MIN_PROFILES_PER_MODE: manage_ai_for_mode(mode)

# Диалоги AI
AI_DIALOG_TEMPLATES = {
    "weird": {
        "greeting": ["Привет! Давно искал кого-то с причудами. Расскажи, какая у тебя самая странная привычка? 😄"],
        "reply_habit": ["Ого, интересно! Почему ты так делаешь?"],
        "reply_default": ["А что ещё тебя увлекает?"],
        "goodbye": ["Ладно, мне пора. Приятно было поболтать!"]
    },
    "books": {
        "greeting": ["Привет! Какая книга тебя разочаровала больше всего?"],
        "reply_book": ["О да, я тоже плевался. А что конкретно не понравилось?"],
        "reply_default": ["А какой жанр любишь?"],
        "goodbye": ["Ладно, пойду читать что-нибудь новое. Пока!"]
    },
    "city": {
        "greeting": ["Привет! Я обожаю гулять по городу. А ты где любишь бывать?"],
        "reply_place": ["О, я знаю это место! Классная атмосфера."],
        "reply_default": ["А в других городах бывал?"],
        "goodbye": ["Ладно, я пойду гулять. Хорошего дня!"]
    },
    "micro": {
        "greeting": ["Привет! Утро или вечер? Я больше люблю рассветы."],
        "reply_default": ["Интересный выбор. Почему тебе нравится именно это?"],
        "goodbye": ["Мне пора бежать. Было приятно поболтать!"]
    }
}

def can_ai_reply(user_id, ai_id):
    today = date.today().isoformat()
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT msg_count FROM ai_daily_limit WHERE user_id=? AND ai_id=? AND date=?", (user_id, ai_id, today))
    row = c.fetchone()
    conn.close()
    if row and row[0] >= 10: return False
    return True

def record_ai_message(user_id, ai_id, from_ai, text):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    today = date.today().isoformat()
    c.execute("INSERT INTO ai_conversations (user_id, ai_id, message_text, from_ai, timestamp) VALUES (?,?,?,?,?)",
              (user_id, ai_id, text, int(from_ai), datetime.now().strftime("%Y-%m-%d %H:%M")))
    c.execute("INSERT OR IGNORE INTO ai_daily_limit (user_id, ai_id, date, msg_count) VALUES (?,?,?,0)", (user_id, ai_id, today))
    if from_ai:
        c.execute("UPDATE ai_daily_limit SET msg_count = msg_count + 1 WHERE user_id=? AND ai_id=? AND date=?", (user_id, ai_id, today))
    conn.commit()
    conn.close()

def generate_ai_response(user_msg, mode, profile_info):
    templates = AI_DIALOG_TEMPLATES.get(mode, AI_DIALOG_TEMPLATES["micro"])
    msg_lower = user_msg.lower()
    if any(w in msg_lower for w in ["привет", "здравствуй"]): return random.choice(templates["greeting"])
    if mode == "weird" and any(w in msg_lower for w in ["привычка", "странность"]): return random.choice(templates["reply_habit"])
    if mode == "books" and any(w in msg_lower for w in ["книга", "читать"]): return random.choice(templates["reply_book"])
    if mode == "city" and any(w in msg_lower for w in ["место", "парк"]): return random.choice(templates["reply_place"])
    if any(w in msg_lower for w in ["пока", "до свидания"]): return random.choice(templates["goodbye"])
    return random.choice(templates["reply_default"])

async def ai_like_random_users(app: Application):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT user_id, mode FROM ai_profiles WHERE active=1")
    ai_list = c.fetchall()
    if not ai_list: return
    for ai_id, ai_mode in ai_list:
        c.execute("""SELECT user_id FROM users
                     WHERE mode=? AND user_id NOT IN (SELECT user_id FROM ai_profiles)
                     AND user_id NOT IN (SELECT user_id FROM banned_users)
                     AND user_id NOT IN (SELECT to_user FROM likes WHERE from_user=? AND mode=?)
                     ORDER BY RANDOM() LIMIT 1""", (ai_mode, ai_id, ai_mode))
        target = c.fetchone()
        if not target: continue
        target_id = target[0]
        c.execute("INSERT OR IGNORE INTO likes VALUES (?,?,?,?)", (ai_id, target_id, ai_mode, datetime.now().strftime("%Y-%m-%d %H:%M")))
        c.execute("INSERT INTO ai_actions (actor_id, target_id, action, timestamp) VALUES (?,?,?,?)", (ai_id, target_id, "like", datetime.now().strftime("%Y-%m-%d %H:%M")))
        c.execute("SELECT * FROM likes WHERE from_user=? AND to_user=? AND mode=?", (target_id, ai_id, ai_mode))
        if c.fetchone():
            c.execute("INSERT OR IGNORE INTO matches VALUES (?,?,?,?)", (min(ai_id, target_id), max(ai_id, target_id), ai_mode, datetime.now().strftime("%Y-%m-%d %H:%M")))
            greeting = random.choice(AI_DIALOG_TEMPLATES[ai_mode]["greeting"])
            try:
                await app.bot.send_message(target_id, f"🤖 {greeting}")
                record_ai_message(target_id, ai_id, True, greeting)
            except: pass
    conn.commit()
    conn.close()

async def handle_ai_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    user_id = msg.from_user.id
    text = msg.text
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("""SELECT m.user1, m.user2, m.mode, a.user_id FROM matches m
                 JOIN ai_profiles a ON (a.user_id = m.user1 OR a.user_id = m.user2)
                 WHERE (m.user1 = ? OR m.user2 = ?) AND a.active = 1 LIMIT 1""", (user_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    u1, u2, mode, ai_id = row
    if not can_ai_reply(user_id, ai_id):
        await msg.reply_text("🤖 Пока не могу ответить (дневной лимит). Попробуйте позже.")
        conn.close()
        return
    record_ai_message(user_id, ai_id, False, text)
    response = generate_ai_response(text, mode, {})
    await asyncio.sleep(random.randint(2,5))
    await msg.reply_text(f"🤖 {response}")
    record_ai_message(user_id, ai_id, True, response)
    conn.close()

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================
async def start(update, context):
    user_id = update.effective_user.id
    args = context.args
    if args and args[0].startswith("ref"):
        try:
            ref_part = args[0][3:]
            referrer_id_str, _ = ref_part.split("_")
            referrer_id = int(referrer_id_str)
            if referrer_id != user_id:
                conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
                c = conn.cursor()
                c.execute("SELECT referrer_id FROM referrals WHERE referred_id=?", (user_id,))
                if not c.fetchone():
                    c.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id, date) VALUES (?, ?, ?)",
                              (referrer_id, user_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
                conn.commit()
                conn.close()
        except: pass
    if is_banned(user_id):
        await update.message.reply_text("⛔ Вы заблокированы. /appeal")
        return
    text = "🌟 Добро пожаловать в бот знакомств с характером!\n\n"
    text += "🎭 Анти‑тиндер — странные привычки\n"
    text += "📚 Книжный клуб — нелюбимые книги\n"
    text += "🗺 Городские истории — места в городе\n"
    text += "❓ Микро‑диалоги — три вопроса\n\n"
    if is_launch_mode():
        text += "🎉 МЫ РАСТЁМ! Пока всё абсолютно бесплатно — без ограничений по времени."
    await update.message.reply_text(text, reply_markup=main_keyboard())

def main_keyboard():
    kb = [[InlineKeyboardButton("🎭 Анти‑тиндер", callback_data="mode_weird")],
          [InlineKeyboardButton("📚 Книжный клуб", callback_data="mode_books")],
          [InlineKeyboardButton("🗺 Городские истории", callback_data="mode_city")],
          [InlineKeyboardButton("❓ Микро‑диалоги", callback_data="mode_micro")],
          [InlineKeyboardButton("💕 Мэтчи", callback_data="my_matches")],
          [InlineKeyboardButton("👤 Профиль", callback_data="my_profile")],
          [InlineKeyboardButton("🔗 Пригласить друга", callback_data="invite")]]
    if not is_launch_mode():
        kb.append([InlineKeyboardButton("🛒 Магазин", callback_data="shop")])
    return InlineKeyboardMarkup(kb)

def search_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Лайк", callback_data=f"like_{user_id}"),
         InlineKeyboardButton("👎 Дизлайк", callback_data=f"dislike_{user_id}")],
        [InlineKeyboardButton("🚩 Жалоба", callback_data=f"report_{user_id}")],
        [InlineKeyboardButton("⏹ Стоп", callback_data="stop_search")]
    ])

# ---------- Регистрация ----------
async def choose_mode(update, context):
    query = update.callback_query; await query.answer()
    if is_banned(query.from_user.id):
        await query.edit_message_text("⛔ Вы заблокированы."); return ConversationHandler.END
    mode = query.data.split("_")[1]
    context.user_data["mode"] = mode
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT mode FROM users WHERE user_id=?", (query.from_user.id,))
    if c.fetchone():
        conn.close()
        await query.edit_message_text("У вас уже есть профиль! /search", reply_markup=main_keyboard())
        return ConversationHandler.END
    conn.close()
    await query.edit_message_text("🔞 Введите дату рождения ДД.ММ.ГГГГ:")
    return BIRTH_DATE

async def check_birthdate(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        await update.message.reply_text("❌ Недопустимый текст."); return BIRTH_DATE
    try:
        birth = datetime.strptime(text, "%d.%m.%Y").date()
        today = date.today()
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        if age < 18:
            await update.message.reply_text("🚫 Только 18+"); return ConversationHandler.END
        if age > 120:
            await update.message.reply_text("😅 Введите реальную дату."); return BIRTH_DATE
        context.user_data["birth_date"] = birth.isoformat()
        await update.message.reply_text("Выберите пол:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👨 Мужской", callback_data="gender_male"),
             InlineKeyboardButton("👩 Женский", callback_data="gender_female")]
        ]))
        return GENDER
    except ValueError:
        await update.message.reply_text("❌ Формат ДД.ММ.ГГГГ"); return BIRTH_DATE

async def choose_gender(update, context):
    query = update.callback_query; await query.answer()
    context.user_data["gender"] = query.data.split("_")[1]
    keyboard = [[KeyboardButton("📱 Отправить номер телефона", request_contact=True)]]
    await query.edit_message_text(
        "Для завершения регистрации подтвердите номер телефона.\nНажмите кнопку ниже.",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return PHONE_VERIFY

async def get_phone(update, context):
    contact = update.message.contact
    if not contact:
        await update.message.reply_text("Пожалуйста, используйте кнопку.")
        return PHONE_VERIFY
    context.user_data["phone_hash"] = hashlib.sha256(contact.phone_number.encode()).hexdigest()
    await update.message.reply_text("✅ Номер подтверждён! Теперь укажите ваш город (например, Москва):", reply_markup=ReplyKeyboardRemove())
    return CITY_SELF

async def get_city_self(update, context):
    city = update.message.text.strip()
    if not city:
        await update.message.reply_text("Введите название города.")
        return CITY_SELF
    context.user_data["city"] = city
    context.user_data["search_city"] = city
    await update.message.reply_text(f"🏙 Город {city} сохранён!")
    return await redirect_to_mode(update, context)

async def redirect_to_mode(source, context):
    if isinstance(source, Update):
        msg_func = source.message.reply_text
    else:
        msg_func = source.edit_message_text
    mode = context.user_data.get("mode")
    if mode == "weird":
        await msg_func("🎭 АНТИ‑ТИНДЕР\nВопрос 1/3: Самая странная привычка?")
        return STRANGE_HABIT
    elif mode == "books":
        await msg_func("📚 КНИЖНЫЙ КЛУБ\nНазови 1-2 книги, которые разочаровали:")
        return DISLIKED_BOOKS
    elif mode == "city":
        await msg_func("🗺 В каком городе живёте?")
        return CITY
    elif mode == "micro":
        await msg_func("❓ МИКРО‑ДИАЛОГИ\nВопрос 1/3: Утро или вечер?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🌅 Утро", callback_data="morning"), InlineKeyboardButton("🌙 Вечер", callback_data="evening")]
        ]))
        return MICRO_Q1
    else:
        await msg_func("Неизвестный режим. /start")
        return ConversationHandler.END

async def get_strange_habit(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        admin = await get_admin_id()
        if has_terrorism(text) and admin: await context.bot.send_message(admin, f"🚨 Терроризм от {update.effective_user.id}:\n{text[:200]}")
        await update.message.reply_text("❌ Запрещённые слова."); return STRANGE_HABIT
    context.user_data["strange_habit"] = text
    await update.message.reply_text("🔥 Любимый мем?")
    return FAVORITE_MEME

async def get_admin_id():
    admin = os.environ.get("ADMIN_CHAT_ID")
    return int(admin) if admin else None

async def get_favorite_meme(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        admin = await get_admin_id()
        if has_terrorism(text) and admin: await context.bot.send_message(admin, f"🚨 Терроризм от {update.effective_user.id}:\n{text[:200]}")
        await update.message.reply_text("❌ Недопустимый мем."); return FAVORITE_MEME
    context.user_data["favorite_meme"] = text
    await update.message.reply_text("🤫 Что делаете, когда никто не видит?")
    return SECRET_ACTION

async def get_secret_action(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        admin = await get_admin_id()
        if has_terrorism(text) and admin: await context.bot.send_message(admin, f"🚨 Терроризм от {update.effective_user.id}:\n{text[:200]}")
        await update.message.reply_text("❌ Нельзя."); return SECRET_ACTION
    context.user_data["secret_action"] = text
    user_id = update.effective_user.id
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    trial = ""
    if not is_launch_mode():
        trial = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
    c.execute("""INSERT OR REPLACE INTO users (user_id, username, first_name, mode, birth_date, registered_at, subscription_type, subscription_expiry, gender, phone_hash, city, search_city)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (user_id, update.effective_user.username, update.effective_user.first_name, "weird", context.user_data["birth_date"],
               datetime.now().strftime("%Y-%m-%d %H:%M"), "trial" if trial else None, trial or None,
               context.user_data["gender"], context.user_data["phone_hash"], context.user_data["city"], context.user_data["search_city"]))
    c.execute("INSERT OR REPLACE INTO weird_profiles VALUES (?,?,?,?)", (user_id, context.user_data["strange_habit"], context.user_data["favorite_meme"], context.user_data["secret_action"]))
    conn.commit()
    conn.close()
    await process_referral_bonus(user_id, context)
    await update.message.reply_text("✅ Профиль создан! /search", reply_markup=main_keyboard())
    return ConversationHandler.END

async def get_disliked_books(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        admin = await get_admin_id()
        if has_terrorism(text) and admin: await context.bot.send_message(admin, f"🚨 Терроризм от {update.effective_user.id}:\n{text[:200]}")
        await update.message.reply_text("❌ Запрещённое содержание."); return DISLIKED_BOOKS
    context.user_data["disliked_books"] = text
    await update.message.reply_text("Почему разочаровали?")
    return BOOK_REASON

async def get_book_reason(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        admin = await get_admin_id()
        if has_terrorism(text) and admin: await context.bot.send_message(admin, f"🚨 Терроризм от {update.effective_user.id}:\n{text[:200]}")
        await update.message.reply_text("❌ Нельзя."); return BOOK_REASON
    context.user_data["book_reason"] = text
    user_id = update.effective_user.id
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    trial = ""
    if not is_launch_mode(): trial = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
    c.execute("""INSERT OR REPLACE INTO users (user_id, username, first_name, mode, birth_date, registered_at, subscription_type, subscription_expiry, gender, phone_hash, city, search_city)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (user_id, update.effective_user.username, update.effective_user.first_name, "books", context.user_data["birth_date"],
               datetime.now().strftime("%Y-%m-%d %H:%M"), "trial" if trial else None, trial or None,
               context.user_data["gender"], context.user_data["phone_hash"], context.user_data["city"], context.user_data["search_city"]))
    c.execute("INSERT OR REPLACE INTO book_profiles VALUES (?,?,?)", (user_id, context.user_data["disliked_books"], context.user_data["book_reason"]))
    conn.commit()
    conn.close()
    await process_referral_bonus(user_id, context)
    await update.message.reply_text("✅ Профиль создан! /search", reply_markup=main_keyboard())
    return ConversationHandler.END

async def get_city(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        admin = await get_admin_id()
        if has_terrorism(text) and admin: await context.bot.send_message(admin, f"🚨 Терроризм от {update.effective_user.id}:\n{text[:200]}")
        await update.message.reply_text("❌ Запрещённый город."); return CITY
    context.user_data["city"] = text
    await update.message.reply_text("Место, где заряжаетесь энергией?")
    return ENERGY_PLACE

async def get_energy_place(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        admin = await get_admin_id()
        if has_terrorism(text) and admin: await context.bot.send_message(admin, f"🚨 Терроризм от {update.effective_user.id}:\n{text[:200]}")
        await update.message.reply_text("❌ Нельзя."); return ENERGY_PLACE
    context.user_data["energy_place"] = text
    await update.message.reply_text("Место, которое бесит?")
    return HATE_PLACE

async def get_hate_place(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        admin = await get_admin_id()
        if has_terrorism(text) and admin: await context.bot.send_message(admin, f"🚨 Терроризм от {update.effective_user.id}:\n{text[:200]}")
        await update.message.reply_text("❌ Нельзя."); return HATE_PLACE
    context.user_data["hate_place"] = text
    await update.message.reply_text("Куда хотите сходить?")
    return WANT_PLACE

async def get_want_place(update, context):
    text = update.message.text.strip()
    safe, words = is_safe_text(text)
    log_message(update.effective_user.id, update.effective_chat.id, text, not safe, ", ".join(words))
    if not safe:
        admin = await get_admin_id()
        if has_terrorism(text) and admin: await context.bot.send_message(admin, f"🚨 Терроризм от {update.effective_user.id}:\n{text[:200]}")
        await update.message.reply_text("❌ Нельзя."); return WANT_PLACE
    context.user_data["want_place"] = text
    user_id = update.effective_user.id
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    trial = ""
    if not is_launch_mode(): trial = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
    c.execute("""INSERT OR REPLACE INTO users (user_id, username, first_name, mode, birth_date, registered_at, subscription_type, subscription_expiry, gender, phone_hash, city, search_city)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (user_id, update.effective_user.username, update.effective_user.first_name, "city", context.user_data["birth_date"],
               datetime.now().strftime("%Y-%m-%d %H:%M"), "trial" if trial else None, trial or None,
               context.user_data["gender"], context.user_data["phone_hash"], context.user_data["city"], context.user_data["search_city"]))
    c.execute("INSERT OR REPLACE INTO city_profiles VALUES (?,?,?,?,?)", (user_id, context.user_data["city"], context.user_data["energy_place"], context.user_data["hate_place"], context.user_data["want_place"]))
    conn.commit()
    conn.close()
    await process_referral_bonus(user_id, context)
    await update.message.reply_text("✅ Профиль создан! /search", reply_markup=main_keyboard())
    return ConversationHandler.END

async def micro_q1(update, context):
    query = update.callback_query; await query.answer()
    context.user_data["answer_1"] = query.data
    await query.edit_message_text("Вопрос 2/3: Кофе или чай?", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("☕ Кофе", callback_data="coffee"), InlineKeyboardButton("🍵 Чай", callback_data="tea")]
    ]))
    return MICRO_Q2

async def micro_q2(update, context):
    query = update.callback_query; await query.answer()
    context.user_data["answer_2"] = query.data
    await query.edit_message_text("Вопрос 3/3: Горы или море?", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🏔 Горы", callback_data="mountains"), InlineKeyboardButton("🌊 Море", callback_data="sea")]
    ]))
    return MICRO_Q3

async def micro_q3(update, context):
    query = update.callback_query; await query.answer()
    context.user_data["answer_3"] = query.data
    user_id = query.from_user.id
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    trial = ""
    if not is_launch_mode(): trial = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
    c.execute("""INSERT OR REPLACE INTO users (user_id, username, first_name, mode, birth_date, registered_at, subscription_type, subscription_expiry, gender, phone_hash, city, search_city)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (user_id, query.from_user.username, query.from_user.first_name, "micro", context.user_data["birth_date"],
               datetime.now().strftime("%Y-%m-%d %H:%M"), "trial" if trial else None, trial or None,
               context.user_data["gender"], context.user_data["phone_hash"], context.user_data["city"], context.user_data["search_city"]))
    c.execute("INSERT OR REPLACE INTO micro_profiles VALUES (?,?,?,?)", (user_id, context.user_data["answer_1"], context.user_data["answer_2"], context.user_data["answer_3"]))
    conn.commit()
    conn.close()
    await process_referral_bonus(user_id, context)
    await query.edit_message_text("✅ Профиль создан! /search", reply_markup=main_keyboard())
    return ConversationHandler.END

# ---------- Поиск ----------
def find_next_profile(user_id, mode, search_city=None):
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT to_user FROM likes WHERE from_user=? AND mode=?", (user_id, mode))
    viewed = [r[0] for r in c.fetchall()] + [user_id]
    ph = ",".join("?"*len(viewed))
    cols, table = {
        "weird": ("w.strange_habit, w.favorite_meme, w.secret_action", "weird_profiles w"),
        "books": ("b.disliked_books, b.book_reason", "book_profiles b"),
        "city": ("c.city, c.energy_place, c.hate_place, c.want_place", "city_profiles c"),
        "micro": ("m.answer_1, m.answer_2, m.answer_3", "micro_profiles m")
    }[mode]
    query = f"SELECT u.user_id, u.first_name, {cols} FROM users u JOIN {table} ON u.user_id = {table.split()[0]}.user_id WHERE u.mode=? AND u.user_id NOT IN ({ph}) AND u.user_id NOT IN (SELECT user_id FROM banned_users) AND (u.user_id NOT IN (SELECT user_id FROM ai_profiles) OR u.user_id IN (SELECT user_id FROM ai_profiles WHERE active=1))"
    params = [mode] + viewed
    if search_city:
        query += " AND (u.search_city = ? OR u.search_city = '' OR u.city = ?) "
        params.extend([search_city, search_city])
    query += " ORDER BY RANDOM() LIMIT 1"
    c.execute(query, params)
    res = c.fetchone()
    conn.close()
    return res

async def search(update, context):
    user_id = update.effective_user.id
    if is_banned(user_id): await update.message.reply_text("⛔ Вы заблокированы."); return
    if not can_like(user_id): await update.message.reply_text("⚠️ Дневной лимит лайков исчерпан."); return
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT mode, search_city FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    if not user: await update.message.reply_text("Сначала создайте профиль: /start"); return
    mode, search_city = user
    profile = find_next_profile(user_id, mode, search_city)
    if not profile: await update.message.reply_text("😅 Нет анкет в вашем городе."); return
    context.user_data["current_profile_id"] = profile[0]
    if mode == "weird": text = f"🎭 АНКЕТА\nИмя: {profile[1]}\n🤪 Привычка: {profile[2]}\n🔥 Мем: {profile[3]}\n🤫 Секрет: {profile[4]}"
    elif mode == "books": text = f"📚 АНКЕТА\nИмя: {profile[1]}\n📕 Книги: {profile[2]}\n💭 Причина: {profile[3]}"
    elif mode == "city": text = f"🗺 АНКЕТА\nИмя: {profile[1]}\nГород: {profile[2]}\n⚡: {profile[3]}\n😤: {profile[4]}\n🌟: {profile[5]}"
    else:
        answers = {"morning":"🌅 Утро","evening":"🌙 Вечер","coffee":"☕ Кофе","tea":"🍵 Чай","mountains":"🏔 Горы","sea":"🌊 Море"}
        text = f"❓ АНКЕТА\nИмя: {profile[1]}\n• {answers.get(profile[2], profile[2])}\n• {answers.get(profile[3], profile[3])}\n• {answers.get(profile[4], profile[4])}"
    if profile[0] >= AI_START_RANGE:
        await update.message.reply_text(f"🤖 {text}", reply_markup=search_keyboard(profile[0]))
    else:
        await update.message.reply_text(text, reply_markup=search_keyboard(profile[0]))

async def handle_like(update, context):
    query = update.callback_query; await query.answer()
    from_user = query.from_user.id
    if is_banned(from_user): return
    to_user = int(query.data.split("_")[1])
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT mode FROM users WHERE user_id=?", (from_user,))
    mode = c.fetchone()[0] if c.fetchone() else "micro"
    c.execute("INSERT OR IGNORE INTO likes VALUES (?,?,?,?)", (from_user, to_user, mode, datetime.now().strftime("%Y-%m-%d %H:%M")))
    c.execute("SELECT * FROM likes WHERE from_user=? AND to_user=? AND mode=?", (to_user, from_user, mode))
    if c.fetchone():
        c.execute("INSERT OR IGNORE INTO matches VALUES (?,?,?,?)", (min(from_user, to_user), max(from_user, to_user), mode, datetime.now().strftime("%Y-%m-%d %H:%M")))
        c.execute("SELECT first_name, username FROM users WHERE user_id=?", (to_user,))
        u2 = c.fetchone()
        await query.message.reply_text(f"💕 МЭТЧ! Привет, {u2[0] if u2 else 'собеседник'}!")
    conn.commit()
    conn.close()
    profile = find_next_profile(from_user, mode)
    if profile:
        context.user_data["current_profile_id"] = profile[0]
        await search(update, context)
    else:
        await query.message.reply_text("✅ Анкеты закончились.", reply_markup=main_keyboard())

async def handle_dislike(update, context):
    query = update.callback_query; await query.answer()
    from_user = query.from_user.id
    if is_banned(from_user): return
    to_user = int(query.data.split("_")[1])
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT mode FROM users WHERE user_id=?", (from_user,))
    mode = c.fetchone()[0] if c.fetchone() else "micro"
    c.execute("INSERT OR IGNORE INTO likes VALUES (?,?,?,?)", (from_user, to_user, mode, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()
    profile = find_next_profile(from_user, mode)
    if profile:
        context.user_data["current_profile_id"] = profile[0]
        await search(update, context)
    else:
        await query.message.reply_text("✅ Анкеты закончились.", reply_markup=main_keyboard())

async def handle_report(update, context):
    query = update.callback_query; await query.answer()
    from_user = query.from_user.id
    if is_banned(from_user): return
    about_user = int(query.data.split("_")[1])
    if about_user >= AI_START_RANGE:
        await query.answer("Нельзя жаловаться на AI 🤖")
        return
    add_complaint(from_user, about_user)
    await query.answer("Жалоба отправлена.")
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT mode FROM users WHERE user_id=?", (from_user,))
    mode = c.fetchone()[0] if c.fetchone() else "micro"
    conn.close()
    profile = find_next_profile(from_user, mode)
    if profile:
        context.user_data["current_profile_id"] = profile[0]
        await search(update, context)
    else:
        await query.message.reply_text("✅ Анкеты закончились.", reply_markup=main_keyboard())

async def stop_search(update, context):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("Поиск остановлен.", reply_markup=main_keyboard())

# ---------- Профиль, мэтчи, магазин, приглашения ----------
async def show_profile(update, context):
    query = update.callback_query
    if query: await query.answer(); user_id = query.from_user.id
    else: user_id = update.effective_user.id
    if is_banned(user_id): await update.message.reply_text("⛔ Вы заблокированы."); return
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT mode, coins, subscription_type, subscription_expiry, gender, city, search_city FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row: await update.message.reply_text("Профиль не найден."); conn.close(); return
    mode, coins, sub_type, sub_exp, gender, city, search_city = row
    text = f"👤 Профиль\n"
    if gender: text += f"Пол: {'👨' if gender=='male' else '👩'}\n"
    text += f"🏙 Город: {city}\n🔍 Город поиска: {search_city}\n💰 Коины: {coins}\n"
    if sub_type and sub_exp > datetime.now().strftime("%Y-%m-%d %H:%M"):
        text += f"⭐ Подписка: {sub_type} (до {sub_exp})\n"
    else: text += "❌ Нет активной подписки\n"
    if mode == "weird":
        c.execute("SELECT * FROM weird_profiles WHERE user_id=?", (user_id,))
        data = c.fetchone()
        if data: text += f"🎭 Анти‑тиндер\n🤪 {data[1]}\n🔥 {data[2]}\n🤫 {data[3]}"
    elif mode == "books":
        c.execute("SELECT * FROM book_profiles WHERE user_id=?", (user_id,))
        data = c.fetchone()
        if data: text += f"📚 Книжный клуб\n📕 {data[1]}\n💭 {data[2]}"
    elif mode == "city":
        c.execute("SELECT * FROM city_profiles WHERE user_id=?", (user_id,))
        data = c.fetchone()
        if data: text += f"🗺 Городские истории\n🏙 {data[1]}\n⚡ {data[2]}\n😤 {data[3]}\n🌟 {data[4]}"
    else:
        c.execute("SELECT * FROM micro_profiles WHERE user_id=?", (user_id,))
        data = c.fetchone()
        if data:
            answers = {"morning":"🌅 Утро","evening":"🌙 Вечер","coffee":"☕ Кофе","tea":"🍵 Чай","mountains":"🏔 Горы","sea":"🌊 Море"}
            text += f"❓ Микро‑диалоги\n• {answers.get(data[1], data[1])}\n• {answers.get(data[2], data[2])}\n• {answers.get(data[3], data[3])}"
    c.execute("SELECT flag, details FROM moderation_flags WHERE user_id=?", (user_id,))
    flag = c.fetchone()
    if flag: text += f"\n⚠️ Статус: {flag[0]} ({flag[1]})"
    conn.close()
    await update.message.reply_text(text, reply_markup=main_keyboard())

async def show_matches(update, context):
    query = update.callback_query
    if query: await query.answer(); user_id = query.from_user.id
    else: user_id = update.effective_user.id
    if is_banned(user_id): await update.message.reply_text("⛔ Вы заблокированы."); return
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("""SELECT CASE WHEN user1=? THEN user2 ELSE user1 END, mode FROM matches WHERE user1=? OR user2=? ORDER BY match_date DESC""", (user_id, user_id, user_id))
    matches = c.fetchall()
    if not matches: text = "😔 Пока нет мэтчей."
    else:
        mode_emoji = {"weird":"🎭","books":"📚","city":"🗺","micro":"❓"}
        text = "💕 Мэтчи:\n\n"
        for m in matches:
            c.execute("SELECT first_name, username FROM users WHERE user_id=?", (m[0],))
            u = c.fetchone()
            if u: text += f"{mode_emoji.get(m[1],'💕')} {u[0]} (@{u[1] if u[1] else '—'})\n"
    conn.close()
    await update.message.reply_text(text, reply_markup=main_keyboard())

async def shop(update, context):
    if is_launch_mode():
        await update.message.reply_text(
            "🛍 Магазин откроется, когда в боте наберётся 500 активных анкет.\n"
            "Но вы как ранний пользователь уже получили премиум — для вас ничего не изменится!"
        )
    else:
        keyboard = [
            [InlineKeyboardButton("💎 Дневная подписка (79₽)", callback_data="buy_sub_day")],
            [InlineKeyboardButton("💎 Недельная подписка (199₽)", callback_data="buy_sub_week")],
            [InlineKeyboardButton("💎 Месячная подписка (399₽)", callback_data="buy_sub_month")],
            [InlineKeyboardButton("🎭 Странный вечер (99₽)", callback_data="buy_pack_weird_evening")],
            [InlineKeyboardButton("📚 Книжный баттл (129₽)", callback_data="buy_pack_book_battle")],
            [InlineKeyboardButton("🗺 Маршрут выходного дня (159₽)", callback_data="buy_pack_weekend_route")],
            [InlineKeyboardButton("❓ Вечер вопросов (79₽)", callback_data="buy_pack_evening_questions")],
            [InlineKeyboardButton("👤 Мой баланс", callback_data="my_balance")],
        ]
        await update.message.reply_text("🛒 Магазин улучшений:", reply_markup=InlineKeyboardMarkup(keyboard))

async def my_balance(update, context):
    user_id = update.effective_user.id
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT coins, subscription_type, subscription_expiry FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        coins, sub_type, sub_exp = row
        text = f"💰 Коины: {coins}\n"
        if sub_type and sub_exp > datetime.now().strftime("%Y-%m-%d %H:%M"):
            text += f"⭐ Подписка: {sub_type} (до {sub_exp})"
        else: text += "❌ Нет подписки"
        await update.message.reply_text(text)
    else: await update.message.reply_text("Профиль не найден.")

async def buy_subscription(update, context):
    query = update.callback_query; await query.answer()
    sub_type = query.data.split("_")[2]
    prices = {"day": 79, "week": 199, "month": 399}
    await context.bot.send_invoice(chat_id=query.from_user.id, title=f"Подписка {sub_type}",
                                   description=f"Премиум на {sub_type}", payload=f"sub_{sub_type}",
                                   provider_token="", currency="XTR",
                                   prices=[{"label": sub_type, "amount": prices[sub_type]}],
                                   start_parameter="subscription")

async def buy_pack(update, context):
    query = update.callback_query; await query.answer()
    pack_key = query.data.split("_", 2)[2]
    prices = {"weird_evening": 99, "book_battle": 129, "weekend_route": 159, "evening_questions": 79}
    await context.bot.send_invoice(chat_id=query.from_user.id, title=f"Пакет {pack_key}",
                                   description="Специальное предложение", payload=f"pack_{pack_key}",
                                   provider_token="", currency="XTR",
                                   prices=[{"label": pack_key, "amount": prices[pack_key]}],
                                   start_parameter="pack")

async def precheckout(update, context): await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update, context):
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload
    if payload.startswith("sub_"):
        duration = payload[4:]
        if duration == "day":
            expiry = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
            sub_type = "day"; coins_gift = 50
        elif duration == "week":
            expiry = (datetime.now() + timedelta(weeks=1)).strftime("%Y-%m-%d %H:%M")
            sub_type = "week"; coins_gift = 150
        elif duration == "month":
            expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
            sub_type = "month"; coins_gift = 300
        else: return
        conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
        c = conn.cursor()
        c.execute("UPDATE users SET subscription_type=?, subscription_expiry=?, coins = coins + ? WHERE user_id=?",
                  (sub_type, expiry, coins_gift, user_id))
        conn.commit(); conn.close()
        await update.message.reply_text(f"✅ Подписка '{sub_type}' активирована!\n🎁 +{coins_gift} коинов.")
    elif payload.startswith("pack_"):
        pack_name = payload[5:]
        if pack_name == "weird_evening": expiry = (datetime.now()+timedelta(hours=3)).strftime("%Y-%m-%d %H:%M"); uses = 3
        elif pack_name == "book_battle": expiry = (datetime.now()+timedelta(hours=12)).strftime("%Y-%m-%d %H:%M"); uses = 5
        elif pack_name == "weekend_route": expiry = (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d %H:%M"); uses = None
        elif pack_name == "evening_questions": expiry = (datetime.now()+timedelta(hours=3)).strftime("%Y-%m-%d %H:%M"); uses = 3
        else: return
        conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO active_packs VALUES (?, ?, ?, ?)", (user_id, pack_name, expiry, uses))
        conn.commit(); conn.close()
        await update.message.reply_text(f"✅ Пакет '{pack_name}' активирован!")

async def invite(update, context):
    user_id = update.effective_user.id
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND bonus_given=1", (user_id,))
    cnt = c.fetchone()[0]
    conn.close()
    link = get_referral_link(user_id)
    await update.message.reply_text(f"🔗 Ваша ссылка: {link}\nПриглашено: {cnt}")

async def appeal_start(update, context):
    if not is_banned(update.effective_user.id):
        await update.message.reply_text("Ваш аккаунт не заблокирован."); return ConversationHandler.END
    await update.message.reply_text("Опишите ситуацию (можно фото).")
    return APPEAL_TEXT

async def receive_appeal(update, context):
    user_id = update.effective_user.id
    if update.message.photo: text = "[Фото] " + (update.message.caption or "")
    else: text = update.message.text
    log_message(user_id, update.effective_chat.id, text, False, "апелляция")
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("INSERT INTO appeals (user_id, text, created_at) VALUES (?,?,?)", (user_id, text, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit(); conn.close()
    await update.message.reply_text("✅ Апелляция принята.")
    return ConversationHandler.END

async def set_city(update, context):
    user_id = update.effective_user.id
    if not context.args: await update.message.reply_text("Использование: /setcity Москва"); return
    new_city = " ".join(context.args).strip()
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("UPDATE users SET search_city=? WHERE user_id=?", (new_city, user_id))
    conn.commit(); conn.close()
    await update.message.reply_text(f"✅ Город поиска изменён на {new_city}")

async def delete_account(update, context):
    user_id = update.effective_user.id
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    tables = ["users","weird_profiles","book_profiles","city_profiles","micro_profiles","likes","matches","referrals","ai_conversations","ai_daily_limit","message_log","active_packs","complaints","appeals","moderation_flags"]
    for t in tables: c.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))
    c.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()
    await update.message.reply_text("♻️ Аккаунт и данные удалены.")

async def privacy(update, context):
    await update.message.reply_text("Политика конфиденциальности: ... (текст документа).")

async def admin_ban(update, context):
    admin = await get_admin_id()
    if not admin or update.effective_user.id != admin: return
    try:
        target = int(context.args[0]); reason = " ".join(context.args[1:]) or "Нарушение"
        ban_user(target, reason); await update.message.reply_text("✅ Забанен.")
    except: pass

async def admin_review(update, context):
    admin = await get_admin_id()
    if not admin or update.effective_user.id != admin: return
    try: target = int(context.args[0])
    except: await update.message.reply_text("/review <id>"); return
    conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
    c = conn.cursor()
    c.execute("SELECT from_user, created_at FROM complaints WHERE about_user=?", (target,))
    complaints = c.fetchall()
    c.execute("SELECT text, created_at FROM appeals WHERE user_id=?", (target,))
    appeals = c.fetchall()
    c.execute("SELECT message_text, filter_triggered, filter_reason, timestamp FROM message_log WHERE user_id=? ORDER BY timestamp DESC LIMIT 10", (target,))
    messages = c.fetchall()
    text = f"📋 Обзор {target}\nЖалоб: {len(complaints)}\nАпелляций: {len(appeals)}\nСообщения:\n"
    for m in messages: text += f"{'🚫' if m[1] else '✅'} {m[3]}: {m[0][:100]}\n"
    conn.close()
    await update.message.reply_text(text)

async def ai_manage_cmd(update, context):
    admin = await get_admin_id()
    if not admin or update.effective_user.id != admin: return
    text = "📊 Статистика AI:\n"
    for mode in MIN_PROFILES_PER_MODE:
        real = count_real_users_in_mode(mode)
        ai = count_active_ai_in_mode(mode)
        text += f"{mode}: реал {real}, AI {ai}\n"
    keyboard = [
        [InlineKeyboardButton("➕ Добавить 10 AI в каждый режим", callback_data="ai_add_10_each")],
        [InlineKeyboardButton("➖ Удалить 10 AI из каждого режима", callback_data="ai_remove_10_each")],
        [InlineKeyboardButton("🔄 Авто‑пересчёт", callback_data="ai_auto")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def ai_button(update, context):
    query = update.callback_query; await query.answer()
    admin = await get_admin_id()
    if not admin or query.from_user.id != admin: return
    data = query.data
    if data == "ai_auto":
        manage_all_ai()
        await query.edit_message_text("✅ Авто‑пересчёт выполнен.")
    elif data == "ai_add_10_each":
        conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
        c = conn.cursor()
        for mode in MIN_PROFILES_PER_MODE:
            for _ in range(10): create_single_ai(c, mode)
        conn.commit(); conn.close()
        await query.edit_message_text("✅ Добавлено по 10 AI в каждый режим.")
    elif data == "ai_remove_10_each":
        conn = sqlite3.connect("/data/dating_bot.db")  # ← ИСПРАВЛЕНО
        c = conn.cursor()
        for mode in MIN_PROFILES_PER_MODE:
            c.execute("UPDATE ai_profiles SET active=0 WHERE user_id IN (SELECT user_id FROM ai_profiles WHERE mode=? AND active=1 LIMIT 10)", (mode,))
        conn.commit(); conn.close()
        await query.edit_message_text("✅ Деактивировано по 10 AI в каждом режиме.")

async def cancel(update, context):
    await update.message.reply_text("❌ Отменено."); return ConversationHandler.END

# ---------- ЗАПУСК ----------
def main():
    TOKEN = os.environ.get("BOT_TOKEN")
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN not set")

    app = Application.builder().token(TOKEN).build()

    # Планировщик и AI
    scheduler = AsyncIOScheduler()
    scheduler.add_job(manage_all_ai, 'interval', hours=1)
    scheduler.add_job(lambda: ai_like_random_users(app), 'interval', hours=1)
    scheduler.start()

    # ConversationHandler регистрации
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(choose_mode, pattern="^mode_")],
        states={
            BIRTH_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_birthdate)],
            GENDER: [CallbackQueryHandler(choose_gender, pattern="^gender_")],
            PHONE_VERIFY: [MessageHandler(filters.CONTACT, get_phone)],
            CITY_SELF: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_city_self)],
            STRANGE_HABIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_strange_habit)],
            FAVORITE_MEME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_favorite_meme)],
            SECRET_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_secret_action)],
            DISLIKED_BOOKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_disliked_books)],
            BOOK_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_book_reason)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_city)],
            ENERGY_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_energy_place)],
            HATE_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_hate_place)],
            WANT_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_want_place)],
            MICRO_Q1: [CallbackQueryHandler(micro_q1, pattern="^(morning|evening)$")],
            MICRO_Q2: [CallbackQueryHandler(micro_q2, pattern="^(coffee|tea)$")],
            MICRO_Q3: [CallbackQueryHandler(micro_q3, pattern="^(mountains|sea)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    appeal_conv = ConversationHandler(
        entry_points=[CommandHandler("appeal", appeal_start)],
        states={APPEAL_TEXT: [MessageHandler(filters.TEXT | filters.PHOTO, receive_appeal)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", lambda u,c: u.message.reply_text("/start /search /matches /profile /invite /shop /balance /setcity /delete /privacy /appeal")))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("matches", show_matches))
    app.add_handler(CommandHandler("profile", show_profile))
    app.add_handler(CommandHandler("invite", invite))
    app.add_handler(CommandHandler("shop", shop))
    app.add_handler(CommandHandler("balance", my_balance))
    app.add_handler(CommandHandler("setcity", set_city))
    app.add_handler(CommandHandler("delete", delete_account))
    app.add_handler(CommandHandler("privacy", privacy))
    app.add_handler(CommandHandler("ban", admin_ban))
    app.add_handler(CommandHandler("review", admin_review))
    app.add_handler(CommandHandler("ai_manage", ai_manage_cmd))
    app.add_handler(conv_handler)
    app.add_handler(appeal_conv)
    app.add_handler(CallbackQueryHandler(handle_like, pattern="^like_"))
    app.add_handler(CallbackQueryHandler(handle_dislike, pattern="^dislike_"))
    app.add_handler(CallbackQueryHandler(handle_report, pattern="^report_"))
    app.add_handler(CallbackQueryHandler(stop_search, pattern="^stop_search$"))
    app.add_handler(CallbackQueryHandler(show_profile, pattern="^my_profile$"))
    app.add_handler(CallbackQueryHandler(show_matches, pattern="^my_matches$"))
    app.add_handler(CallbackQueryHandler(invite, pattern="^invite$"))
    app.add_handler(CallbackQueryHandler(shop, pattern="^shop$"))
    app.add_handler(CallbackQueryHandler(my_balance, pattern="^my_balance$"))
    app.add_handler(CallbackQueryHandler(buy_subscription, pattern="^buy_sub_"))
    app.add_handler(CallbackQueryHandler(buy_pack, pattern="^buy_pack_"))
    app.add_handler(CallbackQueryHandler(ai_button, pattern="^ai_"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    # AI‑диалоги (после всех основных обработчиков)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_conversation), group=1)

    # Запуск: webhook, если задан порт (Render), иначе polling
    port = os.environ.get("PORT")
    if port:
        render_external_url = os.environ.get("RENDER_EXTERNAL_URL", "")
        if not render_external_url:
            service_name = os.environ.get("RENDER_SERVICE_NAME", "dating-bot")
            render_external_url = f"https://{service_name}.onrender.com"
        webhook_url = f"{render_external_url}/webhook"
        app.run_webhook(
            listen="0.0.0.0",
            port=int(port),
            webhook_url=webhook_url,
            drop_pending_updates=True
        )
        print(f"🤖 Бот запущен через webhook на порту {port}")
    else:
        manage_all_ai()
        app.run_polling(drop_pending_updates=True)
        print("🤖 Бот запущен локально через polling")

if __name__ == "__main__":
    main()
