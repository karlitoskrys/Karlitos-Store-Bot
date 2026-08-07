"""Configurações centralizadas da Karlitos Store."""

import os
import re
from datetime import time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import discord
from dotenv import load_dotenv

# Raiz do projeto (pasta onde ficam .env, banco, GIF e JSON).
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
SUPPORT_ROLE_ID = int(os.getenv("SUPPORT_ROLE_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
DELIVERY_CHANNEL_ID = int(os.getenv("DELIVERY_CHANNEL_ID", "0"))
STATUS_CHANNEL_ID = int(os.getenv("STATUS_CHANNEL_ID", "0"))
REVIEW_CHANNEL_ID = int(os.getenv("REVIEW_CHANNEL_ID", "0"))
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0"))
MANUAL_PIX_KEY = os.getenv("MANUAL_PIX_KEY", os.getenv("PIX_KEY", "")).strip()
MANUAL_PIX_HOLDER = os.getenv("MANUAL_PIX_HOLDER", "").strip()

# O PIX é gerado exclusivamente pelo Mercado Pago.
# Não é necessário configurar chave PIX ou imagem de QR Code no .env.

MERCADO_PAGO_ACCESS_TOKEN = os.getenv(
    "MERCADO_PAGO_ACCESS_TOKEN",
    "",
).strip()
MERCADO_PAGO_PAYER_EMAIL = os.getenv(
    "MERCADO_PAGO_PAYER_EMAIL",
    "",
).strip().lower()
MERCADO_PAGO_POLL_SECONDS = int(
    os.getenv("MERCADO_PAGO_POLL_SECONDS", "15")
)
MERCADO_PAGO_PIX_EXPIRATION = os.getenv(
    "MERCADO_PAGO_PIX_EXPIRATION",
    "PT30M",
).strip()
MERCADO_PAGO_TEST_MODE = os.getenv(
    "MERCADO_PAGO_TEST_MODE",
    "false",
).strip().lower() in {"1", "true", "yes", "sim"}

# A API Orders pode criar a cobrança de forma assíncrona.
MERCADO_PAGO_CREATE_RETRIES = int(
    os.getenv("MERCADO_PAGO_CREATE_RETRIES", "10")
)
MERCADO_PAGO_CREATE_RETRY_SECONDS = float(
    os.getenv("MERCADO_PAGO_CREATE_RETRY_SECONDS", "2")
)

# Taxa repassada ao cliente.
# Confirme a porcentagem real nos detalhes da sua conta Mercado Pago.
MERCADO_PAGO_FEE_PERCENT = Decimal(
    os.getenv("MERCADO_PAGO_FEE_PERCENT", "0.99")
)
MERCADO_PAGO_FIXED_FEE = Decimal(
    os.getenv("MERCADO_PAGO_FIXED_FEE", "0.00")
)
MANUAL_PAYMENT_THRESHOLD = Decimal(
    os.getenv("MANUAL_PAYMENT_THRESHOLD", "100.00")
)

STORE_NAME = os.getenv("STORE_NAME", "Karlitos Store").strip()

# Banner animado usado no painel principal.
RULES_SERVER_BANNER_PATH = Path(
    os.getenv(
        "RULES_SERVER_BANNER_PATH",
        str(BASE_DIR / "regras_servidor_karlitos.png"),
    )
)
RULES_PURCHASE_BANNER_PATH = Path(
    os.getenv(
        "RULES_PURCHASE_BANNER_PATH",
        str(BASE_DIR / "regras_compra_karlitos.png"),
    )
)

SHOP_MENU_GIF_PATH = Path(
    os.getenv(
        "SHOP_MENU_GIF_PATH",
        str(BASE_DIR / "karlitos_store_banner_animado.gif"),
    )
)
PRICE_PER_1000_ROBUX = 27.00

# Identidade visual da loja.
THEME_PURPLE = discord.Color.from_rgb(139, 71, 255)
THEME_DARK_PURPLE = discord.Color.from_rgb(88, 28, 135)
THEME_GOLD = discord.Color.from_rgb(255, 196, 71)
THEME_GREEN = discord.Color.from_rgb(46, 204, 113)
THEME_ORANGE = discord.Color.from_rgb(255, 145, 77)
THEME_RED = discord.Color.from_rgb(231, 76, 60)

DATABASE_PATH = BASE_DIR / "loja.db"
BACKUP_DIRECTORY = BASE_DIR / "backups"
TRANSCRIPT_DIRECTORY = BASE_DIR / "transcripts"
ROBLOX_USERNAME_ENDPOINT = "https://users.roblox.com/v1/usernames/users"
PRIVATE_SERVERS_PATH = BASE_DIR / "private_servers.json"
STORE_TIMEZONE = timezone(timedelta(hours=-3))

# Horários: segunda=0 e domingo=6. O fim 24:00 representa meia-noite.
OPENING_HOURS = {
    0: (time(8, 0), 24 * 60),
    1: (time(13, 0), 22 * 60),
    2: (time(13, 0), 24 * 60),
    3: (time(8, 0), 22 * 60),
    4: (time(13, 0), 24 * 60),
    5: (time(8, 0), 24 * 60),
    6: (time(8, 0), 24 * 60),
}

if not DISCORD_TOKEN:
    raise RuntimeError("Preencha DISCORD_TOKEN no arquivo .env")
if not GUILD_ID:
    raise RuntimeError("Preencha GUILD_ID no arquivo .env")
if not TICKET_CATEGORY_ID:
    raise RuntimeError("Preencha TICKET_CATEGORY_ID no arquivo .env")
if not SUPPORT_ROLE_ID:
    raise RuntimeError("Preencha SUPPORT_ROLE_ID no arquivo .env")
if not OWNER_USER_ID:
    raise RuntimeError(
        "Preencha OWNER_USER_ID no .env com o ID da única conta "
        "autorizada a fechar tickets manualmente."
    )

if not MERCADO_PAGO_ACCESS_TOKEN:
    raise RuntimeError(
        "Preencha MERCADO_PAGO_ACCESS_TOKEN no arquivo .env."
    )
if not MERCADO_PAGO_PAYER_EMAIL:
    raise RuntimeError(
        "Preencha MERCADO_PAGO_PAYER_EMAIL no arquivo .env."
    )
if not re.fullmatch(
    r"[^@\s]+@[^@\s]+\.[^@\s]+",
    MERCADO_PAGO_PAYER_EMAIL,
):
    raise RuntimeError(
        "MERCADO_PAGO_PAYER_EMAIL não contém um e-mail válido."
    )
if not Decimal("0") <= MERCADO_PAGO_FEE_PERCENT < Decimal("100"):
    raise RuntimeError(
        "MERCADO_PAGO_FEE_PERCENT precisa estar entre 0 e 99.99."
    )
if MERCADO_PAGO_FIXED_FEE < Decimal("0"):
    raise RuntimeError(
        "MERCADO_PAGO_FIXED_FEE não pode ser negativo."
    )
if MANUAL_PAYMENT_THRESHOLD <= Decimal("0"):
    raise RuntimeError(
        "MANUAL_PAYMENT_THRESHOLD precisa ser maior que zero."
    )
