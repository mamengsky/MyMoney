"""
MY WALLET Bot - alur bertahap (tanpa input cepat)

Install : pip install "python-telegram-bot>=21" supabase

Alur: pilih menu -> nominal -> akun -> kategori -> tersimpan.
Semua langkah tampil dalam SATU kartu pesan yang diedit, jadi chat tetap rapi.
"""

import asyncio
import datetime
import html
import logging
import uuid

from supabase import Client, create_client
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("wallet-bot")

# ---------------------------------------------------------------- Konfigurasi
SUPABASE_URL = "https://nfquoswrwtgegsprpuae.supabase.co"
SUPABASE_KEY = "sb_publishable_7zCLrX-DciJNflkgPUj6jQ_xb9G_mtf"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TOKEN = "8841482158:AAGwDZp4UhX7qQltllmHJMtyBXTH_KTZPeo"

NOMINAL, ACCOUNT, KATEGORI, CUSTOM = range(4)

EXPENSE_OPTIONS = [
    "Biaya Listrik", "Biaya Internet", "Bensin", "Service Motor",
    "Makan dan Minum", "Rokok", "Pods", "Jajan", "Kebutuhan Rumah", "Jatah Ayang",
]
INCOME_OPTIONS = ["Gaji / Income Utama", "Bonus / Sampingan"]

BTN_IN, BTN_OUT = "🟢 Pemasukan", "🔴 Pengeluaran"
BTN_BAL, BTN_HIST = "📊 Saldo", "🕘 Riwayat"
MENU_REGEX = rf"^({BTN_IN}|{BTN_OUT}|{BTN_BAL}|{BTN_HIST})$"


# ------------------------------------------------------------------- Helper
def rupiah(n: float) -> str:
    sign = "-" if n < 0 else ""
    return f"{sign}Rp {abs(n):,.0f}".replace(",", ".")


def esc(s) -> str:
    return html.escape(str(s))


def parse_amount(text: str):
    """'50000' | '50.000' | '25rb' | '1,5jt' -> float, atau None."""
    t = text.lower().replace("rp", "").replace(" ", "")
    mult = 1
    for suffix, m in (("juta", 1_000_000), ("jt", 1_000_000),
                      ("ribu", 1_000), ("rb", 1_000), ("k", 1_000)):
        if t.endswith(suffix):
            mult, t = m, t[: -len(suffix)]
            break
    t = t.replace(",", ".") if mult > 1 else t.replace(".", "").replace(",", "")
    try:
        val = float(t) * mult
    except ValueError:
        return None
    return val if val > 0 else None


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_IN), KeyboardButton(BTN_OUT)],
         [KeyboardButton(BTN_BAL), KeyboardButton(BTN_HIST)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel_row():
    return [InlineKeyboardButton("✖️ Batal", callback_data="cancel")]


def card_text(d: dict, step: str) -> str:
    """Isi kartu: judul + data yang sudah terisi + instruksi langkah sekarang."""
    masuk = d["type"] == "masuk"
    lines = ["🟢 <b>Catat Pemasukan</b>" if masuk else "🔴 <b>Catat Pengeluaran</b>", ""]
    if d.get("amount"):
        lines.append(f"💰 Nominal: <b>{rupiah(d['amount'])}</b>")
    if d.get("account"):
        lines.append("🏦 Akun: <b>Bank</b>" if d["account"] == "bank" else "💵 Akun: <b>Cash</b>")
    lines += ["", step]
    return "\n".join(lines)


async def show_card(context, text, kb=None):
    d = context.user_data
    await context.bot.edit_message_text(
        text,
        chat_id=d["chat_id"],
        message_id=d["card_id"],
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


async def drop_card(context):
    """Hapus kartu lama (kalau pengguna pindah menu di tengah proses)."""
    d = context.user_data
    if d.get("card_id"):
        try:
            await context.bot.delete_message(d["chat_id"], d["card_id"])
        except Exception:
            pass
    d.clear()


async def try_delete(message):
    try:
        await message.delete()
    except Exception:
        pass


async def db(fn):
    """Jalankan panggilan Supabase (blocking) di thread terpisah."""
    return await asyncio.to_thread(fn)


# -------------------------------------------------------------------- Umum
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Selamat datang di <b>MY WALLET</b> 💰\n\n"
        "Data yang kamu catat otomatis tersinkron dengan website.\n"
        "Pilih menu di bawah untuk memulai ⬇️",
        reply_markup=main_menu(),
        parse_mode=ParseMode.HTML,
    )


async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Silakan pilih menu di bawah ⬇️", reply_markup=main_menu()
    )


# ------------------------------------------------------------------ Transaksi
async def mulai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Langkah 1: pilih jenis lewat menu, lalu minta nominal."""
    await drop_card(context)
    text = update.message.text.lower()
    tipe = "masuk" if ("pemasukan" in text or "masuk" in text) else "keluar"
    context.user_data["type"] = tipe

    msg = await update.message.reply_text(
        card_text(
            context.user_data,
            "<b>Langkah 1/3</b> — Ketik nominalnya\n"
            "<i>Contoh: 50000, 50.000, 25rb, 1,5jt</i>",
        ),
        reply_markup=InlineKeyboardMarkup([cancel_row()]),
        parse_mode=ParseMode.HTML,
    )
    context.user_data.update(chat_id=msg.chat_id, card_id=msg.message_id)
    return NOMINAL


async def terima_nominal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Langkah 2: terima nominal, tampilkan pilihan akun."""
    amount = parse_amount(update.message.text)
    await try_delete(update.message)  # bersihkan chat

    d = context.user_data
    if not amount:
        await show_card(
            context,
            card_text(d, "⚠️ Nominal tidak dikenali.\n<b>Langkah 1/3</b> — Ketik ulang nominalnya\n"
                         "<i>Contoh: 50000, 25rb, 1,5jt</i>"),
            InlineKeyboardMarkup([cancel_row()]),
        )
        return NOMINAL

    d["amount"] = amount
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 Bank", callback_data="acc:bank"),
         InlineKeyboardButton("💵 Cash", callback_data="acc:cash")],
        cancel_row(),
    ])
    await show_card(context, card_text(d, "<b>Langkah 2/3</b> — Pilih akun"), kb)
    return ACCOUNT


async def terima_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Langkah 3: terima akun, tampilkan pilihan kategori."""
    q = update.callback_query
    await q.answer()
    d = context.user_data
    d["account"] = q.data.split(":")[1]

    options = INCOME_OPTIONS if d["type"] == "masuk" else EXPENSE_OPTIONS
    buttons = [InlineKeyboardButton(o, callback_data=f"cat:{i}") for i, o in enumerate(options)]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("✏️ Lainnya (ketik sendiri)", callback_data="cat:custom")])
    rows.append(cancel_row())

    await show_card(
        context, card_text(d, "<b>Langkah 3/3</b> — Pilih keterangan"), InlineKeyboardMarkup(rows)
    )
    return KATEGORI


async def terima_kategori(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pilihan = q.data.split(":")[1]

    if pilihan == "custom":
        await show_card(
            context,
            card_text(context.user_data, "<b>Langkah 3/3</b> — Ketik keterangan transaksi"),
            InlineKeyboardMarkup([cancel_row()]),
        )
        return CUSTOM

    options = INCOME_OPTIONS if context.user_data["type"] == "masuk" else EXPENSE_OPTIONS
    return await simpan(context, options[int(pilihan)])


async def terima_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()[:100]
    await try_delete(update.message)
    return await simpan(context, note)


async def simpan(context: ContextTypes.DEFAULT_TYPE, note: str):
    """Simpan ke Supabase lalu ubah kartu menjadi struk."""
    d = context.user_data
    db_type = "income" if d["type"] == "masuk" else "expense"
    tx_id = f"bot_{uuid.uuid4().hex[:10]}"
    today = datetime.date.today().isoformat()

    row = {
        "id": tx_id,
        "type": db_type,
        "amount": d["amount"],
        "category": note if db_type == "expense" else "Pemasukan",
        "account": d["account"],
        "note": note,
        "date": today,
    }

    try:
        res = await db(lambda: supabase.table("transactions").insert(row).execute())
        if not res.data:
            await show_card(context, "❌ Gagal menyimpan. Silakan coba lagi.")
        else:
            icon = "🟢" if db_type == "income" else "🔴"
            acc = "🏦 Bank" if d["account"] == "bank" else "💵 Cash"
            await show_card(
                context,
                f"✅ <b>Tercatat!</b> {icon}\n\n"
                f"💰 Jumlah: <b>{rupiah(d['amount'])}</b>\n"
                f"{acc.split()[0]} Akun: <b>{acc.split()[1]}</b>\n"
                f"📝 Keterangan: <b>{esc(note)}</b>\n"
                f"📅 Tanggal: <b>{today}</b>",
                InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🗑 Hapus (salah input)", callback_data=f"undo:{tx_id}")]]
                ),
            )
    except Exception:
        logger.exception("Gagal insert transaksi")
        await show_card(context, "❌ Terjadi kesalahan saat menyimpan. Coba lagi nanti.")

    context.user_data.clear()
    return ConversationHandler.END


async def batal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    if update.callback_query:
        await update.callback_query.answer()
        await show_card(context, "❌ Pencatatan dibatalkan.")
    elif d.get("card_id"):
        await show_card(context, "❌ Pencatatan dibatalkan.")
    else:
        await update.message.reply_text("Tidak ada proses yang berjalan.", reply_markup=main_menu())
    d.clear()
    return ConversationHandler.END


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tombol hapus pada struk (di luar conversation)."""
    q = update.callback_query
    tx_id = q.data.split(":", 1)[1]
    try:
        await db(lambda: supabase.table("transactions").delete().eq("id", tx_id).execute())
        await q.answer("Transaksi dihapus")
        await q.edit_message_text("🗑 Transaksi dihapus.")
    except Exception:
        logger.exception("Gagal hapus")
        await q.answer("Gagal menghapus", show_alert=True)


# --------------------------------------------------------------- Saldo/Riwayat
async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await drop_card(context)
    try:
        res = await db(
            lambda: supabase.table("transactions").select("type,amount,account,date").execute()
        )
        data = res.data or []
        if not data:
            await update.effective_message.reply_text(
                "Belum ada transaksi tercatat.", reply_markup=main_menu()
            )
            return ConversationHandler.END

        bulan = datetime.date.today().strftime("%Y-%m")
        sign = lambda r: r["amount"] if r.get("type") == "income" else -r["amount"]
        total = lambda rows, t: sum(r["amount"] for r in rows if r.get("type") == t)

        bulan_ini = [r for r in data if str(r.get("date", "")).startswith(bulan)]
        bank = sum(sign(r) for r in data if r.get("account") == "bank")
        cash = sum(sign(r) for r in data if r.get("account") == "cash")

        await update.effective_message.reply_text(
            "📊 <b>Ringkasan MY WALLET</b>\n\n"
            f"💰 <b>Saldo Bersih:</b> {rupiah(total(data, 'income') - total(data, 'expense'))}\n"
            f"   🏦 Bank: {rupiah(bank)}\n"
            f"   💵 Cash: {rupiah(cash)}\n\n"
            "📅 <b>Bulan ini</b>\n"
            f"   🟢 Pemasukan: {rupiah(total(bulan_ini, 'income'))}\n"
            f"   🔴 Pengeluaran: {rupiah(total(bulan_ini, 'expense'))}",
            reply_markup=main_menu(),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("Gagal ambil saldo")
        await update.effective_message.reply_text("Gagal mengambil data saldo.", reply_markup=main_menu())
    return ConversationHandler.END


async def riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await drop_card(context)
    try:
        res = await db(
            lambda: supabase.table("transactions")
            .select("type,amount,account,note,date")
            .order("date", desc=True)
            .limit(10)
            .execute()
        )
        data = res.data or []
        if not data:
            await update.effective_message.reply_text("Belum ada transaksi.", reply_markup=main_menu())
            return ConversationHandler.END

        lines = ["🕘 <b>10 Transaksi Terakhir</b>\n"]
        for r in data:
            icon = "🟢" if r["type"] == "income" else "🔴"
            acc = "🏦" if r.get("account") == "bank" else "💵"
            lines.append(
                f"{icon} {rupiah(r['amount'])} {acc} • {esc(r.get('note') or '-')} "
                f"<i>({r.get('date', '')})</i>"
            )
        await update.effective_message.reply_text(
            "\n".join(lines), reply_markup=main_menu(), parse_mode=ParseMode.HTML
        )
    except Exception:
        logger.exception("Gagal ambil riwayat")
        await update.effective_message.reply_text("Gagal mengambil riwayat.", reply_markup=main_menu())
    return ConversationHandler.END


# ------------------------------------------------------------------- Setup
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled error", exc_info=context.error)


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("masuk", "Catat pemasukan"),
        BotCommand("keluar", "Catat pengeluaran"),
        BotCommand("saldo", "Lihat saldo"),
        BotCommand("riwayat", "10 transaksi terakhir"),
        BotCommand("batal", "Batalkan pencatatan"),
    ])


def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    text_input = filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_REGEX)
    is_bal = filters.Regex(rf"^{BTN_BAL}$")
    is_hist = filters.Regex(rf"^{BTN_HIST}$")

    conv = ConversationHandler(
        entry_points=[
            CommandHandler(["masuk", "keluar"], mulai),
            MessageHandler(filters.Regex(rf"^({BTN_IN}|{BTN_OUT})$"), mulai),
        ],
        states={
            NOMINAL: [MessageHandler(text_input, terima_nominal)],
            ACCOUNT: [CallbackQueryHandler(terima_account, pattern=r"^acc:")],
            KATEGORI: [CallbackQueryHandler(terima_kategori, pattern=r"^cat:")],
            CUSTOM: [MessageHandler(text_input, terima_custom)],
        },
        fallbacks=[
            CallbackQueryHandler(batal, pattern=r"^cancel$"),
            CommandHandler("batal", batal),
            CommandHandler("saldo", saldo),
            CommandHandler("riwayat", riwayat),
            MessageHandler(is_bal, saldo),
            MessageHandler(is_hist, riwayat),
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("saldo", saldo))
    app.add_handler(CommandHandler("riwayat", riwayat))
    app.add_handler(MessageHandler(is_bal, saldo))
    app.add_handler(MessageHandler(is_hist, riwayat))
    app.add_handler(CallbackQueryHandler(undo, pattern=r"^undo:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_text))
    app.add_error_handler(on_error)

    logger.info("Bot berjalan... Tekan Ctrl+C untuk berhenti.")
    app.run_polling()


if __name__ == "__main__":
    main()
