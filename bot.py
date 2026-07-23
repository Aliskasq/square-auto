"""Telegram bot — commands + group listener."""
import asyncio
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters,
)
import httpx
from config import (
    ADMIN_ID, SOURCE_GROUP_ID, SOURCE_GROUP_2_ID,
    get, set_val, get_settings, TG_BOT_TOKEN,
)
from core.listener import parse_push_message
from core import queue_manager as qm
from core.pipeline import process_ticker
from core.binance_api import is_futures_symbol

logger = logging.getLogger(__name__)

# Lock for sequential AI processing
_processing_lock = asyncio.Lock()
_processing_queue: asyncio.Queue = asyncio.Queue()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


# --- Commands ---

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "🟧 Square Auto Bot\n\n"
        "/status — статус\n"
        "/models — список/смена моделей\n"
        "/sleep ЧЧ:ММ-ЧЧ:ММ — время сна (МСК)\n"
        "/pause N — пауза между постами (мин)\n"
        "/hashtags текст — хэштеги\n"
        "/queue — очередь\n"
        "/counters — счётчики\n"
        "/clear — очистить очередь\n"
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    s = get_settings()
    models = s.get("models", [])
    sleep_str = f"{s.get('sleep_start', '01:00')}-{s.get('sleep_end', '05:00')} МСК"
    sleeping = "💤 СПИТ" if qm.is_sleep_time() else "✅ Активен"
    counters = qm.get_counters_info()
    queue = qm.get_queue()

    await update.message.reply_text(
        f"📊 Статус: {sleeping}\n\n"
        f"🤖 Модели ({len(models)}):\n" +
        "\n".join(f"  {i+1}. {m}" for i, m in enumerate(models)) + "\n\n"
        f"💤 Сон: {sleep_str}\n"
        f"⏸ Пауза: {s.get('pause_minutes', 6)} мин\n"
        f"📊 {counters}\n"
        f"📋 Очередь: {len(queue)} монет\n"
        f"🏷 {s.get('hashtags', '')}\n"
    )


async def cmd_sleep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        return await update.message.reply_text(
            f"💤 Сон: {get('sleep_start')}-{get('sleep_end')} МСК\n"
            f"Изменить: /sleep 01:00-05:00"
        )
    arg = " ".join(ctx.args).strip()
    match = re.match(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})", arg)
    if not match:
        return await update.message.reply_text("Формат: /sleep 01:00-05:00")
    set_val("sleep_start", match.group(1))
    set_val("sleep_end", match.group(2))
    await update.message.reply_text(f"✅ Сон: {match.group(1)}-{match.group(2)} МСК")


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        return await update.message.reply_text(f"⏸ Пауза: {get('pause_minutes')} мин\nИзменить: /pause 6")
    try:
        minutes = int(ctx.args[0])
        set_val("pause_minutes", max(1, minutes))
        await update.message.reply_text(f"✅ Пауза: {minutes} мин")
    except ValueError:
        await update.message.reply_text("Число!")


async def cmd_hashtags(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        return await update.message.reply_text(f"🏷 {get('hashtags')}\nИзменить: /hashtags #tag1 #tag2")
    set_val("hashtags", " ".join(ctx.args))
    await update.message.reply_text(f"✅ {get('hashtags')}")


async def cmd_queue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    queue = qm.get_queue()
    if not queue:
        return await update.message.reply_text("📋 Очередь пуста")
    text = "📋 Очередь:\n\n"
    for i, item in enumerate(queue[:20], 1):
        text += f"{i}. ${item['ticker']} @ {item['price']:.6f} ({item.get('sector', '')})\n"
    await update.message.reply_text(text)


async def cmd_counters(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(f"📊 {qm.get_counters_info()}")


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    qm.clear_queue()
    await update.message.reply_text("🗑 Очередь очищена")


# --- Models ---

async def _fetch_free_models() -> list[dict]:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://openrouter.ai/api/v1/models", timeout=15)
            r.raise_for_status()
            data = r.json()
        free = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {})
            pi = float(pricing.get("prompt", "1") or "1")
            po = float(pricing.get("completion", "1") or "1")
            if pi == 0 and po == 0:
                free.append({"id": m["id"], "name": m.get("name", m["id"])})
        free.sort(key=lambda x: x["name"].lower())
        return free
    except Exception as e:
        logger.error(f"Fetch models error: {e}")
        return []


async def cmd_models(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    current = get("models") or []

    if ctx.args:
        if ctx.args[0].lower() == "clear":
            set_val("models", [])
            return await update.message.reply_text("🗑 Модели очищены")
        # Add model (max 8)
        model_name = " ".join(ctx.args).strip()
        if model_name not in current:
            if len(current) >= 8:
                return await update.message.reply_text("⚠️ Максимум 8 моделей! Используй /models clear")
            current.append(model_name)
            set_val("models", current)
        return await update.message.reply_text(
            f"✅ Модели ({len(current)}/8):\n" +
            "\n".join(f"  {i+1}. {m}" for i, m in enumerate(current))
        )

    msg = await update.message.reply_text("⏳ Загружаю модели...")
    free = await _fetch_free_models()
    if not free:
        return await msg.edit_text("❌ Не удалось загрузить")

    buttons = []
    row = []
    for m in free:
        short = m["id"].replace(":free", "").split("/")[-1]
        marker = "✅ " if m["id"] in current else ""
        label = f"{marker}{short}"
        cb_data = f"addm:{m['id']}"
        if len(cb_data.encode()) > 64:
            cb_data = cb_data[:64]
        row.append(InlineKeyboardButton(label, callback_data=cb_data))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    text = (
        f"🤖 Активные ({len(current)}):\n" +
        "\n".join(f"  {i+1}. {m}" for i, m in enumerate(current)) +
        f"\n\nНажми чтобы добавить/удалить 👇"
    )
    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def callback_model_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔")
        return

    model_id = query.data.split(":", 1)[1]
    current = get("models") or []

    if model_id in current:
        current.remove(model_id)
        await query.answer(f"❌ Удалён")
    else:
        if len(current) >= 8:
            await query.answer(f"⚠️ Максимум 8 моделей!")
            return
        current.append(model_id)
        await query.answer(f"✅ Добавлен ({len(current)}/8)")

    set_val("models", current)

    # Update buttons
    old_markup = query.message.reply_markup
    if old_markup:
        new_buttons = []
        for brow in old_markup.inline_keyboard:
            new_row = []
            for btn in brow:
                cb = btn.callback_data or ""
                if cb.startswith("addm:"):
                    btn_model = cb.split(":", 1)[1]
                    short = btn_model.replace(":free", "").split("/")[-1]
                    marker = "✅ " if btn_model in current else ""
                    new_row.append(InlineKeyboardButton(f"{marker}{short}", callback_data=cb))
                else:
                    new_row.append(btn)
            new_buttons.append(new_row)

        text = (
            f"🤖 Активные ({len(current)}):\n" +
            "\n".join(f"  {i+1}. {m}" for i, m in enumerate(current)) +
            f"\n\nНажми чтобы добавить/удалить 👇"
        )
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(new_buttons))
        except Exception:
            pass


# --- Group message handler ---

async def handle_group_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle messages from source groups."""
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    text = update.message.text

    # Determine source
    if chat_id == SOURCE_GROUP_ID:
        source = "group1"
    elif chat_id == SOURCE_GROUP_2_ID:
        source = "group2"
    else:
        return

    parsed = parse_push_message(text)
    if not parsed:
        return

    ticker = parsed["ticker"]
    price = parsed["price"]
    sector = parsed["sector"]

    logger.info(f"📨 Push from {source}: ${ticker} @ {price} ({sector})")

    # Group 2 — just log
    if source == "group2":
        qm.add_group2_ticker(ticker, price, sector)
        return

    # Group 1 — main source
    # Check if futures
    symbol = ticker + "USDT" if not ticker.endswith("USDT") else ticker
    if not await is_futures_symbol(symbol):
        logger.info(f"⏭ {ticker} not on futures, skipping")
        return

    # Check dedup
    if qm.is_recently_posted(ticker):
        logger.info(f"⏭ {ticker} already posted recently")
        return

    # Check sleep
    if qm.is_sleep_time():
        qm.add_to_queue(ticker, price, sector, "sleep")
        return

    # Check limits
    can_post, reason = qm.can_post_now()
    if not can_post:
        qm.add_to_queue(ticker, price, sector, f"overflow:{reason}")
        if "daily" in reason:
            # Send daily limit alert
            try:
                from telegram import Bot
                bot = Bot(TG_BOT_TOKEN)
                await bot.send_message(ADMIN_ID, f"🚫 Дневной лимит постов исчерпан! ({get('posts_per_day')})")
            except Exception:
                pass
        return

    # Process immediately (sequential)
    await _processing_queue.put((ticker, price, sector, "group1"))


async def _notify_admin(text: str):
    """Send notification to admin via TG."""
    try:
        from telegram import Bot
        bot = Bot(TG_BOT_TOKEN)
        await bot.send_message(ADMIN_ID, text)
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")


# Track daily limit alert to avoid spam
_daily_limit_alerted = {"day_key": "", "alerted": False}


async def processing_worker(app: Application):
    """Background worker — processes tickers sequentially with pause between posts.
    
    Logic:
    1. Wake from sleep → filter queue (top 5 growers)
    2. Process overflow/sleep queue first
    3. If queue empty and hour has free slots → backfill from group 2 (top by growth)
    4. Track pace: 5/hour, ~98/day minimum
    """
    logger.info("🔄 Processing worker started")

    _was_sleeping = qm.is_sleep_time()
    _posts_group1 = 0
    _posts_group2 = 0
    _sleep_report_sent = False

    while True:
        try:
            # Detect transition to sleep → send daily report
            is_sleeping = qm.is_sleep_time()
            if not _was_sleeping and is_sleeping and not _sleep_report_sent:
                total = _posts_group1 + _posts_group2
                counters_info = qm.get_counters_info()
                await _notify_admin(
                    f"😴 Ушёл спать\n\n"
                    f"📊 Итог за день:\n"
                    f"  Группа 1: {_posts_group1} постов\n"
                    f"  Группа 2: {_posts_group2} постов\n"
                    f"  Всего: {total}\n"
                    f"  {counters_info}\n\n"
                    f"  В очереди сна: {len(qm.get_queue())} монет"
                )
                _sleep_report_sent = True

            # Detect wake-up from sleep
            if _was_sleeping and not is_sleeping:
                logger.info("☀️ Waking up from sleep! Filtering queue...")
                dropped, kept, kept_names = await qm.filter_queue_on_wake()
                logger.info(f"☀️ Wake filter: dropped={dropped}, kept top {kept}: {kept_names}")
                await _notify_admin(
                    f"☀️ Проснулся!\n"
                    f"🗑 Убрал {dropped} монет (упали >15%)\n"
                    f"✅ Топ {kept} по росту: {', '.join('$'+t for t in kept_names) if kept_names else 'нет'}"
                )
                # Reset daily counters
                _posts_group1 = 0
                _posts_group2 = 0
                _sleep_report_sent = False
            _was_sleeping = is_sleeping

            if is_sleeping:
                await asyncio.sleep(60)
                continue

            # --- Fill processing queue ---
            if _processing_queue.empty():
                # 1. Process overflow/sleep queue first
                queue = qm.get_queue()
                if queue:
                    for item in queue[:]:
                        can_post, reason = qm.can_post_now()
                        if not can_post:
                            if "daily" in reason:
                                day_key = qm._current_day_key()
                                if _daily_limit_alerted.get("day_key") != day_key:
                                    _daily_limit_alerted["day_key"] = day_key
                                    _daily_limit_alerted["alerted"] = True
                                    await _notify_admin(
                                        f"🚫 Суточный лимит исчерпан ({get('posts_per_day')})!\n"
                                        f"⏳ Попробую снова через час."
                                    )
                                logger.info("Daily limit hit, sleeping 1 hour")
                                await asyncio.sleep(3600)
                            break
                        if qm.is_recently_posted(item["ticker"]):
                            qm.remove_from_queue(item["ticker"])
                            continue
                        await _processing_queue.put((item["ticker"], item["price"], item.get("sector", ""), "queue"))
                        qm.remove_from_queue(item["ticker"])

                # 2. If still empty — backfill from group 2
                if _processing_queue.empty():
                    slots = qm.hour_slots_remaining()
                    can_post, _ = qm.can_post_now()
                    
                    if can_post and slots > 0:
                        # Check if we're behind pace — if yes, fill all remaining slots
                        behind, deficit = qm.are_we_behind_pace()
                        fill_count = slots  # Always fill remaining hour slots from group 2
                        
                        if behind:
                            logger.info(f"📉 Behind pace by {deficit} posts, filling {fill_count} slots from group 2")
                        else:
                            logger.info(f"📋 Hour has {slots} free slots, checking group 2")
                        
                        top_g2 = await qm.get_top_group2_tickers(fill_count)
                        for item in top_g2:
                            logger.info(f"📈 Group2 fill: ${item['ticker']} +{item.get('growth_pct', 0):.1f}%")
                            await _processing_queue.put((
                                item["ticker"],
                                item.get("current_price", item["price"]),
                                item.get("sector", ""),
                                "group2"
                            ))

            # --- Get next ticker ---
            try:
                result = await asyncio.wait_for(_processing_queue.get(), timeout=30)
            except asyncio.TimeoutError:
                continue

            # Unpack (support both 3-tuple and 4-tuple)
            if len(result) == 4:
                ticker, price, sector, source = result
            else:
                ticker, price, sector = result
                source = "group1"

            # Check limits again
            can_post, reason = qm.can_post_now()
            if not can_post:
                if source != "group2":  # Don't re-queue group2 items
                    qm.add_to_queue(ticker, price, sector, f"overflow:{reason}")
                if "daily" in reason:
                    day_key = qm._current_day_key()
                    if _daily_limit_alerted.get("day_key") != day_key:
                        _daily_limit_alerted["day_key"] = day_key
                        _daily_limit_alerted["alerted"] = True
                        await _notify_admin(
                            f"🚫 Суточный лимит исчерпан ({get('posts_per_day')})!\n"
                            f"⏳ Попробую снова через час."
                        )
                    await asyncio.sleep(3600)
                continue

            if qm.is_recently_posted(ticker):
                continue

            if qm.is_sleep_time():
                if source != "group2":
                    qm.add_to_queue(ticker, price, sector, "sleep")
                continue

            # --- Process ---
            async with _processing_lock:
                try:
                    result = await process_ticker(ticker, price, sector)
                    if result == "💀 ALL_MODELS_DEAD":
                        await _notify_admin(
                            f"🚨 Все модели и все ключи мертвы!\n"
                            f"Монета ${ticker} не обработана.\n"
                            f"Проверь ключи и модели."
                        )
                    elif result:
                        if source == "group2":
                            _posts_group2 += 1
                            qm.remove_group2_ticker(ticker)
                        else:
                            _posts_group1 += 1
                except Exception as e:
                    logger.error(f"Pipeline error {ticker}: {e}")

            # Pause between posts
            pause = (get("pause_minutes") or 6) * 60
            logger.info(f"⏸ Pause {pause}s before next post")
            await asyncio.sleep(pause)

        except Exception as e:
            logger.error(f"Worker error: {e}")
            await asyncio.sleep(10)


# --- Setup ---

def setup_handlers(app: Application):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("sleep", cmd_sleep))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("hashtags", cmd_hashtags))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("counters", cmd_counters))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CallbackQueryHandler(callback_model_toggle, pattern=r"^addm:"))

    # Group listener — listen to ALL text in groups (not just commands)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (
            filters.Chat(chat_id=SOURCE_GROUP_ID) | filters.Chat(chat_id=SOURCE_GROUP_2_ID)
        ) if SOURCE_GROUP_ID else filters.FORWARDED,  # fallback filter if no group configured
        handle_group_message,
    ))
