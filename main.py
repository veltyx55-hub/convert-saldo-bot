"""
Bot Convert Saldo (CV Payment System)

Bot Telegram untuk menerima order convert saldo, mengirim QRIS pembayaran,
menerima bukti transfer, dan mengelola order melalui grup admin.

Tidak menggunakan AI / OpenAI API.
"""

import logging
import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import db

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("cv_payment_bot")

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------

ASK_NOMINAL, ASK_EWALLET, ASK_NOMOR_TUJUAN, WAITING_CONFIRM, WAITING_PROOF = range(5)

ORDER_GROUP_ID_INT = int(config.ORDER_GROUP_ID)
SUPPORT_GROUP_ID_INT = int(config.SUPPORT_GROUP_ID)
REQUIRED_GROUP_ID_INT = int(config.REQUIRED_GROUP_ID)

MEMBER_STATUSES = (
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
)

# Status order yang berarti "sudah diproses" (tidak boleh diproses ulang)
ORDER_ACTIVE_STATUSES_FOR_ACCEPT_REJECT = {"MENUNGGU_ADMIN"}
ORDER_ACTIVE_STATUSES_FOR_COMPLETE = {"DIPROSES"}
ORDER_ACTIVE_STATUSES_FOR_COMPLETION_PROOF = {"MENUNGGU_BUKTI_ADMIN"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_rupiah(amount: int) -> str:
    return f"{amount:,}".replace(",", ".")


def calculate_fee(nominal: int) -> tuple[int, int]:
    fee = round(nominal * config.ADMIN_FEE_PERCENT / 100)
    total = nominal + fee
    return fee, total


def main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("\U0001F4B8 Buat Order", callback_data="menu_order")],
        [InlineKeyboardButton("\U0001F4B0 Rate & Biaya", callback_data="menu_rate")],
        [InlineKeyboardButton("\U0001F4DC Riwayat Transaksi", callback_data="menu_riwayat")],
        [InlineKeyboardButton("\U0001F4D6 Cara Order", callback_data="menu_help")],
        [InlineKeyboardButton("\U0001F4AC Chat Admin", callback_data="menu_chat_admin")],
    ]
    return InlineKeyboardMarkup(keyboard)


def join_group_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    if config.REQUIRED_GROUP_INVITE_LINK:
        keyboard.append(
            [InlineKeyboardButton("\U0001F517 Gabung Grup", url=config.REQUIRED_GROUP_INVITE_LINK)]
        )
    keyboard.append(
        [InlineKeyboardButton("\u2705 Verifikasi", callback_data="verify_membership")]
    )
    return InlineKeyboardMarkup(keyboard)


def ewallet_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(config.EWALLET_LABELS[w], callback_data=f"ewallet_{w}")]
        for w in config.EWALLET_OPTIONS
    ]
    keyboard.append([InlineKeyboardButton("\u274C Batal", callback_data="cancel_order")])
    return InlineKeyboardMarkup(keyboard)


def payment_confirm_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("\u2705 Saya Sudah Transfer", callback_data="confirm_transfer")],
        [InlineKeyboardButton("\u274C Batal", callback_data="cancel_order")],
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_action_keyboard(order_id: str) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("\u2705 Terima", callback_data=f"admin_accept_{order_id}"),
            InlineKeyboardButton("\u274C Tolak", callback_data=f"admin_reject_{order_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_complete_keyboard(order_id: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("\u2705 Selesai", callback_data=f"admin_complete_{order_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def is_group_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        logger.error(
            "Gagal memeriksa status admin untuk user %s di chat %s.", user_id, chat_id
        )
        traceback.print_exc()
        return False


async def is_group_member(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(REQUIRED_GROUP_ID_INT, user_id)
        is_member = member.status in MEMBER_STATUSES
        db.set_user_verified(user_id, is_member)
        return is_member
    except Exception:
        logger.error(
            "Gagal memeriksa status keanggotaan grup wajib untuk user %s.", user_id
        )
        traceback.print_exc()
        db.set_user_verified(user_id, False)
        return False


async def send_join_group_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "\U0001F512 *Akses Terkunci*\n\n"
        "Untuk menggunakan bot ini, kamu wajib bergabung ke grup kami terlebih dahulu.\n\n"
        "Klik *Gabung Grup*, lalu klik *Verifikasi* setelah kamu bergabung."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=join_group_keyboard()
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=join_group_keyboard()
        )


async def require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if await is_group_member(context, user_id):
        return True
    await send_join_group_gate(update, context)
    return False


def is_within_operating_hours() -> bool:
    now = datetime.now(ZoneInfo(config.OPERATING_TIMEZONE))
    return config.OPERATING_HOUR_START <= now.hour < config.OPERATING_HOUR_END


# ---------------------------------------------------------------------------
# /start & menu handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("User %s menjalankan /start", update.effective_user.id)

    if not await require_membership(update, context):
        return

    text = (
        "\U0001F44B Selamat datang di *CV Payment System*!\n\n"
        "Bot ini melayani convert saldo e-wallet dengan cepat dan aman.\n"
        "Silakan pilih menu di bawah ini:"
    )
    await update.effective_message.reply_text(
        text, reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN
    )


async def verify_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    if await is_group_member(context, user_id):
        await query.answer("\u2705 Verifikasi berhasil!", show_alert=True)
        text = (
            "\u2705 *Verifikasi berhasil!*\n\n"
            "Terima kasih sudah bergabung. Silakan pilih menu di bawah ini:"
        )
        await query.message.reply_text(
            text, reply_markup=main_menu_keyboard(), parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query.answer(
            "\u274C Kamu belum bergabung ke grup. Silakan gabung terlebih dahulu.",
            show_alert=True,
        )


async def menu_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    if not await require_membership(update, context):
        return

    await query.answer()
    text = (
        "\U0001F4B0 *Rate & Biaya Admin*\n\n"
        "Biaya admin hanya *1%* dari nominal convert.\n\n"
        "Contoh perhitungan:\n\n"
        "\u2022 Convert Rp10.000 \u2192 Biaya Rp100 \u2192 Total Rp10.100\n"
        "\u2022 Convert Rp20.000 \u2192 Biaya Rp200 \u2192 Total Rp20.200\n"
        "\u2022 Convert Rp50.000 \u2192 Biaya Rp500 \u2192 Total Rp50.500\n"
        "\u2022 Convert Rp100.000 \u2192 Biaya Rp1.000 \u2192 Total Rp101.000\n\n"
        "Semakin besar nominal convert, biaya tetap hanya 1%.\n"
        "Nominal kecil juga dikenakan biaya yang sangat ringan."
    )
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def menu_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    if not await require_membership(update, context):
        return

    await query.answer()
    text = (
        "\U0001F4D6 *Cara Order*\n\n"
        "1. Klik *Buat Order*\n"
        "2. Masukkan nominal yang ingin di-convert\n"
        "3. Pilih tujuan saldo\n"
        "4. Masukkan nomor tujuan\n"
        "5. Scan QRIS dan lakukan pembayaran sesuai total\n"
        "6. Klik *Saya Sudah Transfer* lalu kirim bukti transfer\n"
        "7. Tunggu admin memverifikasi dan menyelesaikan order\n\n"
        "Ada kendala? Gunakan menu *Chat Admin*."
    )
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def menu_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    if not await require_membership(update, context):
        return

    await query.answer()
    context.user_data["awaiting_support_message"] = True
    text = (
        "\U0001F4AC *Chat Admin*\n\n"
        "Silakan tulis pesan kamu sekarang. Pesan ini akan diteruskan ke admin, "
        "dan balasan admin akan langsung dikirim ke chat ini."
    )
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def menu_riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    if not await require_membership(update, context):
        return

    await query.answer()
    user_id = query.from_user.id
    orders = db.get_orders_by_user(user_id, limit=10)

    if not orders:
        await query.message.reply_text("\U0001F4ED Kamu belum memiliki riwayat transaksi.")
        return

    lines = ["\U0001F4DC *Riwayat Transaksi (maks. 10 terakhir)*\n"]
    for order in orders:
        tanggal = order["created_at"].strftime("%d-%m-%Y %H:%M")
        lines.append(
            f"\U0001F9FE Order ID: `{order['order_id']}`\n"
            f"\U0001F4B0 Nominal: Rp{format_rupiah(order['nominal'])}\n"
            f"\U0001F4B3 Biaya Admin: Rp{format_rupiah(order['fee'])}\n"
            f"\U0001F9FE Total: Rp{format_rupiah(order['total'])}\n"
            f"\U0001F3E6 Tujuan: {order['ewallet']}\n"
            f"\U0001F522 Nomor Tujuan: {order['nomor_tujuan'] or '-'}\n"
            f"\U0001F4CC Status: {order['status']}\n"
            f"\U0001F5D3 Tanggal: {tanggal}\n"
        )

    text = "\n".join(lines)
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Chat Admin (relay ke SUPPORT_GROUP_ID)
# ---------------------------------------------------------------------------

async def relay_user_message_to_support(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not context.user_data.get("awaiting_support_message"):
        return  # bukan pesan chat admin, biarkan (tidak ada aksi)

    user = update.effective_user
    user_text = update.message.text

    caption = (
        "\U0001F4AC *Pesan dari User*\n\n"
        f"User ID: `{user.id}`\n\n"
        f"{user_text}"
    )

    support_message = await context.bot.send_message(
        chat_id=SUPPORT_GROUP_ID_INT,
        text=caption,
        parse_mode=ParseMode.MARKDOWN,
    )

    db.save_support_message(support_message.message_id, user.id)

    logger.info("Pesan dari user %s diteruskan ke support group", user.id)

    await update.message.reply_text(
        "\u2705 Pesan kamu sudah dikirim ke admin. Kamu bisa kirim pesan lagi kapan saja."
    )


async def relay_admin_reply_to_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    replied_message = update.message.reply_to_message
    if not replied_message:
        return

    user_id = db.get_support_message_user(replied_message.message_id)
    if not user_id:
        return  # bukan balasan untuk pesan customer service

    admin_reply_text = update.message.text
    if not admin_reply_text:
        await update.message.reply_text(
            "Balasan hanya mendukung teks. Kirim balasan berupa pesan teks."
        )
        return

    await context.bot.send_message(
        chat_id=user_id,
        text=f"\U0001F4AC *Balasan Admin:*\n\n{admin_reply_text}",
        parse_mode=ParseMode.MARKDOWN,
    )

    logger.info("Balasan admin diteruskan ke user %s", user_id)

    await update.message.reply_text("\u2705 Balasan sudah dikirim ke user.")


# ---------------------------------------------------------------------------
# Buat Order - ConversationHandler (alur ringkas berbasis Langkah x/4)
# ---------------------------------------------------------------------------

async def start_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_membership(update, context):
        return ConversationHandler.END

    query = update.callback_query
    if query:
        await query.answer()
        send = query.message.reply_text
    else:
        send = update.effective_message.reply_text

    if not is_within_operating_hours():
        await send(
            "\u23F0 *Di luar jam operasional*\n\n"
            f"Order baru hanya dapat dibuat pukul {config.OPERATING_HOUR_START:02d}.00\u2013"
            f"{config.OPERATING_HOUR_END:02d}.00 WITA.\n"
            "Kamu tetap bisa melihat Riwayat, Cara Order, dan Rate & Biaya.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    context.user_data.clear()

    text = (
        "\U0001F4DD *Langkah 1/4 \u2014 Nominal Convert*\n\n"
        "Silakan masukkan nominal convert.\n\n"
        "Contoh:\n`100000`"
    )
    await send(text, parse_mode=ParseMode.MARKDOWN, reply_markup=ReplyKeyboardRemove())
    return ASK_NOMINAL


async def ask_nominal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(".", "").replace(",", "").replace(" ", "")
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text(
            "\u26A0\uFE0F Nominal tidak valid. Masukkan angka saja, contoh: 100000"
        )
        return ASK_NOMINAL

    nominal = int(text)
    context.user_data["nominal"] = nominal

    step2_text = "\U0001F4DD *Langkah 2/4 \u2014 Tujuan Saldo*\n\nPilih tujuan saldo kamu:"
    message = await update.message.reply_text(
        step2_text, parse_mode=ParseMode.MARKDOWN, reply_markup=ewallet_keyboard()
    )
    context.user_data["step2_message_id"] = message.message_id
    return ASK_EWALLET


async def ask_ewallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    ewallet = query.data.replace("ewallet_", "")
    context.user_data["ewallet"] = ewallet

    if ewallet in config.EWALLET_REQUIRE_NUMBER:
        step3_text = (
            "\U0001F4DD *Langkah 3/4 \u2014 Nomor Tujuan*\n\n"
            f"Silakan masukkan nomor tujuan {ewallet} yang akan menerima saldo."
        )
        await query.edit_message_text(step3_text, parse_mode=ParseMode.MARKDOWN)
        return ASK_NOMOR_TUJUAN

    context.user_data["nomor_tujuan"] = None
    return await send_qris_payment(update, context)


async def ask_nomor_tujuan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nomor = update.message.text.strip()
    if not nomor:
        await update.message.reply_text("\u26A0\uFE0F Nomor tujuan tidak boleh kosong. Coba lagi:")
        return ASK_NOMOR_TUJUAN

    context.user_data["nomor_tujuan"] = nomor
    return await send_qris_payment(update, context)


async def send_qris_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    nominal = context.user_data["nominal"]
    ewallet = context.user_data["ewallet"]
    nomor_tujuan = context.user_data.get("nomor_tujuan")

    fee, total = calculate_fee(nominal)
    order_id = db.generate_order_id()

    db.create_order(
        order_id=order_id,
        user_id=user.id,
        username=user.username or user.full_name,
        nominal=nominal,
        fee=fee,
        total=total,
        ewallet=ewallet,
        nomor_tujuan=nomor_tujuan,
    )
    context.user_data["order_id"] = order_id

    logger.info("Order dibuat: %s oleh user %s", order_id, user.id)

    caption = (
        "\U0001F4DD *Langkah 4/4 \u2014 Pembayaran*\n\n"
        f"\U0001F9FE Order ID: `{order_id}`\n\n"
        f"\U0001F4B0 Nominal Convert: Rp{format_rupiah(nominal)}\n"
        f"\U0001F4B3 Biaya Admin ({config.ADMIN_FEE_PERCENT}%): Rp{format_rupiah(fee)}\n"
        f"\U0001F9FE *Total Pembayaran: Rp{format_rupiah(total)}*\n\n"
        "Silakan scan QRIS di atas dan lakukan pembayaran sesuai total.\n"
        "Setelah transfer, klik tombol di bawah ini."
    )

    target_message = update.callback_query.message if update.callback_query else update.message

    if os.path.exists(config.QRIS_IMAGE_PATH):
        with open(config.QRIS_IMAGE_PATH, "rb") as qris_file:
            qris_message = await target_message.reply_photo(
                photo=qris_file,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=payment_confirm_keyboard(),
            )
        db.update_order(order_id, qris_message_id=qris_message.message_id)
    else:
        logger.error("File QRIS tidak ditemukan di %s", config.QRIS_IMAGE_PATH)
        await target_message.reply_text(
            caption + "\n\n(Gambar QRIS tidak ditemukan, hubungi admin)",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=payment_confirm_keyboard(),
        )

    return WAITING_CONFIRM


async def confirm_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "\U0001F4F7 *Upload Bukti Transfer*\n\nSilakan kirim screenshot bukti transfer.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WAITING_PROOF


async def receive_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_id = context.user_data.get("order_id")
    if not order_id:
        await update.message.reply_text(
            "Order tidak ditemukan. Silakan mulai ulang dengan /start."
        )
        return ConversationHandler.END

    photo = update.message.photo[-1]
    file_id = photo.file_id

    db.update_order(order_id, status="MENUNGGU_ADMIN", proof_file_id=file_id)

    order = db.get_order(order_id)
    user = update.effective_user

    caption = (
        f"\U0001F514 *Order Baru Menunggu Verifikasi*\n\n"
        f"Order ID: `{order['order_id']}`\n"
        f"Username: @{user.username if user.username else user.full_name}\n"
        f"Nominal: Rp{format_rupiah(order['nominal'])}\n"
        f"Fee: Rp{format_rupiah(order['fee'])}\n"
        f"Total: Rp{format_rupiah(order['total'])}\n"
        f"Tujuan Saldo: {order['ewallet']}\n"
        f"Nomor Tujuan: {order['nomor_tujuan'] or '-'}"
    )

    order_message = await context.bot.send_photo(
        chat_id=ORDER_GROUP_ID_INT,
        photo=file_id,
        caption=caption,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_action_keyboard(order_id),
    )
    db.update_order(order_id, order_group_message_id=order_message.message_id)

    logger.info("Bukti transfer order %s dikirim ke order group", order_id)

    await update.message.reply_text(
        "\u2705 Bukti transfer kamu sudah diterima dan sedang dikirim ke admin.\n"
        "Mohon tunggu verifikasi dari admin."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    order_id = context.user_data.get("order_id")
    if order_id:
        db.update_order(order_id, status="DIBATALKAN")
        logger.info("Order %s dibatalkan oleh user", order_id)

    await query.message.reply_text("\u274C Order dibatalkan.")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_id = context.user_data.get("order_id")
    if order_id:
        db.update_order(order_id, status="DIBATALKAN")
    await update.message.reply_text("\u274C Order dibatalkan.")
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Admin actions (ORDER_GROUP_ID) - hanya admin grup yang boleh menekan tombol
# ---------------------------------------------------------------------------

async def admin_accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    admin_user = query.from_user
    chat_id = query.message.chat.id

    if not await is_group_admin(context, chat_id, admin_user.id):
        logger.warning(
            "Akses ditolak: user %s (%s) bukan admin, mencoba Terima order.",
            admin_user.id,
            admin_user.username,
        )
        await query.answer("\u274C Hanya admin yang dapat menggunakan tombol ini.", show_alert=True)
        return

    order_id = query.data.replace("admin_accept_", "")
    order = db.get_order(order_id)

    if not order:
        await query.answer("Order tidak ditemukan.", show_alert=True)
        return

    status_before = order["status"]
    if status_before not in ORDER_ACTIVE_STATUSES_FOR_ACCEPT_REJECT:
        logger.warning(
            "Double process dicegah: order %s status %s, admin %s mencoba Terima.",
            order_id,
            status_before,
            admin_user.id,
        )
        await query.answer("Order ini sudah diproses.", show_alert=True)
        return

    await query.answer()

    db.update_order(order_id, status="DIPROSES")
    logger.info(
        "Order %s diterima oleh admin %s (%s). Status: %s -> DIPROSES",
        order_id,
        admin_user.id,
        admin_user.username,
        status_before,
    )

    await query.edit_message_caption(
        caption=query.message.caption + "\n\n\u2705 *STATUS: DITERIMA - DIPROSES*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_complete_keyboard(order_id),
    )

    await context.bot.send_message(
        chat_id=order["user_id"],
        text=(
            f"\u2705 Pembayaran untuk order `{order_id}` telah *diterima* "
            "dan sedang diproses oleh admin.\nMohon tunggu ya!"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    admin_user = query.from_user
    chat_id = query.message.chat.id

    if not await is_group_admin(context, chat_id, admin_user.id):
        logger.warning(
            "Akses ditolak: user %s (%s) bukan admin, mencoba Tolak order.",
            admin_user.id,
            admin_user.username,
        )
        await query.answer("\u274C Hanya admin yang dapat menggunakan tombol ini.", show_alert=True)
        return

    order_id = query.data.replace("admin_reject_", "")
    order = db.get_order(order_id)

    if not order:
        await query.answer("Order tidak ditemukan.", show_alert=True)
        return

    status_before = order["status"]
    if status_before not in ORDER_ACTIVE_STATUSES_FOR_ACCEPT_REJECT:
        logger.warning(
            "Double process dicegah: order %s status %s, admin %s mencoba Tolak.",
            order_id,
            status_before,
            admin_user.id,
        )
        await query.answer("Order ini sudah diproses.", show_alert=True)
        return

    await query.answer()

    db.update_order(order_id, status="DITOLAK")
    logger.info(
        "Order %s ditolak oleh admin %s (%s). Status: %s -> DITOLAK",
        order_id,
        admin_user.id,
        admin_user.username,
        status_before,
    )

    await query.edit_message_caption(
        caption=query.message.caption + "\n\n\u274C *STATUS: DITOLAK*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=None,
    )

    await context.bot.send_message(
        chat_id=order["user_id"],
        text=(
            f"\u274C Bukti transfer untuk order `{order_id}` *tidak valid*.\n"
            "Silakan hubungi admin melalui menu Chat Admin atau buat order baru."
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def admin_complete_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    admin_user = query.from_user
    chat_id = query.message.chat.id

    if not await is_group_admin(context, chat_id, admin_user.id):
        logger.warning(
            "Akses ditolak: user %s (%s) bukan admin, mencoba Selesai order.",
            admin_user.id,
            admin_user.username,
        )
        await query.answer("\u274C Hanya admin yang dapat menggunakan tombol ini.", show_alert=True)
        return

    order_id = query.data.replace("admin_complete_", "")
    order = db.get_order(order_id)

    if not order:
        await query.answer("Order tidak ditemukan.", show_alert=True)
        return

    status_before = order["status"]
    if status_before not in ORDER_ACTIVE_STATUSES_FOR_COMPLETE:
        logger.warning(
            "Double process dicegah: order %s status %s, admin %s mencoba Selesai.",
            order_id,
            status_before,
            admin_user.id,
        )
        await query.answer("Order ini sudah diproses.", show_alert=True)
        return

    await query.answer()

    db.update_order(order_id, status="MENUNGGU_BUKTI_ADMIN")

    logger.info(
        "Admin %s (%s) menandai order %s menunggu bukti transfer admin. Status: %s -> MENUNGGU_BUKTI_ADMIN",
        admin_user.id,
        admin_user.username,
        order_id,
        status_before,
    )

    await query.edit_message_caption(
        caption=(
            query.message.caption
            + "\n\n\u23F3 *STATUS: MENUNGGU BUKTI TRANSFER ADMIN*\n\n"
            "Silakan reply pesan order ini dengan foto bukti transfer."
        ),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=None,
    )


async def admin_receive_completion_proof(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    admin_user = update.effective_user

    if not await is_group_admin(context, update.effective_chat.id, admin_user.id):
        logger.warning(
            "Akses ditolak: user %s (%s) bukan admin, mencoba mengirim bukti transfer di order group.",
            admin_user.id,
            admin_user.username,
        )
        await update.message.reply_text(
            "\u274C Hanya admin yang dapat menggunakan fitur ini."
        )
        return

    replied_message = update.message.reply_to_message
    if not replied_message:
        await update.message.reply_text(
            "\u274C Silakan reply pesan order yang sesuai menggunakan foto bukti transfer."
        )
        return

    order = db.get_order_by_group_message_id(replied_message.message_id)
    if not order:
        await update.message.reply_text("\u274C Pesan ini bukan order yang valid.")
        return

    order_id = order["order_id"]
    status_before = order["status"]

    if status_before == "SELESAI":
        logger.warning(
            "Admin %s (%s) mencoba kirim bukti transfer untuk order %s yang sudah selesai.",
            admin_user.id,
            admin_user.username,
            order_id,
        )
        await update.message.reply_text(
            "\u274C Order ini sudah selesai dan tidak dapat menerima bukti transfer lagi."
        )
        return

    if status_before not in ORDER_ACTIVE_STATUSES_FOR_COMPLETION_PROOF:
        logger.warning(
            "Double process dicegah: order %s status %s, admin %s mencoba kirim bukti transfer.",
            order_id,
            status_before,
            admin_user.id,
        )
        await update.message.reply_text("Order ini sudah diproses.")
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    completed = db.complete_order_if_pending(
        order_id, admin_proof_file_id=file_id, expected_status="MENUNGGU_BUKTI_ADMIN"
    )
    if not completed:
        logger.warning(
            "Double process dicegah (race): order %s sudah diproses admin lain saat %s mengirim bukti.",
            order_id,
            admin_user.id,
        )
        await update.message.reply_text("Order ini sudah diproses.")
        return

    order = db.get_order(order_id)
    completed_at = datetime.now().strftime("%d-%m-%Y %H:%M")

    logger.info(
        "Order %s selesai oleh admin %s (%s). Status: %s -> SELESAI (%s)",
        order_id,
        admin_user.id,
        admin_user.username,
        status_before,
        completed_at,
    )

    await context.bot.send_photo(
        chat_id=order["user_id"],
        photo=file_id,
        caption=(
            f"\U0001F389 Order `{order_id}` telah *SELESAI*!\n\n"
            f"Nominal: Rp{format_rupiah(order['nominal'])}\n"
            f"Tujuan Saldo: {order['ewallet']}\n"
            f"Nomor Tujuan: {order['nomor_tujuan'] or '-'}\n\n"
            "Terima kasih telah menggunakan CV Payment System!"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    qris_message_id = order["qris_message_id"]
    if qris_message_id:
        try:
            await context.bot.delete_message(
                chat_id=order["user_id"], message_id=qris_message_id
            )
            logger.info("Pesan QRIS order %s berhasil dihapus dari chat user.", order_id)
        except Exception:
            logger.error(
                "Gagal menghapus pesan QRIS order %s (mungkin sudah dihapus user).", order_id
            )
            traceback.print_exc()

    order_group_message_id = order["order_group_message_id"]
    if order_group_message_id:
        try:
            await context.bot.edit_message_caption(
                chat_id=ORDER_GROUP_ID_INT,
                message_id=order_group_message_id,
                caption=(
                    f"\U0001F514 *Order Selesai*\n\n"
                    f"Order ID: `{order_id}`\n"
                    f"Nominal: Rp{format_rupiah(order['nominal'])}\n"
                    f"Tujuan Saldo: {order['ewallet']}\n"
                    f"Nomor Tujuan: {order['nomor_tujuan'] or '-'}\n\n"
                    "\U0001F7E2 *Status: Selesai*\n"
                    f"Waktu selesai: {completed_at}"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=None,
            )
        except Exception:
            logger.error(
                "Gagal mengedit pesan order group untuk order %s setelah selesai.", order_id
            )
            traceback.print_exc()

    await update.message.reply_text(
        f"\u2705 Order `{order_id}` ditandai selesai.", parse_mode=ParseMode.MARKDOWN
    )


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception saat menangani update: %s", update)
    traceback.print_exc()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    db.init_db()

    application = Application.builder().token(config.TELEGRAM_TOKEN).build()

    order_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("order", start_order),
            CallbackQueryHandler(start_order, pattern="^menu_order$"),
        ],
        states={
            ASK_NOMINAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_nominal)],
            ASK_EWALLET: [CallbackQueryHandler(ask_ewallet, pattern="^ewallet_")],
            ASK_NOMOR_TUJUAN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_nomor_tujuan)
            ],
            WAITING_CONFIRM: [
                CallbackQueryHandler(confirm_transfer, pattern="^confirm_transfer$"),
            ],
            WAITING_PROOF: [MessageHandler(filters.PHOTO, receive_proof)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(cancel_order_callback, pattern="^cancel_order$"),
        ],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(order_conversation)

    application.add_handler(CallbackQueryHandler(verify_membership, pattern="^verify_membership$"))
    application.add_handler(CallbackQueryHandler(menu_rate, pattern="^menu_rate$"))
    application.add_handler(CallbackQueryHandler(menu_riwayat, pattern="^menu_riwayat$"))
    application.add_handler(CallbackQueryHandler(menu_help, pattern="^menu_help$"))
    application.add_handler(CallbackQueryHandler(menu_chat_admin, pattern="^menu_chat_admin$"))

    application.add_handler(CallbackQueryHandler(admin_accept, pattern="^admin_accept_"))
    application.add_handler(CallbackQueryHandler(admin_reject, pattern="^admin_reject_"))
    application.add_handler(
        CallbackQueryHandler(admin_complete_request, pattern="^admin_complete_")
    )

    # Chat Admin: pesan user (private chat) -> diteruskan ke SUPPORT_GROUP_ID
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            relay_user_message_to_support,
        )
    )

    # Balasan admin (reply di SUPPORT_GROUP_ID) -> diteruskan ke user
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.REPLY
            & filters.Chat(chat_id=SUPPORT_GROUP_ID_INT),
            relay_admin_reply_to_user,
        )
    )

    # Bukti transfer admin (foto di ORDER_GROUP_ID) untuk penyelesaian order
    application.add_handler(
        MessageHandler(
            filters.PHOTO & filters.Chat(chat_id=ORDER_GROUP_ID_INT),
            admin_receive_completion_proof,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Bot CV Payment System dimulai...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
