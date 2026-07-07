"""
Konfigurasi Bot Convert Saldo (CV Payment System)

Semua nilai sensitif diambil dari environment variable.
Kompatibel untuk dijalankan di Replit maupun Railway (dengan PostgreSQL).
"""

import os

# --- Kredensial & ID ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ORDER_GROUP_ID = os.environ.get("ORDER_GROUP_ID", "")
SUPPORT_GROUP_ID = os.environ.get("SUPPORT_GROUP_ID", "")
REQUIRED_GROUP_ID = os.environ.get("REQUIRED_GROUP_ID", "")

# Grup testimoni publik — menerima notifikasi order selesai (nomor disensor).
# Bot harus menjadi admin di grup ini agar bisa mengirim pesan.
TESTIMONI_GROUP_ID = os.environ.get("TESTIMONI_GROUP_ID", "-1003903755476")
TESTIMONI_GROUP_INVITE_LINK = os.environ.get(
    "TESTIMONI_GROUP_INVITE_LINK", "https://t.me/+cRgnoODYu-VjMGU1"
)

# Link undangan yang ditampilkan di pesan "wajib join" → arahkan ke grup testimoni.
REQUIRED_GROUP_INVITE_LINK = TESTIMONI_GROUP_INVITE_LINK

# --- Path Aset ---
QRIS_IMAGE_PATH = "assets/qris.png"

# --- Fee ---
ADMIN_FEE_PERCENT = 1  # persen

# --- Daftar Tujuan Saldo (kode internal -> label tombol) ---
EWALLET_LABELS = {
    "DANA": "\U0001F499 DANA",
    "SPay": "\U0001F6CD\uFE0F SPay",
    "SeaBank": "\U0001F30A SeaBank",
    "BNI": "\U0001F3E6 BNI",
}
EWALLET_OPTIONS = list(EWALLET_LABELS.keys())

# Semua tujuan saat ini butuh nomor/nomor rekening tujuan
EWALLET_REQUIRE_NUMBER = {"DANA", "SPay", "SeaBank", "BNI"}

# --- Jam Operasional (mudah diubah lewat Environment Variable) ---
OPERATING_TIMEZONE = os.environ.get("OPERATING_TIMEZONE", "Asia/Makassar")
OPERATING_HOUR_START = int(os.environ.get("OPERATING_HOUR_START", "6"))
OPERATING_HOUR_END = int(os.environ.get("OPERATING_HOUR_END", "21"))

# --- Validasi konfigurasi wajib ---
_REQUIRED_ENV_VARS = {
    "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
    "DATABASE_URL": DATABASE_URL,
    "ORDER_GROUP_ID": ORDER_GROUP_ID,
    "SUPPORT_GROUP_ID": SUPPORT_GROUP_ID,
    "REQUIRED_GROUP_ID": REQUIRED_GROUP_ID,
}

_missing = [key for key, value in _REQUIRED_ENV_VARS.items() if not value]
if _missing:
    raise RuntimeError(
        "Environment variable berikut belum diset: "
        + ", ".join(_missing)
        + ". Tambahkan di Replit Secrets (dev) atau Railway Variables (prod)."
    )
