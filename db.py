"""
Lapisan database (PostgreSQL) untuk Bot Convert Saldo (CV Payment System).

Menggantikan SQLite agar kompatibel dengan Railway (dan penyedia Postgres lain).
Setiap fungsi membuka & menutup koneksi sendiri (pola sederhana, cocok untuk
skala bot ini) dan selalu melempar traceback lengkap jika terjadi error.
"""

import logging
import traceback
from datetime import datetime

import psycopg2
import psycopg2.extras

import config

logger = logging.getLogger("cv_payment_bot.db")


def get_conn():
    return psycopg2.connect(config.DATABASE_URL)


def init_db() -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT,
                nominal BIGINT NOT NULL,
                fee BIGINT NOT NULL,
                total BIGINT NOT NULL,
                ewallet TEXT NOT NULL,
                nomor_tujuan TEXT,
                status TEXT NOT NULL,
                proof_file_id TEXT,
                admin_proof_file_id TEXT,
                order_group_message_id BIGINT,
                qris_message_id BIGINT,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS qris_message_id BIGINT")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS order_counter (
                date TEXT PRIMARY KEY,
                count INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS support_messages (
                support_message_id BIGINT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_verification (
                user_id BIGINT PRIMARY KEY,
                verified BOOLEAN NOT NULL DEFAULT FALSE,
                verified_at TIMESTAMP
            )
            """
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database PostgreSQL siap.")
    except Exception:
        logger.error("Gagal inisialisasi database PostgreSQL.")
        traceback.print_exc()
        raise


def generate_order_id() -> str:
    today = datetime.now().strftime("%Y%m%d")
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO order_counter (date, count) VALUES (%s, 1)
            ON CONFLICT (date) DO UPDATE SET count = order_counter.count + 1
            RETURNING count
            """,
            (today,),
        )
        seq = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return f"CV-{today}-{seq:04d}"
    except Exception:
        logger.error("Gagal generate order_id.")
        traceback.print_exc()
        raise


def create_order(order_id, user_id, username, nominal, fee, total, ewallet, nomor_tujuan) -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO orders (
                order_id, user_id, username, nominal, fee, total, ewallet,
                nomor_tujuan, status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_id,
                user_id,
                username,
                nominal,
                fee,
                total,
                ewallet,
                nomor_tujuan,
                "MENUNGGU_PEMBAYARAN",
                datetime.now(),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        logger.error("Gagal membuat order %s.", order_id)
        traceback.print_exc()
        raise


def get_order(order_id):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row
    except Exception:
        logger.error("Gagal mengambil order %s.", order_id)
        traceback.print_exc()
        raise


def update_order(order_id, **fields) -> None:
    if not fields:
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        set_clause = ", ".join(f"{key} = %s" for key in fields)
        values = list(fields.values()) + [order_id]
        cur.execute(f"UPDATE orders SET {set_clause} WHERE order_id = %s", values)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        logger.error("Gagal update order %s dengan field %s.", order_id, list(fields.keys()))
        traceback.print_exc()
        raise


def save_support_message(support_message_id: int, user_id: int) -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO support_messages (support_message_id, user_id, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (support_message_id) DO NOTHING
            """,
            (support_message_id, user_id, datetime.now()),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        logger.error("Gagal menyimpan support_message_id %s.", support_message_id)
        traceback.print_exc()
        raise


def get_orders_by_user(user_id: int, limit: int = 10):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT * FROM orders
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception:
        logger.error("Gagal mengambil riwayat order untuk user %s.", user_id)
        traceback.print_exc()
        raise


def set_user_verified(user_id: int, verified: bool) -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO user_verification (user_id, verified, verified_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
                SET verified = EXCLUDED.verified, verified_at = EXCLUDED.verified_at
            """,
            (user_id, verified, datetime.now() if verified else None),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        logger.error("Gagal menyimpan status verifikasi user %s.", user_id)
        traceback.print_exc()
        raise


def get_order_by_group_message_id(order_group_message_id: int):
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM orders WHERE order_group_message_id = %s",
            (order_group_message_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row
    except Exception:
        logger.error(
            "Gagal mengambil order untuk order_group_message_id %s.", order_group_message_id
        )
        traceback.print_exc()
        raise


def complete_order_if_pending(order_id: str, admin_proof_file_id: str, expected_status: str):
    """Menyelesaikan order secara atomik: hanya berhasil jika status saat ini
    masih sesuai expected_status. Mencegah double-processing saat dua admin
    membalas foto secara bersamaan. Mengembalikan True jika berhasil."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE orders
            SET status = 'SELESAI', admin_proof_file_id = %s
            WHERE order_id = %s AND status = %s
            RETURNING order_id
            """,
            (admin_proof_file_id, order_id, expected_status),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return row is not None
    except Exception:
        logger.error("Gagal menyelesaikan order %s secara atomik.", order_id)
        traceback.print_exc()
        raise


def get_support_message_user(support_message_id: int):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id FROM support_messages WHERE support_message_id = %s",
            (support_message_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else None
    except Exception:
        logger.error("Gagal mengambil user untuk support_message_id %s.", support_message_id)
        traceback.print_exc()
        raise
