import asyncio
import base64
import json
import os
import re
import sqlite3
import uuid
import unicodedata
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import *  # noqa: F403
from .catalog import *  # noqa: F403



# ============================================================
# FUNÇÕES
# ============================================================

def robux_to_brl(robux: int) -> float:
    return round((robux / 1000) * PRICE_PER_1000_ROBUX, 2)


def brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def safe_name(text: str) -> str:
    text = re.sub(r"[^a-z0-9-]+", "-", text.lower().strip())
    return text.strip("-")[:45] or "cliente"


def chunks(data: dict, size: int = 25) -> list[dict]:
    items = list(data.items())
    return [dict(items[i:i + size]) for i in range(0, len(items), size)]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_now() -> datetime:
    return datetime.now(STORE_TIMEZONE)


def minutes_now(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def is_store_open_by_schedule(
    moment: Optional[datetime] = None,
) -> bool:
    """Verifica somente o horário automático configurado."""
    moment = moment or local_now()
    opening, closing_minutes = OPENING_HOURS[moment.weekday()]
    opening_minutes = opening.hour * 60 + opening.minute
    current_minutes = minutes_now(moment)
    return opening_minutes <= current_minutes < closing_minutes


async def get_store_mode() -> str:
    """Retorna automatico, aberta ou fechada."""
    row = await db_fetchone(
        "SELECT value FROM store_settings WHERE key = 'store_mode'"
    )

    if not row:
        return "automatico"

    mode = str(row["value"])
    if mode not in {"automatico", "aberta", "fechada"}:
        return "automatico"

    return mode


async def is_store_open(
    moment: Optional[datetime] = None,
) -> bool:
    """O modo manual tem prioridade sobre o horário automático."""
    mode = await get_store_mode()

    if mode == "aberta":
        return True

    if mode == "fechada":
        return False

    return is_store_open_by_schedule(moment)


def format_minutes(value: int) -> str:
    if value >= 24 * 60:
        return "00:00"
    return f"{value // 60:02d}:{value % 60:02d}"


def next_opening(
    moment: Optional[datetime] = None,
) -> datetime:
    moment = moment or local_now()

    for offset in range(0, 8):
        day = moment + timedelta(days=offset)
        opening, _ = OPENING_HOURS[day.weekday()]
        candidate = day.replace(
            hour=opening.hour,
            minute=opening.minute,
            second=0,
            microsecond=0,
        )
        if candidate > moment:
            return candidate

    return moment + timedelta(days=1)


async def store_status_text() -> tuple[str, discord.Color]:
    now = local_now()
    mode = await get_store_mode()
    _, closing = OPENING_HOURS[now.weekday()]
    opened = await is_store_open(now)

    if opened:
        if mode == "aberta":
            detail = (
                "A loja foi aberta manualmente pelo proprietário.\n"
                "O sistema de compras está funcionando normalmente."
            )
        else:
            detail = (
                f"Atendimento disponível até "
                f"**{format_minutes(closing)}**.\n"
                "O sistema de compras está funcionando normalmente."
            )

        return (
            "🟢 **LOJA ABERTA**\n\n" + detail,
            THEME_GREEN,
        )

    if mode == "fechada":
        return (
            "🔴 **LOJA FECHADA**\n\n"
            "A loja foi fechada manualmente pelo proprietário.\n"
            "Novas compras estão temporariamente bloqueadas.",
            THEME_RED,
        )

    next_time = next_opening(now)
    weekday_names = [
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    ]

    return (
        "🔴 **LOJA FECHADA**\n\n"
        "O sistema de compras está indisponível fora do horário.\n"
        f"Próxima abertura: **{weekday_names[next_time.weekday()]} "
        f"às {next_time:%H:%M}**.",
        THEME_RED,
    )


async def reject_if_store_closed(
    interaction: discord.Interaction,
    *,
    use_followup: bool = False,
) -> bool:
    if await is_store_open():
        return False

    status, _ = await store_status_text()
    message = f"## 🔴 Loja fechada\n{status}"

    if use_followup:
        await interaction.followup.send(
            message,
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True,
        )

    return True



def apply_branding(
    embed: discord.Embed,
    *,
    footer: str | None = None,
    show_bot_icon: bool = True,
) -> discord.Embed:
    """Aplica a identidade visual da loja aos embeds."""
    if show_bot_icon and bot.user:
        embed.set_author(
            name=STORE_NAME,
            icon_url=bot.user.display_avatar.url,
        )

    embed.set_footer(
        text=footer or f"{STORE_NAME} • Atendimento seguro via PIX"
    )
    return embed


# ============================================================
# BANCO DE DADOS
# ============================================================

DB_LOCK = asyncio.Lock()


def init_database() -> None:
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS cart_items (
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                game TEXT NOT NULL,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                robux INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                emoji TEXT NOT NULL,
                PRIMARY KEY (user_id, item_key)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                total_robux INTEGER NOT NULL,
                total_brl REAL NOT NULL,
                items_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                paid_at TEXT,
                roblox_username TEXT,
                delivered_at TEXT,
                delivered_by INTEGER,
                closed_at TEXT,
                roblox_user_id INTEGER,
                roblox_display_name TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                code TEXT PRIMARY KEY,
                percent REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                max_uses INTEGER,
                uses INTEGER NOT NULL DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS cart_coupons (
                user_id INTEGER PRIMARY KEY,
                code TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                item_key TEXT PRIMARY KEY,
                stock INTEGER NOT NULL DEFAULT -1
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                order_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                stars INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS store_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS order_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                roblox_username TEXT NOT NULL,
                roblox_user_id INTEGER NOT NULL,
                roblox_display_name TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # Atualiza bancos antigos sem apagar pedidos existentes.
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(orders)").fetchall()
        }
        if "roblox_username" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN roblox_username TEXT"
            )
        if "delivered_at" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN delivered_at TEXT"
            )
        if "delivered_by" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN delivered_by INTEGER"
            )
        if "closed_at" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN closed_at TEXT"
            )
        if "roblox_user_id" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN roblox_user_id INTEGER"
            )
        if "roblox_display_name" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN roblox_display_name TEXT"
            )
        if "discount_code" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN discount_code TEXT"
            )
        if "discount_percent" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN discount_percent REAL DEFAULT 0"
            )
        if "subtotal_brl" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN subtotal_brl REAL"
            )
        if "recipients_complete" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN recipients_complete INTEGER DEFAULT 0"
            )
        if "mp_order_id" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN mp_order_id TEXT"
            )
        if "mp_payment_id" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN mp_payment_id TEXT"
            )
        if "mp_status" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN mp_status TEXT"
            )
        if "mp_status_detail" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN mp_status_detail TEXT"
            )
        if "mp_external_reference" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN mp_external_reference TEXT"
            )
        if "mp_idempotency_key" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN mp_idempotency_key TEXT"
            )
        if "mp_payer_email" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN mp_payer_email TEXT"
            )
        if "mp_pix_code" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN mp_pix_code TEXT"
            )
        if "mp_ticket_url" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN mp_ticket_url TEXT"
            )
        if "payment_fee_brl" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN payment_fee_brl REAL DEFAULT 0"
            )
        if "charged_total_brl" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN charged_total_brl REAL"
            )
        if "progress_message_id" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN progress_message_id INTEGER"
            )
        if "payer_bank" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN payer_bank TEXT"
            )
        if "manual_payment_review" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN manual_payment_review INTEGER DEFAULT 0"
            )
        if "recipient_locked" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN recipient_locked INTEGER DEFAULT 0"
            )
        if "payment_message_id" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN payment_message_id INTEGER"
            )
        if "pix_instruction_message_id" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN pix_instruction_message_id INTEGER"
            )
        if "pix_code_message_id" not in columns:
            db.execute(
                "ALTER TABLE orders ADD COLUMN pix_code_message_id INTEGER"
            )

        db.commit()


async def db_execute(query: str, params: tuple = ()) -> None:
    async with DB_LOCK:
        with sqlite3.connect(DATABASE_PATH) as db:
            db.execute(query, params)
            db.commit()


async def db_fetchall(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    async with DB_LOCK:
        with sqlite3.connect(DATABASE_PATH) as db:
            db.row_factory = sqlite3.Row
            return db.execute(query, params).fetchall()


async def db_fetchone(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    async with DB_LOCK:
        with sqlite3.connect(DATABASE_PATH) as db:
            db.row_factory = sqlite3.Row
            return db.execute(query, params).fetchone()


class OutOfStockError(Exception):
    pass


async def get_inventory(item_key: str) -> int:
    row = await db_fetchone(
        "SELECT stock FROM inventory WHERE item_key = ?",
        (item_key,),
    )
    return int(row["stock"]) if row else -1


async def ensure_stock(item_key: str, desired_quantity: int) -> None:
    stock = await get_inventory(item_key)
    if stock >= 0 and desired_quantity > stock:
        raise OutOfStockError(
            f"Estoque insuficiente. Disponível: {stock} unidade(s)."
        )


async def decrement_order_stock(items: list[dict]) -> None:
    for item in items:
        item_key = item["item_key"]
        quantity = int(item["quantity"])
        stock = await get_inventory(item_key)
        if stock >= 0:
            await db_execute(
                "UPDATE inventory SET stock = MAX(stock - ?, 0) "
                "WHERE item_key = ?",
                (quantity, item_key),
            )


async def get_active_coupon(user_id: int) -> Optional[sqlite3.Row]:
    return await db_fetchone(
        """
        SELECT c.*
        FROM cart_coupons cc
        JOIN coupons c ON c.code = cc.code
        WHERE cc.user_id = ? AND c.active = 1
        """,
        (user_id,),
    )


async def calculate_checkout(
    user_id: int,
    total_robux: int,
) -> tuple[float, float, Optional[str], float]:
    subtotal = robux_to_brl(total_robux)
    coupon = await get_active_coupon(user_id)

    if not coupon:
        return subtotal, subtotal, None, 0.0

    if coupon["max_uses"] is not None and int(coupon["uses"]) >= int(coupon["max_uses"]):
        await db_execute("DELETE FROM cart_coupons WHERE user_id = ?", (user_id,))
        return subtotal, subtotal, None, 0.0

    percent = max(0.0, min(float(coupon["percent"]), 100.0))
    final = round(subtotal * (1 - percent / 100), 2)
    return subtotal, final, str(coupon["code"]), percent


async def customer_history(user_id: int) -> tuple[int, float, Optional[str]]:
    row = await db_fetchone(
        """
        SELECT
            COUNT(*) AS orders_count,
            COALESCE(SUM(total_brl), 0) AS spent,
            MIN(created_at) AS first_order
        FROM orders
        WHERE user_id = ? AND status IN ('paid', 'delivered')
        """,
        (user_id,),
    )
    return (
        int(row["orders_count"] or 0),
        float(row["spent"] or 0),
        row["first_order"],
    )


async def add_to_cart(
    user_id: int,
    item_key: str,
    game: str,
    category: str,
    product: dict,
) -> int:
    row = await db_fetchone(
        "SELECT quantity FROM cart_items WHERE user_id = ? AND item_key = ?",
        (user_id, item_key),
    )

    if row:
        quantity = int(row["quantity"]) + 1
        await db_execute(
            "UPDATE cart_items SET quantity = ? WHERE user_id = ? AND item_key = ?",
            (quantity, user_id, item_key),
        )
        return quantity

    await db_execute("""
        INSERT INTO cart_items
        (user_id, item_key, game, category, name, robux, quantity, emoji)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)
    """, (
        user_id,
        item_key,
        game,
        category,
        product["name"],
        int(product["robux"]),
        product.get("emoji", "🛒"),
    ))
    return 1


async def get_cart(user_id: int) -> list[dict]:
    rows = await db_fetchall(
        "SELECT * FROM cart_items WHERE user_id = ? ORDER BY game, category, name",
        (user_id,),
    )
    return [dict(row) for row in rows]


async def clear_cart(user_id: int) -> None:
    await db_execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
    await db_execute("DELETE FROM cart_coupons WHERE user_id = ?", (user_id,))


async def remove_one(user_id: int, item_key: str) -> str:
    row = await db_fetchone(
        "SELECT name, quantity FROM cart_items WHERE user_id = ? AND item_key = ?",
        (user_id, item_key),
    )

    if not row:
        return "Esse item não está mais no carrinho."

    name = row["name"]
    quantity = int(row["quantity"])

    if quantity > 1:
        await db_execute(
            "UPDATE cart_items SET quantity = quantity - 1 "
            "WHERE user_id = ? AND item_key = ?",
            (user_id, item_key),
        )
        return f"Uma unidade de **{name}** foi removida."

    await db_execute(
        "DELETE FROM cart_items WHERE user_id = ? AND item_key = ?",
        (user_id, item_key),
    )
    return f"**{name}** foi removido do carrinho."


def cart_totals(items: list[dict]) -> tuple[int, float, int]:
    total_robux = sum(int(i["robux"]) * int(i["quantity"]) for i in items)
    total_brl = robux_to_brl(total_robux)
    total_quantity = sum(int(i["quantity"]) for i in items)
    return total_robux, total_brl, total_quantity


def cart_text(items: list[dict]) -> str:
    if not items:
        return "Seu carrinho está vazio."

    lines = []
    for item in items:
        quantity = int(item["quantity"])
        subtotal_robux = int(item["robux"]) * quantity
        subtotal_brl = robux_to_brl(subtotal_robux)

        lines.append(
            f"{item['emoji']} **{quantity}x {item['name']}**\n"
            f"└ {item['game']} • {item['category']}\n"
            f"└ {format_number(subtotal_robux)} Robux • {brl(subtotal_brl)}"
        )

    total_robux, total_brl, total_quantity = cart_totals(items)

    lines.append(
        f"\n**Itens:** {total_quantity}\n"
        f"**Total em Robux:** {format_number(total_robux)}\n"
        f"**Total a pagar:** {brl(total_brl)}"
    )

    text = "\n\n".join(lines)
    return text[:4000]


async def make_cart_embed(user_id: int) -> discord.Embed:
    items = await get_cart(user_id)

    embed = discord.Embed(
        title="🛒 Seu carrinho",
        description=(
            "Confira os produtos escolhidos antes de finalizar.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{cart_text(items)}"
        ),
        color=THEME_GOLD,
        timestamp=datetime.now(timezone.utc),
    )

    coupon = await get_active_coupon(user_id)
    if coupon:
        embed.add_field(
            name="🎟️ Cupom aplicado",
            value=(
                f"`{coupon['code']}` • **{float(coupon['percent']):g}% "
                "de desconto**"
            ),
            inline=False,
        )

    if items:
        embed.add_field(
            name="💳 Próximo passo",
            value=(
                "Use **Finalizar compra** para abrir um ticket privado "
                "com o resumo completo e os dados do PIX."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="✨ Carrinho vazio",
            value="Use **Continuar comprando** para escolher seus produtos.",
            inline=False,
        )

    return apply_branding(
        embed,
        footer=f"{STORE_NAME} • Conversão: R$ 27,00 por 1.000 Robux",
    )


async def get_ticket_category(
    guild: discord.Guild,
) -> tuple[Optional[discord.CategoryChannel], Optional[str]]:
    channel = guild.get_channel(TICKET_CATEGORY_ID)

    if channel is None:
        try:
            channel = await guild.fetch_channel(TICKET_CATEGORY_ID)
        except discord.NotFound:
            return None, "Categoria não encontrada."
        except discord.Forbidden:
            return None, "O bot não consegue visualizar a categoria."
        except discord.HTTPException as error:
            return None, str(error)

    if not isinstance(channel, discord.CategoryChannel):
        return None, "O ID configurado não pertence a uma categoria."

    return channel, None



async def validate_roblox_username(username: str) -> tuple[bool, Optional[dict], str]:
    """Confere o username na API pública oficial do Roblox."""
    payload = {
        "usernames": [username],
        "excludeBannedUsers": True,
    }

    timeout = aiohttp.ClientTimeout(total=15)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                ROBLOX_USERNAME_ENDPOINT,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status == 429:
                    return False, None, "A API do Roblox está ocupada. Tente novamente em alguns minutos."
                if response.status != 200:
                    return False, None, f"A API do Roblox respondeu com erro {response.status}."

                data = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False, None, "Não foi possível consultar o Roblox agora. Tente novamente."

    users = data.get("data", [])
    if not users:
        return False, None, "Nenhuma conta Roblox foi encontrada com esse username."

    user = users[0]
    official_name = str(user.get("name", "")).strip()

    if official_name.lower() != username.lower():
        return False, None, "O Roblox retornou uma conta diferente. Confira o username original."

    return True, user, ""


async def create_database_backup(reason: str = "automatico") -> Path:
    """Cria uma cópia consistente do SQLite sem interromper o bot."""
    BACKUP_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = BACKUP_DIRECTORY / f"loja_{reason}_{timestamp}.db"

    async with DB_LOCK:
        with sqlite3.connect(DATABASE_PATH) as source_db:
            with sqlite3.connect(destination) as backup_db:
                source_db.backup(backup_db)

    # Mantém apenas os 30 backups mais recentes.
    backups = sorted(
        BACKUP_DIRECTORY.glob("loja_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[30:]:
        try:
            old_backup.unlink()
        except OSError:
            pass

    return destination


async def create_ticket_transcript(
    channel: discord.TextChannel,
    order_id: int,
) -> Path:
    """Salva as mensagens do ticket em texto antes da exclusão."""
    TRANSCRIPT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIRECTORY / f"pedido_{order_id:06d}_{channel.id}.txt"

    lines = [
        f"TRANSCRIPT DO PEDIDO #{order_id:06d}",
        f"Canal: {channel.name} ({channel.id})",
        f"Servidor: {channel.guild.name} ({channel.guild.id})",
        f"Gerado em: {datetime.now(timezone.utc).isoformat()}",
        "=" * 80,
        "",
    ]

    try:
        async for message in channel.history(limit=None, oldest_first=True):
            timestamp = message.created_at.astimezone(timezone.utc).isoformat()
            author = f"{message.author} ({message.author.id})"
            content = message.content or "[sem texto]"

            lines.append(f"[{timestamp}] {author}")
            lines.append(content)

            for embed in message.embeds:
                if embed.title:
                    lines.append(f"[EMBED TÍTULO] {embed.title}")
                if embed.description:
                    lines.append(f"[EMBED] {embed.description}")
                for field in embed.fields:
                    lines.append(f"[CAMPO] {field.name}: {field.value}")

            for attachment in message.attachments:
                lines.append(f"[ANEXO] {attachment.filename}: {attachment.url}")

            lines.append("-" * 80)
    except discord.Forbidden:
        lines.append("[ERRO] O bot não possui permissão para ler o histórico completo.")
    except discord.HTTPException as error:
        lines.append(f"[ERRO] Falha ao obter mensagens: {error}")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def send_transcript_to_log(
    transcript_path: Path,
    order_id: int,
    channel_name: str,
) -> None:
    if not LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(LOG_CHANNEL_ID)
        except discord.DiscordException:
            return

    if isinstance(channel, discord.TextChannel):
        embed = discord.Embed(
            title="📝 Transcript de ticket",
            description=(
                f"Pedido: **#{order_id:06d}**\n"
                f"Canal encerrado: `{channel_name}`"
            ),
            color=THEME_DARK_PURPLE,
            timestamp=datetime.now(timezone.utc),
        )
        await channel.send(
            embed=embed,
            file=discord.File(transcript_path, filename=transcript_path.name),
        )


@tasks.loop(hours=24)
async def automatic_backup_loop():
    try:
        backup = await create_database_backup("diario")
        print(f"[BACKUP] Criado: {backup}")
    except Exception as error:
        print(f"[BACKUP] Falha: {error}")


@automatic_backup_loop.before_loop
async def before_automatic_backup():
    await bot.wait_until_ready()



MERCADO_PAGO_API = "https://api.mercadopago.com"


def valid_email(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            value.strip(),
        )
    )


def money_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def calculate_payment_fee(
    order_total: float | Decimal,
) -> tuple[Decimal, Decimal]:
    """
    Calcula quanto cobrar para que o valor líquido fique próximo ao
    total original depois da taxa percentual e da taxa fixa.

    Fórmula:
        valor_cobrado = (valor_desejado + taxa_fixa) / (1 - percentual)

    O resultado é arredondado para cima no centavo.
    """
    net_total = money_decimal(order_total)
    rate = MERCADO_PAGO_FEE_PERCENT / Decimal("100")

    charged_total = (
        (net_total + MERCADO_PAGO_FIXED_FEE)
        / (Decimal("1") - rate)
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_CEILING,
    )

    fee_amount = (charged_total - net_total).quantize(
        Decimal("0.01")
    )

    return fee_amount, charged_total


async def mercado_pago_request(
    method: str,
    path: str,
    *,
    json_body: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
) -> tuple[int, dict, dict]:
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key

    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                f"{MERCADO_PAGO_API}{path}",
                headers=headers,
                json=json_body,
            ) as response:
                try:
                    data = await response.json()
                except aiohttp.ContentTypeError:
                    data = {"message": await response.text()}

                return response.status, data, dict(response.headers)

    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        return 0, {"message": f"Falha de conexão: {error}"}, {}


def extract_mp_payment(order_data: dict) -> dict:
    payments = (
        order_data.get("transactions", {})
        .get("payments", [])
    )
    return payments[0] if payments else {}


def extract_pix_data(order_data: dict) -> tuple[str, str, str]:
    payment = extract_mp_payment(order_data)
    method = payment.get("payment_method", {}) or {}

    # A documentação oficial usa qr_code_base64.
    # O fallback mantém compatibilidade caso a resposta varie.
    qr_base64 = (
        method.get("qr_code_base64")
        or method.get("qr_code_based64")
        or ""
    )

    return (
        str(method.get("qr_code", "") or ""),
        str(qr_base64),
        str(method.get("ticket_url", "") or ""),
    )


def mercado_pago_is_approved(order_data: dict) -> bool:
    payment = extract_mp_payment(order_data)

    order_status = str(order_data.get("status", "")).lower()
    order_detail = str(order_data.get("status_detail", "")).lower()
    payment_status = str(payment.get("status", "")).lower()
    payment_detail = str(payment.get("status_detail", "")).lower()

    # Para Pix via Orders, aguardando costuma ser:
    # action_required / waiting_transfer.
    # A aprovação deve vir como processed/approved/accredited.
    return (
        order_status in {"processed", "approved"}
        and payment_status in {"processed", "approved"}
        and (
            order_detail in {"processed", "approved", "accredited", ""}
            or payment_detail in {"processed", "approved", "accredited", ""}
        )
    )


def format_mercado_pago_error(
    status: int,
    data: object,
) -> str:
    """Transforma a resposta de erro do Mercado Pago em texto legível."""
    if not isinstance(data, dict):
        return f"HTTP {status}: {data}"

    parts = [f"HTTP {status}"]

    error_code = data.get("error") or data.get("code")
    message = data.get("message")

    if error_code:
        parts.append(f"Código: {error_code}")

    if message:
        parts.append(f"Mensagem: {message}")

    cause = data.get("cause")
    if cause:
        if isinstance(cause, list):
            for index, item in enumerate(cause, start=1):
                if isinstance(item, dict):
                    code_value = (
                        item.get("code")
                        or item.get("error")
                        or item.get("type")
                        or "sem código"
                    )
                    description = (
                        item.get("description")
                        or item.get("message")
                        or item.get("detail")
                        or str(item)
                    )
                    parts.append(
                        f"Causa {index}: {code_value} — {description}"
                    )
                else:
                    parts.append(f"Causa {index}: {item}")
        elif isinstance(cause, dict):
            parts.append(f"Causa: {cause}")
        else:
            parts.append(f"Causa: {cause}")

    errors = data.get("errors")
    if isinstance(errors, list):
        for index, item in enumerate(errors, start=1):
            if isinstance(item, dict):
                code_value = (
                    item.get("code")
                    or item.get("error")
                    or item.get("type")
                    or "sem código"
                )
                description = (
                    item.get("message")
                    or item.get("description")
                    or item.get("detail")
                    or str(item)
                )
                parts.append(
                    f"Erro {index}: {code_value} — {description}"
                )

                details = item.get("details")
                if details:
                    parts.append(f"Detalhes {index}: {details}")
            else:
                parts.append(f"Erro {index}: {item}")

    if len(parts) == 1:
        # Limita o tamanho, mas preserva o JSON retornado para diagnóstico.
        raw = json.dumps(
            data,
            ensure_ascii=False,
            default=str,
        )
        parts.append(f"Resposta: {raw[:1200]}")

    return "\n".join(parts)[:1800]


async def create_mercado_pago_pix(
    local_order_id: int,
    amount: float,
    payer_email: str,
) -> tuple[bool, dict, str]:
    idempotency_key = str(uuid.uuid4())
    external_reference = f"discord_order_{local_order_id}"

    # O ambiente de teste da API Orders exige valores predefinidos:
    # e-mail test_user_br@testuser.com, first_name APRO e total 50.00.
    request_amount = 50.00 if MERCADO_PAGO_TEST_MODE else amount
    request_email = (
        "test_user_br@testuser.com"
        if MERCADO_PAGO_TEST_MODE
        else payer_email
    )

    payer = {"email": request_email}
    if MERCADO_PAGO_TEST_MODE:
        payer["first_name"] = "APRO"

    body = {
        "type": "online",
        "external_reference": external_reference,
        "total_amount": f"{request_amount:.2f}",
        "processing_mode": "automatic",
        "transactions": {
            "payments": [
                {
                    "amount": f"{request_amount:.2f}",
                    "payment_method": {
                        "id": "pix",
                        "type": "bank_transfer",
                    },
                    "expiration_time": MERCADO_PAGO_PIX_EXPIRATION,
                }
            ]
        },
        "payer": payer,
    }

    status, data, response_headers = await mercado_pago_request(
        "POST",
        "/v1/orders",
        json_body=body,
        idempotency_key=idempotency_key,
    )

    if status == 429:
        retry_after = response_headers.get("Retry-After", "alguns segundos")
        return (
            False,
            {},
            f"Limite temporário da API. Tente novamente em {retry_after}.",
        )

    if status not in {200, 201, 202}:
        detailed_error = format_mercado_pago_error(status, data)

        print("=" * 70)
        print("[MERCADO PAGO] Falha ao criar PIX")
        print(detailed_error)
        print(
            "[MERCADO PAGO] Dados não secretos enviados: "
            + json.dumps(
                {
                    "type": body.get("type"),
                    "external_reference": body.get("external_reference"),
                    "total_amount": body.get("total_amount"),
                    "processing_mode": body.get("processing_mode"),
                    "transactions": body.get("transactions"),
                    "payer": {
                        "email": "***@***"
                    },
                    "test_mode": MERCADO_PAGO_TEST_MODE,
                },
                ensure_ascii=False,
            )
        )
        print("=" * 70)

        return False, {}, detailed_error

    mp_order_id = str(data.get("id", "") or "")
    if not mp_order_id:
        return False, {}, "O Mercado Pago não retornou o ID da order."

    # A criação pode retornar processing sem QR. Consulta a order até
    # os dados do Pix ficarem disponíveis.
    current_data = data
    pix_code, qr_base64, ticket_url = extract_pix_data(current_data)

    for _ in range(MERCADO_PAGO_CREATE_RETRIES):
        if pix_code:
            break

        await asyncio.sleep(MERCADO_PAGO_CREATE_RETRY_SECONDS)

        get_status, fetched, _ = await mercado_pago_request(
            "GET",
            f"/v1/orders/{mp_order_id}",
        )

        if get_status == 200:
            current_data = fetched
            pix_code, qr_base64, ticket_url = extract_pix_data(current_data)

        current_status = str(current_data.get("status", "")).lower()
        if current_status in {"cancelled", "canceled", "expired", "failed"}:
            break

    payment = extract_mp_payment(current_data)
    mp_payment_id = str(payment.get("id", "") or "")

    if not pix_code:
        return (
            False,
            {
                "mp_order_id": mp_order_id,
                "idempotency_key": idempotency_key,
            },
            (
                "A order foi criada, mas o Mercado Pago ainda não "
                "disponibilizou o QR Code do Pix."
            ),
        )

    return True, {
        "mp_order_id": mp_order_id,
        "mp_payment_id": mp_payment_id,
        "mp_status": str(current_data.get("status", "") or ""),
        "mp_status_detail": str(
            current_data.get("status_detail", "") or ""
        ),
        "external_reference": external_reference,
        "idempotency_key": idempotency_key,
        "pix_code": pix_code,
        "qr_base64": qr_base64,
        "ticket_url": ticket_url,
        "charged_amount": request_amount,
        "payer_email_sent": request_email,
        "test_mode": MERCADO_PAGO_TEST_MODE,
    }, ""


async def get_mercado_pago_order(
    mp_order_id: str,
) -> tuple[bool, dict, str]:
    status, data, response_headers = await mercado_pago_request(
        "GET",
        f"/v1/orders/{mp_order_id}",
    )

    if status == 429:
        retry_after = response_headers.get("Retry-After", "alguns segundos")
        return (
            False,
            {},
            f"Limite da API; aguarde {retry_after}.",
        )

    if status != 200:
        return (
            False,
            {},
            format_mercado_pago_error(status, data),
        )

    return True, data, ""


class MercadoPagoLinkView(discord.ui.View):
    def __init__(self, ticket_url: str):
        super().__init__(timeout=None)

        if ticket_url:
            self.add_item(
                discord.ui.Button(
                    label="Abrir PIX no Mercado Pago",
                    emoji="💳",
                    style=discord.ButtonStyle.link,
                    url=ticket_url,
                )
            )


def order_games_text(order: sqlite3.Row) -> str:
    games = []
    for item in json.loads(order["items_json"]):
        game = str(item.get("game", "Outro jogo")).strip() or "Outro jogo"
        if game not in games:
            games.append(game)
    return ", ".join(games)


def compact_payment_embed(order: sqlite3.Row) -> discord.Embed:
    charged = float(
        order["charged_total_brl"]
        if order["charged_total_brl"] is not None
        else order["total_brl"]
    )
    embed = discord.Embed(
        title="✅ Pagamento confirmado",
        description=(
            f"🎮 **Jogo:** {order_games_text(order)}\n"
            f"🧾 **Pedido:** #{int(order['id']):06d}\n"
            f"💎 **Valor em Robux:** "
            f"{format_number(int(order['total_robux']))} Robux\n"
            f"💰 **Valor pago:** {brl(charged)}"
        ),
        color=THEME_GREEN,
        timestamp=datetime.now(timezone.utc),
    )
    return apply_branding(
        embed,
        footer=f"{STORE_NAME} • Pagamento confirmado",
    )


async def replace_payment_details_with_summary(
    order: sqlite3.Row,
    channel: Optional[discord.TextChannel],
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return

    if order["payment_message_id"]:
        try:
            message = await channel.fetch_message(
                int(order["payment_message_id"])
            )
            await message.edit(
                content=None,
                embed=compact_payment_embed(order),
                view=None,
                attachments=[],
            )
        except discord.DiscordException:
            pass

    for column in (
        "pix_instruction_message_id",
        "pix_code_message_id",
    ):
        message_id = order[column]
        if not message_id:
            continue
        try:
            message = await channel.fetch_message(int(message_id))
            await message.delete()
        except discord.DiscordException:
            pass


async def send_receipt_request(
    order: sqlite3.Row,
    channel: Optional[discord.TextChannel],
    *,
    manual: bool,
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return

    owner_id = int(order["user_id"])
    bank = order["payer_bank"] or "Não informado"
    charged = float(
        order["charged_total_brl"]
        if order["charged_total_brl"] is not None
        else order["total_brl"]
    )

    embed = discord.Embed(
        title=(
            "🔎 Pagamento aguardando conferência manual"
            if manual
            else "📎 Envie o comprovante do PIX"
        ),
        description=(
            f"<@{owner_id}>, o pagamento foi identificado.\n\n"
            f"🏦 **Banco informado:** {bank}\n"
            f"💰 **Valor:** {brl(charged)}\n\n"
            "Envie agora o **comprovante completo do PIX** neste ticket.\n"
            + (
                "O pedido será liberado somente após minha conferência."
                if manual
                else "O comprovante será conferido antes da entrega."
            )
        ),
        color=THEME_ORANGE if manual else THEME_GREEN,
        timestamp=datetime.now(timezone.utc),
    )
    apply_branding(
        embed,
        footer=f"{STORE_NAME} • Pedido #{int(order['id']):06d}",
    )
    await channel.send(content=f"<@{owner_id}>", embed=embed)


async def send_paid_actions(
    order: sqlite3.Row,
    *,
    confirmed_by: str,
) -> None:
    owner_id = int(order["user_id"])
    channel = bot.get_channel(int(order["channel_id"] or 0))

    if channel is None and order["channel_id"]:
        try:
            channel = await bot.fetch_channel(int(order["channel_id"]))
        except discord.DiscordException:
            channel = None

    embed = discord.Embed(
        title=f"✅ Pedido #{int(order['id']):06d} pago",
        description=(
            f"Pagamento confirmado automaticamente por **{confirmed_by}**.\n\n"
            f"<@{owner_id}>, configure agora os destinatários do pedido.\n\n"
            "Você pode usar **um nome para tudo** ou **dividir os produtos "
            "entre vários usernames do Roblox**.\n\n"
            "⚠️ Informe sempre o **@username ORIGINAL**, nunca o Display Name."
        ),
        color=THEME_GREEN,
        timestamp=datetime.now(timezone.utc),
    )

    view = RecipientManagerView(int(order["id"]), owner_id)

    if isinstance(channel, discord.TextChannel):
        await channel.send(
            content=f"<@{owner_id}>",
            embed=embed,
            view=view,
        )

        await update_order_progress(
            int(order["id"]),
            channel,
        )

    try:
        customer = await bot.fetch_user(owner_id)
        await customer.send(embed=embed, view=view)
    except discord.DiscordException:
        pass

    items = json.loads(order["items_json"])
    log = discord.Embed(
        title="✅ Pagamento automático confirmado",
        description=(
            f"**Pedido:** #{int(order['id']):06d}\n"
            f"**Cliente:** <@{owner_id}>\n"
            f"**Mercado Pago Order:** `{order['mp_order_id']}`\n\n"
            f"### Itens\n{order_items_text(items)}\n\n"
            f"**Produtos:** {brl(float(order['total_brl']))}\n"
            f"**Taxa:** {brl(float(order['payment_fee_brl'] or 0))}\n"
            f"**Cobrado no PIX:** "
            f"{brl(float(order['charged_total_brl'] or order['total_brl']))}"
        ),
        color=THEME_GREEN,
        timestamp=datetime.now(timezone.utc),
    )
    await send_log(log)


async def approve_order_from_mercado_pago(
    order: sqlite3.Row,
    mp_data: dict,
) -> bool:
    if order["status"] != "pending":
        return False

    payment = extract_mp_payment(mp_data)
    returned_amount = money_decimal(
        payment.get("amount", mp_data.get("total_amount"))
    )
    expected_amount = money_decimal(
        50.00
        if MERCADO_PAGO_TEST_MODE
        else (
            order["charged_total_brl"]
            if order["charged_total_brl"] is not None
            else order["total_brl"]
        )
    )

    if returned_amount != expected_amount:
        print(
            f"[MERCADO PAGO] Valor divergente no pedido "
            f"#{int(order['id']):06d}: esperado {expected_amount}, "
            f"recebido {returned_amount}"
        )
        return False

    if str(mp_data.get("external_reference", "")) != str(
        order["mp_external_reference"] or ""
    ):
        print(
            f"[MERCADO PAGO] Referência divergente no pedido "
            f"#{int(order['id']):06d}"
        )
        return False

    is_manual = expected_amount >= MANUAL_PAYMENT_THRESHOLD
    new_status = "payment_review" if is_manual else "paid"
    payment_id = str(payment.get("id", "") or "")

    await db_execute(
        """
        UPDATE orders
        SET status = ?,
            paid_at = ?,
            mp_payment_id = ?,
            mp_status = ?,
            mp_status_detail = ?,
            manual_payment_review = ?
        WHERE id = ? AND status = 'pending'
        """,
        (
            new_status,
            utc_now(),
            payment_id,
            str(mp_data.get("status", "") or ""),
            str(mp_data.get("status_detail", "") or ""),
            1 if is_manual else 0,
            int(order["id"]),
        ),
    )

    updated = await db_fetchone(
        "SELECT * FROM orders WHERE id = ?",
        (int(order["id"]),),
    )
    if not updated or updated["status"] != new_status:
        return False

    channel = bot.get_channel(int(updated["channel_id"] or 0))
    if channel is None and updated["channel_id"]:
        try:
            fetched = await bot.fetch_channel(int(updated["channel_id"]))
            channel = (
                fetched
                if isinstance(fetched, discord.TextChannel)
                else None
            )
        except discord.DiscordException:
            channel = None

    await replace_payment_details_with_summary(updated, channel)
    await send_receipt_request(updated, channel, manual=is_manual)

    if is_manual:
        log = discord.Embed(
            title="🔎 Pagamento aguardando revisão",
            description=(
                f"**Pedido:** #{int(updated['id']):06d}\n"
                f"**Cliente:** <@{updated['user_id']}>\n"
                f"**Banco informado:** {updated['payer_bank']}\n"
                f"**Valor pago:** "
                f"{brl(float(updated['charged_total_brl']))}\n\n"
                "Aguarde o comprovante e use o comando **/pago** neste ticket."
            ),
            color=THEME_ORANGE,
            timestamp=datetime.now(timezone.utc),
        )
        await send_log(log)
        return True

    paid_items = json.loads(updated["items_json"])
    await decrement_order_stock(paid_items)
    if updated["discount_code"]:
        await db_execute(
            "UPDATE coupons SET uses = uses + 1 WHERE code = ?",
            (updated["discount_code"],),
        )

    await send_paid_actions(updated, confirmed_by="Mercado Pago")
    return True


@tasks.loop(seconds=MERCADO_PAGO_POLL_SECONDS)
async def mercado_pago_poll_loop():
    pending_orders = await db_fetchall(
        """
        SELECT *
        FROM orders
        WHERE status = 'pending'
          AND mp_order_id IS NOT NULL
          AND mp_order_id != ''
        ORDER BY id ASC
        LIMIT 50
        """
    )

    for order in pending_orders:
        success, mp_data, error = await get_mercado_pago_order(
            str(order["mp_order_id"])
        )

        if not success:
            print(
                f"[MERCADO PAGO] Falha ao consultar "
                f"#{int(order['id']):06d}: {error}"
            )
            continue

        payment = extract_mp_payment(mp_data)
        await db_execute(
            """
            UPDATE orders
            SET mp_status = ?, mp_status_detail = ?, mp_payment_id = ?
            WHERE id = ?
            """,
            (
                str(mp_data.get("status", "") or ""),
                str(mp_data.get("status_detail", "") or ""),
                str(payment.get("id", "") or ""),
                int(order["id"]),
            ),
        )

        if mercado_pago_is_approved(mp_data):
            await approve_order_from_mercado_pago(order, mp_data)


@mercado_pago_poll_loop.before_loop
async def before_mercado_pago_poll_loop():
    await bot.wait_until_ready()


# ============================================================
# BOT
# ============================================================

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def is_support(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True

    role = interaction.guild.get_role(SUPPORT_ROLE_ID)
    return (
        role is not None
        and isinstance(interaction.user, discord.Member)
        and role in interaction.user.roles
    )



def order_items_text(items: list[dict], *, compact: bool = False) -> str:
    """Monta a lista de itens com quantidade, Robux e valor em reais."""
    if not items:
        return "Nenhum item registrado."

    lines = []
    for item in items:
        quantity = int(item["quantity"])
        unit_robux = int(item["robux"])
        subtotal_robux = unit_robux * quantity
        subtotal_brl = robux_to_brl(subtotal_robux)

        if compact:
            lines.append(
                f"{item.get('emoji', '🛒')} **{quantity}x {item['name']}** — "
                f"{format_number(subtotal_robux)} Robux • {brl(subtotal_brl)}"
            )
        else:
            lines.append(
                f"{item.get('emoji', '🛒')} **{quantity}x {item['name']}**\n"
                f"└ {item['game']} • {item['category']}\n"
                f"└ {format_number(subtotal_robux)} Robux • {brl(subtotal_brl)}"
            )

    text = "\n".join(lines) if compact else "\n\n".join(lines)
    return text[:3900]


def normalize_game_name(value: str) -> str:
    """Normaliza nomes de jogos para comparar sem acentos e diferenças."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )
    text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    aliases = {
        "blox fruit": "blox fruits",
        "bloxfruits": "blox fruits",
        "anime expedition": "anime expeditions",
        "animeexpeditions": "anime expeditions",
        "gakuran spins": "gakuran",
        "gakuran giros": "gakuran",
    }

    return aliases.get(text, text)


def load_private_servers() -> dict[str, list[dict]]:
    """Carrega os links de servidores privados do arquivo JSON."""
    if not PRIVATE_SERVERS_PATH.is_file():
        return {}

    try:
        data = json.loads(
            PRIVATE_SERVERS_PATH.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    valid: dict[str, list[dict]] = {}

    for game, servers in data.items():
        if not isinstance(game, str) or not isinstance(servers, list):
            continue

        valid_servers = []

        for server in servers:
            if not isinstance(server, dict):
                continue

            name = str(server.get("name", "")).strip()
            url = str(server.get("url", "")).strip()

            if (
                name
                and url.startswith(("https://www.roblox.com/", "https://roblox.com/"))
            ):
                valid_servers.append({
                    "name": name[:80],
                    "url": url,
                    "emoji": str(server.get("emoji", "🎮"))[:10],
                })

        if valid_servers:
            normalized_game = normalize_game_name(game)
            valid.setdefault(normalized_game, [])
            valid[normalized_game].extend(valid_servers[:20])

    return valid


def games_in_order(order: sqlite3.Row) -> list[str]:
    """Retorna os jogos presentes no pedido, sem duplicar."""
    games = []

    for item in json.loads(order["items_json"]):
        game = str(item.get("game", "")).strip()
        if game and game not in games:
            games.append(game)

    return games


def private_servers_for_order(order: sqlite3.Row) -> list[dict]:
    """Seleciona somente servidores dos jogos presentes no pedido."""
    catalog = load_private_servers()
    result = []

    for game in games_in_order(order):
        normalized_game = normalize_game_name(game)

        for server in catalog.get(normalized_game, []):
            result.append({
                "game": game,
                **server,
            })

    return result[:20]


class PrivateServerView(discord.ui.View):
    def __init__(self, order: sqlite3.Row):
        super().__init__(timeout=None)

        servers = private_servers_for_order(order)

        for index, server in enumerate(servers):
            button = discord.ui.Button(
                label=f"{server['game']} • {server['name']}"[:80],
                emoji=server.get("emoji", "🎮"),
                style=discord.ButtonStyle.link,
                url=server["url"],
                row=index // 5,
            )
            self.add_item(button)


async def build_order_progress_embed(
    order: sqlite3.Row,
) -> discord.Embed:
    paid = order["status"] in {"paid", "delivered"}
    recipients_done = bool(int(order["recipients_complete"] or 0))
    delivered = order["status"] == "delivered"

    def icon(done: bool, waiting: bool = False) -> str:
        if done:
            return "🟢"
        if waiting:
            return "🟡"
        return "⚪"

    embed = discord.Embed(
        title=f"📋 Progresso do pedido #{int(order['id']):06d}",
        description=(
            f"{icon(paid)} **Pagamento confirmado**\n"
            f"{icon(recipients_done)} **Usernames registrados**\n"
            f"{icon(delivered, waiting=paid and recipients_done and not delivered)} "
            f"**Entrega concluída**\n\n"
            + (
                "✅ Pedido finalizado com sucesso."
                if delivered
                else (
                    "⏳ Aguarde a entrega. Mantenha este ticket aberto."
                    if paid and recipients_done
                    else "Complete as etapas acima para continuar."
                )
            )
        ),
        color=(
            THEME_GREEN
            if delivered
            else THEME_ORANGE
        ),
        timestamp=datetime.now(timezone.utc),
    )

    return apply_branding(
        embed,
        footer=f"{STORE_NAME} • Acompanhamento do pedido",
    )


async def update_order_progress(
    order_id: int,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    order = await db_fetchone(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
    )

    if not order:
        return

    if channel is None and order["channel_id"]:
        channel = bot.get_channel(int(order["channel_id"]))

        if channel is None:
            try:
                fetched = await bot.fetch_channel(
                    int(order["channel_id"])
                )
                channel = (
                    fetched
                    if isinstance(fetched, discord.TextChannel)
                    else None
                )
            except discord.DiscordException:
                channel = None

    if not isinstance(channel, discord.TextChannel):
        return

    embed = await build_order_progress_embed(order)
    message = None

    if order["progress_message_id"]:
        try:
            message = await channel.fetch_message(
                int(order["progress_message_id"])
            )
        except discord.DiscordException:
            message = None

    if message:
        await message.edit(embed=embed)
        return

    message = await channel.send(embed=embed)

    await db_execute(
        "UPDATE orders SET progress_message_id = ? WHERE id = ?",
        (message.id, order_id),
    )


async def send_manual_delivery_notice(
    channel: discord.abc.Messageable,
    order: sqlite3.Row,
    customer_id: int,
) -> None:
    games = ", ".join(games_in_order(order)) or "Outro jogo"

    embed = discord.Embed(
        title="⏳ Aguardando entrega manual",
        description=(
            f"<@{customer_id}>, seus destinatários foram registrados "
            "com sucesso! ✅\n\n"
            f"**Jogo do pedido:** {games}\n\n"
            "Como este pedido não possui servidor privado configurado, "
            "a entrega será feita **manualmente**.\n\n"
            "### O que acontece agora?\n"
            "✅ Pagamento confirmado\n"
            "✅ Usernames registrados\n"
            "⏳ Aguarde neste ticket\n\n"
            "Em breve irei entrar em contato para realizar a entrega.\n\n"
            "⚠️ Mantenha este ticket aberto."
        ),
        color=THEME_ORANGE,
        timestamp=datetime.now(timezone.utc),
    )

    apply_branding(
        embed,
        footer=f"{STORE_NAME} • Entrega manual",
    )

    await channel.send(
        content=f"<@{customer_id}>",
        embed=embed,
    )


async def send_private_servers(
    channel: discord.abc.Messageable,
    order: sqlite3.Row,
    customer_id: int,
) -> bool:
    servers = private_servers_for_order(order)

    if not servers:
        await send_manual_delivery_notice(
            channel,
            order,
            customer_id,
        )
        return False

    games = ", ".join(games_in_order(order))

    embed = discord.Embed(
        title="🎮 Entre em um servidor privado",
        description=(
            f"<@{customer_id}>, escolha abaixo um servidor privado "
            "para receber seu pedido.\n\n"
            f"**Jogo(s) do pedido:** {games}\n\n"
            "### Antes de entrar\n"
            "• Entre usando uma das contas informadas no pedido.\n"
            "• Aguarde a equipe confirmar em qual servidor fará a entrega.\n"
            "• Não compartilhe os links fora deste ticket.\n"
            "• Caso um servidor esteja cheio, escolha outro botão."
        ),
        color=THEME_PURPLE,
        timestamp=datetime.now(timezone.utc),
    )

    apply_branding(
        embed,
        footer=f"{STORE_NAME} • Servidores privados para entrega",
    )

    await channel.send(
        content=f"<@{customer_id}>",
        embed=embed,
        view=PrivateServerView(order),
    )
    return True


def distributable_quantity(item: dict) -> int:
    purchased_units = int(item.get("quantity", 1))
    game = str(item.get("game", "")).strip().lower()
    name = str(item.get("name", "")).strip()

    if game == "gakuran":
        match = re.search(r"(\d+)\s*giros?", name, re.IGNORECASE)
        if match:
            return int(match.group(1)) * purchased_units

    return purchased_units


def distribution_unit(item: dict) -> str:
    game = str(item.get("game", "")).strip().lower()
    name = str(item.get("name", "")).strip()

    if game == "gakuran" and re.search(r"\d+\s*giros?", name, re.IGNORECASE):
        return "giros"

    return "unidade(s)"


def distribution_line(item: dict, quantity: int) -> str:
    if distribution_unit(item) == "giros":
        return f"**{quantity} giros** — pacote `{item['name']}`"
    return f"**{quantity}x {item['name']}**"


async def get_order_recipients(order_id: int) -> list[dict]:
    rows = await db_fetchall(
        """
        SELECT *
        FROM order_recipients
        WHERE order_id = ?
        ORDER BY item_name, id
        """,
        (order_id,),
    )
    return [dict(row) for row in rows]


def order_item_map(order: sqlite3.Row) -> dict[str, dict]:
    return {
        str(item["item_key"]): item
        for item in json.loads(order["items_json"])
    }


async def recipient_assignment_totals(order_id: int) -> dict[str, int]:
    rows = await db_fetchall(
        """
        SELECT item_key, SUM(quantity) AS assigned
        FROM order_recipients
        WHERE order_id = ?
        GROUP BY item_key
        """,
        (order_id,),
    )
    return {
        str(row["item_key"]): int(row["assigned"] or 0)
        for row in rows
    }


async def recipients_are_complete(order: sqlite3.Row) -> tuple[bool, str]:
    items = order_item_map(order)
    assigned = await recipient_assignment_totals(int(order["id"]))
    problems = []

    for item_key, item in items.items():
        purchased = distributable_quantity(item)
        distributed = assigned.get(item_key, 0)
        unit = distribution_unit(item)

        if distributed < purchased:
            problems.append(
                f"• **{item['name']}**: distribuído "
                f"**{distributed}/{purchased} {unit}** — faltam "
                f"**{purchased - distributed} {unit}**"
            )
        elif distributed > purchased:
            problems.append(
                f"• **{item['name']}**: distribuído "
                f"**{distributed}/{purchased} {unit}** — existem "
                f"**{distributed - purchased} {unit}** a mais"
            )

    if problems:
        return False, "\n".join(problems)

    return True, ""


async def recipients_text(order_id: int) -> str:
    recipients = await get_order_recipients(order_id)
    order = await db_fetchone(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
    )

    if not order:
        return "Pedido não encontrado."

    items = order_item_map(order)
    assigned_totals = await recipient_assignment_totals(order_id)

    grouped: dict[str, list[dict]] = {}
    for recipient in recipients:
        grouped.setdefault(
            str(recipient["roblox_username"]),
            [],
        ).append(recipient)

    blocks = []
    for username, entries in grouped.items():
        lines = [
            f"🎮 **@{username}**",
            f"└ Display Name: `{entries[0]['roblox_display_name'] or 'Sem Display Name'}`",
        ]
        for entry in entries:
            item = items.get(str(entry["item_key"]), {
                "name": entry["item_name"],
                "game": "",
                "quantity": entry["quantity"],
            })
            lines.append(
                f"└ {distribution_line(item, int(entry['quantity']))}"
            )
        blocks.append("\n".join(lines))

    if not blocks:
        blocks.append("Nenhum destinatário foi informado.")

    progress = ["### 📊 Total distribuído"]
    for item_key, item in items.items():
        total = distributable_quantity(item)
        distributed = assigned_totals.get(item_key, 0)
        remaining = max(total - distributed, 0)
        unit = distribution_unit(item)
        progress.append(
            f"• **{item['name']}**: **{distributed}/{total} {unit}**"
            + (
                f" — restam **{remaining} {unit}**"
                if remaining
                else " — ✅ completo"
            )
        )

    return ("\n\n".join(blocks) + "\n\n" + "\n".join(progress))[:3900]


async def recipient_progress_bar(order: sqlite3.Row) -> str:
    items = order_item_map(order)
    assigned = await recipient_assignment_totals(int(order["id"]))
    total = sum(distributable_quantity(item) for item in items.values())
    done = sum(min(assigned.get(key, 0), distributable_quantity(item)) for key, item in items.items())
    ratio = 1.0 if total <= 0 else min(done / total, 1.0)
    filled = round(ratio * 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"**Progresso:** `{bar}` **{round(ratio * 100)}%**"


async def get_order_ticket_channel(
    order: sqlite3.Row,
) -> Optional[discord.TextChannel]:
    """Obtém o canal do ticket mesmo quando a interação vem de um modal."""
    channel_id = int(order["channel_id"] or 0)
    if not channel_id:
        return None

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.DiscordException:
            return None

    return channel if isinstance(channel, discord.TextChannel) else None


def recipient_review_embed(
    order: sqlite3.Row,
    *,
    username: str,
    display_name: str,
    summary: str,
    progress: str,
    apply_to_all: bool,
) -> discord.Embed:
    guild = bot.get_guild(int(order["guild_id"] or GUILD_ID))
    customer = f"<@{int(order['user_id'])}>"
    client_icon = emoji_text(guild, "cliente")
    order_icon = emoji_text(guild, "pedido")
    robux_icon = emoji_text(guild, "robux")
    delivery_icon = emoji_text(guild, "entrega")

    mode_text = (
        "Este username receberá **todos os produtos do pedido**."
        if apply_to_all
        else "Revise a distribuição abaixo antes de confirmar."
    )

    embed = discord.Embed(
        title=f"{client_icon} Destinatário do pedido",
        description=(
            f"{order_icon} **Pedido:** `#{int(order['id']):06d}`\n"
            f"{client_icon} **Cliente:** {customer}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{robux_icon} **Username Roblox:** `@{username}`\n"
            f"🪪 **Display Name:** `{display_name or 'Sem Display Name'}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{delivery_icon} {mode_text}\n\n"
            f"{progress}\n\n"
            "### Resumo da entrega\n"
            f"{summary}\n\n"
            "Confira os dados e use os botões abaixo."
        )[:4000],
        color=THEME_PURPLE,
        timestamp=datetime.now(timezone.utc),
    )
    return apply_branding(
        embed,
        footer=f"{STORE_NAME} • Pedido #{int(order['id']):06d} • Etapa 3/3",
    )


async def clear_order_recipients(order_id: int) -> None:
    await db_execute(
        "DELETE FROM order_recipients WHERE order_id = ?",
        (order_id,),
    )
    await db_execute(
        "UPDATE orders SET recipients_complete = 0 WHERE id = ?",
        (order_id,),
    )


async def send_delivery_announcement(
    order: sqlite3.Row,
    delivered_by: discord.Member | discord.User,
) -> None:
    if not DELIVERY_CHANNEL_ID:
        return

    channel = bot.get_channel(DELIVERY_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(DELIVERY_CHANNEL_ID)
        except discord.DiscordException:
            return

    if not isinstance(channel, discord.TextChannel):
        return

    items = json.loads(order["items_json"])
    customer_mention = f"<@{order['user_id']}>"
    delivery_recipients = await recipients_text(int(order["id"]))

    embed = discord.Embed(
        title="🎉 Entrega concluída!",
        description=(
            "Mais um pedido entregue com sucesso. 💜\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Cliente:** {customer_mention}\n"
            f"🧾 **Pedido:** `#{int(order['id']):06d}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"### 🎮 Destinatários no Roblox\n"
            f"{delivery_recipients}\n\n"
            f"### 📦 Produtos entregues\n"
            f"{order_items_text(items)}\n\n"
            f"### 💰 Resumo da compra\n"
            f"**Total em Robux:** {format_number(int(order['total_robux']))}\n"
            f"**Valor total:** {brl(float(order['total_brl']))}\n\n"
            "✅ Obrigado pela confiança e preferência!"
        ),
        color=THEME_GREEN,
        timestamp=datetime.now(timezone.utc),
    )

    embed.set_author(
        name=STORE_NAME,
        icon_url=bot.user.display_avatar.url if bot.user else None,
    )
    embed.set_footer(
        text=f"Entregue por {delivered_by} • Pedido #{int(order['id']):06d}"
    )

    await channel.send(content=customer_mention, embed=embed)


async def send_log(embed: discord.Embed) -> None:
    if not LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(LOG_CHANNEL_ID)
        except discord.DiscordException:
            return

    if isinstance(channel, discord.TextChannel):
        await channel.send(embed=embed)


# ============================================================
# TICKET
# ============================================================


class RecipientUsernameModal(discord.ui.Modal):
    def __init__(
        self,
        order_id: int,
        owner_id: int,
        *,
        item_key: Optional[str] = None,
        apply_to_all: bool = False,
        remaining_quantity: Optional[int] = None,
        remaining_unit: str = "itens",
    ):
        title = (
            "Um usuário para todo o pedido"
            if apply_to_all
            else "Adicionar destinatário Roblox"
        )
        super().__init__(title=title)

        self.order_id = order_id
        self.owner_id = owner_id
        self.item_key = item_key
        self.apply_to_all = apply_to_all
        self.remaining_quantity = remaining_quantity
        self.remaining_unit = remaining_unit

        self.username = discord.ui.TextInput(
            label="Nome de usuário ORIGINAL do Roblox",
            placeholder="Exemplo: Karlos123 (não use o Display Name)",
            min_length=3,
            max_length=20,
            required=True,
        )
        self.add_item(self.username)

        if not apply_to_all:
            self.quantity = discord.ui.TextInput(
                label=f"Quantidade (restam {remaining_quantity} {remaining_unit})"[:45],
                placeholder=(
                    f"Digite até {remaining_quantity}. "
                    + ("Use múltiplos de 50." if remaining_unit == "giros" else "Ex.: 1, 2, 3...")
                )[:100],
                min_length=1,
                max_length=7,
                required=True,
            )
            self.add_item(self.quantity)


    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Somente o cliente deste pedido pode informar destinatários.",
                ephemeral=True,
            )
            return

        username = str(self.username.value).strip()

        if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
            await interaction.response.send_message(
                (
                    "❌ Username inválido.\n"
                    "Informe o nome original da conta, sem espaços."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        order = await db_fetchone(
            "SELECT * FROM orders WHERE id = ?",
            (self.order_id,),
        )

        if not order:
            await interaction.followup.send(
                "Pedido não encontrado.",
                ephemeral=True,
            )
            return

        if int(order["user_id"]) != self.owner_id:
            await interaction.followup.send(
                "Este pedido pertence a outro cliente.",
                ephemeral=True,
            )
            return

        if order["status"] != "paid":
            await interaction.followup.send(
                "Os destinatários não podem mais ser alterados.",
                ephemeral=True,
            )
            return

        if int(order["recipient_locked"] or 0):
            await interaction.followup.send(
                "🔒 Os destinatários já foram confirmados e estão bloqueados.",
                ephemeral=True,
            )
            return

        valid, roblox_user, error = await validate_roblox_username(username)

        if not valid or roblox_user is None:
            await interaction.followup.send(
                f"❌ {error}\n\nConfira o **@username original**.",
                ephemeral=True,
            )
            return

        official_username = str(roblox_user["name"])
        display_name = str(roblox_user.get("displayName", ""))
        roblox_user_id = int(roblox_user["id"])
        items = order_item_map(order)

        if self.apply_to_all:
            await clear_order_recipients(self.order_id)

            for key, item in items.items():
                await db_execute(
                    """
                    INSERT INTO order_recipients
                    (
                        order_id, item_key, item_name, quantity,
                        roblox_username, roblox_user_id,
                        roblox_display_name, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.order_id,
                        key,
                        item["name"],
                        distributable_quantity(item),
                        official_username,
                        roblox_user_id,
                        display_name,
                        utc_now(),
                    ),
                )

            await db_execute(
                """
                UPDATE orders
                SET recipients_complete = 1,
                    recipient_locked = 0
                WHERE id = ?
                """,
                (self.order_id,),
            )

            message = (
                f"Todo o pedido foi destinado para **@{official_username}**."
            )
        else:
            if not self.item_key or self.item_key not in items:
                await interaction.followup.send(
                    "Produto não encontrado neste pedido.",
                    ephemeral=True,
                )
                return

            quantity_text = str(self.quantity.value).strip()

            if not quantity_text.isdigit() or int(quantity_text) < 1:
                await interaction.followup.send(
                    "A quantidade precisa ser um número maior que zero.",
                    ephemeral=True,
                )
                return

            quantity = int(quantity_text)
            selected_item = items[self.item_key]
            purchased = distributable_quantity(selected_item)
            unit = distribution_unit(selected_item)

            # No Gakuran, a divisão só pode ser feita de 50 em 50 giros.
            if unit == "giros" and quantity % 50 != 0:
                await interaction.followup.send(
                    (
                        "❌ No Gakuran, a quantidade precisa ser um "
                        "múltiplo de **50 giros**.\n\n"
                        "Exemplos válidos: **50, 100, 150, 200, 250...**\n"
                        "Exemplos inválidos: **30, 470, 225...**"
                    ),
                    ephemeral=True,
                )
                return

            assigned = await recipient_assignment_totals(self.order_id)
            already_assigned = assigned.get(self.item_key, 0)
            remaining = purchased - already_assigned

            if remaining <= 0:
                await interaction.followup.send(
                    f"✅ **{selected_item['name']}** já foi totalmente distribuído.",
                    ephemeral=True,
                )
                return

            if quantity > remaining:
                await interaction.followup.send(
                    (
                        f"❌ Quantidade maior que o restante disponível.\n"
                        f"Já distribuído: **{already_assigned} {unit}**\n"
                        f"Total comprado: **{purchased} {unit}**\n"
                        f"Restam: **{remaining} {unit}**"
                    ),
                    ephemeral=True,
                )
                return

            await db_execute(
                """
                INSERT INTO order_recipients
                (
                    order_id, item_key, item_name, quantity,
                    roblox_username, roblox_user_id,
                    roblox_display_name, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.order_id,
                    self.item_key,
                    items[self.item_key]["name"],
                    quantity,
                    official_username,
                    roblox_user_id,
                    display_name,
                    utc_now(),
                ),
            )
            await db_execute(
                "UPDATE orders SET recipients_complete = 0 WHERE id = ?",
                (self.order_id,),
            )

            new_distributed = already_assigned + quantity
            new_remaining = purchased - new_distributed

            message = (
                f"✅ {distribution_line(selected_item, quantity)} "
                f"destinado para **@{official_username}**.\n\n"
                f"**Progresso:** {new_distributed}/{purchased} {unit}\n"
                + (
                    f"**Restante:** {new_remaining} {unit}"
                    if new_remaining
                    else "✅ **Distribuição completa deste produto.**"
                )
            )

        refreshed_order = await db_fetchone(
            "SELECT * FROM orders WHERE id = ?",
            (self.order_id,),
        )
        complete, missing = await recipients_are_complete(refreshed_order)
        summary = await recipients_text(self.order_id)
        progress = await recipient_progress_bar(refreshed_order)

        if complete:
            embed = recipient_review_embed(
                refreshed_order,
                username=official_username,
                display_name=display_name,
                summary=summary,
                progress=progress,
                apply_to_all=self.apply_to_all,
            )
            review_view = RecipientReviewView(self.order_id, self.owner_id)
            ticket_channel = await get_order_ticket_channel(refreshed_order)

            if ticket_channel is not None:
                await ticket_channel.send(
                    content=f"<@{self.owner_id}>",
                    embed=embed,
                    view=review_view,
                )
                await interaction.followup.send(
                    "✅ Username salvo. O resumo apareceu no ticket para revisão.",
                    ephemeral=True,
                )
            else:
                # Fallback: nunca esconde o resumo do cliente caso o canal não seja encontrado.
                await interaction.followup.send(
                    embed=embed,
                    view=review_view,
                    ephemeral=True,
                )
        else:
            guild = interaction.guild or bot.get_guild(GUILD_ID)
            embed = discord.Embed(
                title=f"{emoji_text(guild, 'cliente')} Etapa 2/3 • Próximo destinatário",
                description=(
                    f"{message}\n\n"
                    f"{progress}\n\n"
                    "### Distribuição atual\n"
                    f"{summary}\n\n"
                    f"⚠️ **Ainda falta distribuir:**\n{missing}\n\n"
                    "Escolha abaixo o próximo produto e informe outro username."
                ),
                color=THEME_ORANGE,
            )
            apply_branding(
                embed,
                footer=f"{STORE_NAME} • Pedido #{self.order_id:06d} • Etapa 2/3",
            )
            await interaction.followup.send(
                embed=embed,
                view=RecipientProductView(
                    self.order_id,
                    self.owner_id,
                    refreshed_order,
                ),
                ephemeral=True,
            )

class RecipientProductSelect(discord.ui.Select):
    def __init__(
        self,
        order_id: int,
        owner_id: int,
        order: sqlite3.Row,
    ):
        self.order_id = order_id
        self.owner_id = owner_id

        options = []

        # Mostra somente produtos que ainda possuem quantidade restante.
        # O total distribuído é carregado no callback antes do modal.
        for item_key, item in order_item_map(order).items():
            total = distributable_quantity(item)
            unit = distribution_unit(item)

            options.append(
                discord.SelectOption(
                    label=f"{item['name']} • {total} {unit}"[:100],
                    description=f"Selecione para informar username e quantidade."[:100],
                    value=item_key,
                    emoji=item.get("emoji", "🛒"),
                )
            )

        super().__init__(
            placeholder="Escolha o produto que será destinado",
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Este pedido pertence a outro cliente.",
                ephemeral=True,
            )
            return

        order = await db_fetchone(
            "SELECT * FROM orders WHERE id = ?",
            (self.order_id,),
        )
        if not order:
            await interaction.response.send_message(
                "Pedido não encontrado.",
                ephemeral=True,
            )
            return

        item_key = self.values[0]
        item = order_item_map(order).get(item_key)
        assigned = await recipient_assignment_totals(self.order_id)
        remaining = max(
            distributable_quantity(item) - assigned.get(item_key, 0),
            0,
        )

        if remaining <= 0:
            await interaction.response.send_message(
                "✅ Esse produto já foi totalmente distribuído. Escolha outro.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            RecipientUsernameModal(
                self.order_id,
                self.owner_id,
                item_key=item_key,
                remaining_quantity=remaining,
                remaining_unit=distribution_unit(item),
            )
        )


class RecipientProductView(discord.ui.View):
    def __init__(
        self,
        order_id: int,
        owner_id: int,
        order: sqlite3.Row,
    ):
        super().__init__(timeout=300)
        self.add_item(
            RecipientProductSelect(order_id, owner_id, order)
        )


class RecipientReviewView(discord.ui.View):
    def __init__(self, order_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.order_id = order_id
        self.owner_id = owner_id

        guild = bot.get_guild(GUILD_ID)
        confirm = discord.ui.Button(
            label="Confirmar destinatário",
            emoji=select_emoji(guild, "entrega"),
            style=discord.ButtonStyle.success,
            custom_id=f"recipients:review_confirm:{order_id}",
        )
        confirm.callback = self.confirm_recipients
        self.add_item(confirm)

        edit = discord.ui.Button(
            label="Alterar nome",
            emoji=select_emoji(guild, "cliente"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"recipients:review_edit:{order_id}",
        )
        edit.callback = self.edit_recipients
        self.add_item(edit)

    async def _get_order(self, interaction: discord.Interaction) -> Optional[sqlite3.Row]:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Somente o cliente deste pedido pode usar estes botões.",
                ephemeral=True,
            )
            return None
        order = await db_fetchone(
            "SELECT * FROM orders WHERE id = ?",
            (self.order_id,),
        )
        if not order or order["status"] != "paid":
            await interaction.response.send_message(
                "Este pedido não está disponível para edição.",
                ephemeral=True,
            )
            return None
        if int(order["recipient_locked"] or 0):
            await interaction.response.send_message(
                "🔒 Os destinatários já foram confirmados.",
                ephemeral=True,
            )
            return None
        return order

    async def confirm_recipients(self, interaction: discord.Interaction):
        order = await self._get_order(interaction)
        if not order:
            return
        complete, missing = await recipients_are_complete(order)
        if not complete:
            await interaction.response.send_message(
                f"❌ Ainda falta distribuir:\n{missing}",
                ephemeral=True,
            )
            return

        await db_execute(
            """
            UPDATE orders
            SET recipients_complete = 1, recipient_locked = 1
            WHERE id = ?
            """,
            (self.order_id,),
        )
        summary = await recipients_text(self.order_id)
        guild = interaction.guild or bot.get_guild(GUILD_ID)
        embed = discord.Embed(
            title=f"{emoji_text(guild, 'entrega')} Destinatários confirmados",
            description=(
                f"{emoji_text(guild, 'seguranca')} Os nomes foram bloqueados para "
                "evitar alterações depois da confirmação.\n\n" + summary
            ),
            color=THEME_GREEN,
            timestamp=datetime.now(timezone.utc),
        )
        apply_branding(
            embed,
            footer=f"{STORE_NAME} • Pedido #{self.order_id:06d} • Destinatários confirmados",
        )
        await interaction.response.edit_message(embed=embed, view=None)
        if interaction.channel:
            refreshed_order = await db_fetchone(
                "SELECT * FROM orders WHERE id = ?",
                (self.order_id,),
            )
            await update_order_progress(self.order_id, interaction.channel)
            await send_private_servers(
                interaction.channel,
                refreshed_order,
                self.owner_id,
            )

    async def edit_recipients(self, interaction: discord.Interaction):
        order = await self._get_order(interaction)
        if not order:
            return
        summary = await recipients_text(self.order_id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✏️ Editar destinatários",
                description=(
                    f"{summary}\n\n"
                    "Use **Adicionar vários nomes** para continuar ou "
                    "**Limpar nomes** para recomeçar."
                ),
                color=THEME_PURPLE,
            ),
            view=RecipientManagerView(self.order_id, self.owner_id),
            ephemeral=True,
        )


class RecipientManagerView(discord.ui.View):
    def __init__(self, order_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.order_id = order_id
        self.owner_id = owner_id

        guild = bot.get_guild(GUILD_ID)
        one_name = discord.ui.Button(
            label="Um nome para tudo",
            emoji=select_emoji(guild, "cliente"),
            style=discord.ButtonStyle.primary,
            custom_id=f"recipients:all:{order_id}",
        )
        one_name.callback = self.one_name
        self.add_item(one_name)

        split = discord.ui.Button(
            label="Dividir entre nomes",
            emoji=select_emoji(guild, "cliente"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"recipients:split:{order_id}",
        )
        split.callback = self.split_names
        self.add_item(split)

        view_button = discord.ui.Button(
            label="Ver distribuição",
            emoji=select_emoji(guild, "pedido"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"recipients:view:{order_id}",
        )
        view_button.callback = self.view_distribution
        self.add_item(view_button)

        clear = discord.ui.Button(
            label="Limpar nomes",
            emoji=select_emoji(guild, "fechar_ticket"),
            style=discord.ButtonStyle.danger,
            custom_id=f"recipients:clear:{order_id}",
        )
        clear.callback = self.clear_names
        self.add_item(clear)

    async def check_owner(
        self,
        interaction: discord.Interaction,
    ) -> Optional[sqlite3.Row]:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Somente o cliente deste pedido pode configurar os nomes.",
                ephemeral=True,
            )
            return None

        order = await db_fetchone(
            "SELECT * FROM orders WHERE id = ?",
            (self.order_id,),
        )

        if not order:
            await interaction.response.send_message(
                "Pedido não encontrado.",
                ephemeral=True,
            )
            return None

        if order["status"] != "paid":
            await interaction.response.send_message(
                "Os destinatários não podem ser alterados nesta etapa.",
                ephemeral=True,
            )
            return None

        if int(order["recipient_locked"] or 0):
            await interaction.response.send_message(
                "🔒 Os destinatários já foram confirmados e estão bloqueados.",
                ephemeral=True,
            )
            return None

        return order

    async def one_name(self, interaction: discord.Interaction):
        order = await self.check_owner(interaction)
        if not order:
            return

        await interaction.response.send_modal(
            RecipientUsernameModal(
                self.order_id,
                self.owner_id,
                apply_to_all=True,
            )
        )

    async def split_names(self, interaction: discord.Interaction):
        order = await self.check_owner(interaction)
        if not order:
            return

        progress = await recipient_progress_bar(order)
        await interaction.response.send_message(
            (
                f"## {emoji_text(interaction.guild, 'cliente')} Etapa 1/3 • Dividir pedido\n"
                f"{progress}\n\n"
                "`1.` Escolha um produto abaixo.\n"
                "`2.` Informe o username Roblox.\n"
                "`3.` Informe quanto essa conta receberá.\n\n"
                "O bot continuará guiando você até distribuir o pedido inteiro.\n"
                "No final, basta clicar em **Confirmar destinatários**.\n\n"
                "⚠️ Para Gakuran, use quantidades de 50 em 50."
            ),
            view=RecipientProductView(
                self.order_id,
                self.owner_id,
                order,
            ),
            ephemeral=True,
        )

    async def view_distribution(self, interaction: discord.Interaction):
        order = await self.check_owner(interaction)
        if not order:
            return

        complete, missing = await recipients_are_complete(order)
        summary = await recipients_text(self.order_id)

        embed = discord.Embed(
            title=f"📋 Distribuição do pedido #{self.order_id:06d}",
            description=(
                f"{summary}\n\n"
                + (
                    "✅ **Todos os produtos foram distribuídos.**"
                    if complete
                    else f"⚠️ **Ainda falta distribuir:**\n{missing}"
                )
            ),
            color=THEME_GREEN if complete else THEME_ORANGE,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    async def finish_names(self, interaction: discord.Interaction):
        # Compatibilidade com mensagens antigas que ainda possuem o botão.
        order = await self.check_owner(interaction)
        if not order:
            return
        complete, missing = await recipients_are_complete(order)
        if not complete:
            await interaction.response.send_message(
                f"❌ Ainda falta distribuir:\n{missing}",
                ephemeral=True,
            )
            return
        summary = await recipients_text(self.order_id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Etapa 3/3 • Revise os destinatários",
                description=f"{summary}\n\nConfirme abaixo para concluir.",
                color=THEME_GREEN,
            ),
            view=RecipientReviewView(self.order_id, self.owner_id),
            ephemeral=True,
        )

    async def clear_names(self, interaction: discord.Interaction):
        order = await self.check_owner(interaction)
        if not order:
            return

        await clear_order_recipients(self.order_id)

        await interaction.response.send_message(
            "🗑️ Todos os destinatários foram removidos. Configure novamente.",
            ephemeral=True,
        )




class ConfirmCloseView(discord.ui.View):
    def __init__(self, order_id: int, requester_id: int):
        super().__init__(timeout=60)
        self.order_id = order_id
        self.requester_id = requester_id

    @discord.ui.button(
        label="Confirmar fechamento",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Somente quem solicitou o fechamento pode confirmar.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        transcript = await create_ticket_transcript(
            interaction.channel,
            self.order_id,
        )
        await send_transcript_to_log(
            transcript,
            self.order_id,
            interaction.channel.name,
        )

        await db_execute(
            "UPDATE orders SET channel_id = NULL, closed_at = ? WHERE id = ?",
            (utc_now(), self.order_id),
        )

        try:
            await create_database_backup(f"fechamento_{self.order_id:06d}")
        except Exception as error:
            print(f"[BACKUP FECHAMENTO] {error}")

        await interaction.followup.send(
            "Transcript salvo. O ticket será apagado.",
            ephemeral=True,
        )
        await interaction.channel.delete(
            reason=f"Pedido #{self.order_id:06d} fechado por {interaction.user}"
        )

    @discord.ui.button(
        label="Cancelar",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Somente quem solicitou pode cancelar.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content="Fechamento cancelado.",
            view=None,
        )


class ReviewModal(discord.ui.Modal):
    def __init__(self, order_id: int, stars: int, owner_id: int):
        super().__init__(title=f"Avaliar pedido #{order_id:06d}")
        self.order_id = order_id
        self.stars = stars
        self.owner_id = owner_id

        self.comment = discord.ui.TextInput(
            label="Comentário (opcional)",
            placeholder="Conte como foi sua experiência com a loja.",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=600,
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Esta avaliação pertence a outro cliente.",
                ephemeral=True,
            )
            return

        existing = await db_fetchone(
            "SELECT order_id FROM reviews WHERE order_id = ?",
            (self.order_id,),
        )
        if existing:
            await interaction.response.send_message(
                "Este pedido já foi avaliado.",
                ephemeral=True,
            )
            return

        comment = str(self.comment.value).strip()
        await db_execute(
            """
            INSERT INTO reviews (order_id, user_id, stars, comment, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                self.order_id,
                self.owner_id,
                self.stars,
                comment,
                utc_now(),
            ),
        )

        await interaction.response.send_message(
            "💜 Obrigado pela sua avaliação!",
            ephemeral=True,
        )

        if REVIEW_CHANNEL_ID:
            channel = bot.get_channel(REVIEW_CHANNEL_ID)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(REVIEW_CHANNEL_ID)
                except discord.DiscordException:
                    channel = None

            if isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    title="⭐ Nova avaliação",
                    description=(
                        f"**Cliente:** <@{self.owner_id}>\n"
                        f"**Pedido:** `#{self.order_id:06d}`\n"
                        f"**Nota:** {'⭐' * self.stars}\n\n"
                        f"{comment or '*Sem comentário.*'}"
                    ),
                    color=THEME_GOLD,
                    timestamp=datetime.now(timezone.utc),
                )
                await channel.send(embed=embed)


class ReviewView(discord.ui.View):
    def __init__(self, order_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.order_id = order_id
        self.owner_id = owner_id

        for stars in range(1, 6):
            button = discord.ui.Button(
                label=str(stars),
                emoji="⭐",
                style=(
                    discord.ButtonStyle.success
                    if stars == 5
                    else discord.ButtonStyle.secondary
                ),
                custom_id=f"review:{order_id}:{stars}",
            )

            async def callback(
                interaction: discord.Interaction,
                rating: int = stars,
            ):
                if interaction.user.id != self.owner_id:
                    await interaction.response.send_message(
                        "Esta avaliação pertence a outro cliente.",
                        ephemeral=True,
                    )
                    return
                await interaction.response.send_modal(
                    ReviewModal(self.order_id, rating, self.owner_id)
                )

            button.callback = callback
            self.add_item(button)


async def request_review(order: sqlite3.Row) -> None:
    user = bot.get_user(int(order["user_id"]))
    if user is None:
        try:
            user = await bot.fetch_user(int(order["user_id"]))
        except discord.DiscordException:
            return

    embed = discord.Embed(
        title="⭐ Avalie sua compra",
        description=(
            f"Seu pedido **#{int(order['id']):06d}** foi entregue.\n\n"
            "Escolha de 1 a 5 estrelas e, se desejar, deixe um comentário."
        ),
        color=THEME_GOLD,
    )

    try:
        await user.send(
            embed=embed,
            view=ReviewView(int(order["id"]), int(order["user_id"])),
        )
    except discord.Forbidden:
        pass


class TicketAdminView(discord.ui.View):
    def __init__(self, order_id: int):
        super().__init__(timeout=None)
        self.order_id = order_id

        paid = discord.ui.Button(
            label="Marcar como pago",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"order:paid:{order_id}",
        )
        paid.callback = self.mark_paid
        self.add_item(paid)

        delivered = discord.ui.Button(
            label="Marcar como entregue",
            emoji="📦",
            style=discord.ButtonStyle.primary,
            custom_id=f"order:delivered:{order_id}",
        )
        delivered.callback = self.mark_delivered
        self.add_item(delivered)

        close = discord.ui.Button(
            label="Fechar ticket",
            emoji="🔒",
            style=discord.ButtonStyle.danger,
            custom_id=f"order:close:{order_id}",
        )
        close.callback = self.close_ticket
        self.add_item(close)

    async def mark_paid(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_USER_ID:
            await interaction.response.send_message(
                (
                    "O pagamento é confirmado automaticamente pelo "
                    "Mercado Pago. Somente o dono pode usar a "
                    "confirmação manual de emergência."
                ),
                ephemeral=True,
            )
            return

        order = await db_fetchone("SELECT * FROM orders WHERE id = ?", (self.order_id,))
        if not order:
            await interaction.response.send_message(
                "Pedido não encontrado.",
                ephemeral=True,
            )
            return

        if order["status"] in {"paid", "delivered"}:
            await interaction.response.send_message(
                "Este pedido já está pago ou entregue.",
                ephemeral=True,
            )
            return

        if order["status"] not in {"pending", "payment_review"}:
            await interaction.response.send_message(
                "Este pedido não está aguardando confirmação.",
                ephemeral=True,
            )
            return

        await db_execute(
            """
            UPDATE orders
            SET status = 'paid',
                paid_at = COALESCE(paid_at, ?),
                manual_payment_review = 0
            WHERE id = ?
            """,
            (utc_now(), self.order_id),
        )

        order = await db_fetchone(
            "SELECT * FROM orders WHERE id = ?",
            (self.order_id,),
        )
        owner_id = int(order["user_id"])

        if isinstance(interaction.channel, discord.TextChannel):
            await replace_payment_details_with_summary(
                order,
                interaction.channel,
            )

        paid_items = json.loads(order["items_json"])
        await decrement_order_stock(paid_items)
        if order["discount_code"]:
            await db_execute(
                "UPDATE coupons SET uses = uses + 1 WHERE code = ?",
                (order["discount_code"],),
            )

        embed = discord.Embed(
            title=f"✅ Pedido #{self.order_id:06d} pago",
            description=(
                f"Pagamento e comprovante confirmados por "
                f"{interaction.user.mention}.\n\n"
                f"<@{owner_id}>, configure agora os destinatários do pedido.\n\n"
                "Você pode usar **um nome para tudo** ou **dividir os produtos "
                "entre vários usernames do Roblox**.\n\n"
                "⚠️ Informe sempre o **@username ORIGINAL**, nunca o Display Name."
            ),
            color=THEME_GREEN,
        )

        await interaction.response.send_message(
            content=f"<@{owner_id}>",
            embed=embed,
            view=RecipientManagerView(self.order_id, owner_id),
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await update_order_progress(
                self.order_id,
                interaction.channel,
            )

        try:
            customer = await bot.fetch_user(owner_id)
            await customer.send(
                embed=embed,
                view=RecipientManagerView(self.order_id, owner_id),
            )
        except discord.DiscordException:
            pass

        log = discord.Embed(
            title="✅ Pagamento confirmado",
            description=(
                f"**Pedido:** #{self.order_id:06d}\n"
                f"**Cliente:** <@{owner_id}>\n"
                f"**Confirmado por:** {interaction.user.mention}\n"
                f"**Canal:** {interaction.channel.mention}\n\n"
                f"### Itens\n"
                f"{order_items_text(paid_items)}\n\n"
                f"**Total em Robux:** {format_number(int(order['total_robux']))}\n"
                f"**Valor total:** {brl(float(order['total_brl']))}"
            ),
            color=THEME_GREEN,
            timestamp=datetime.now(timezone.utc),
        )
        await send_log(log)


    async def mark_delivered(self, interaction: discord.Interaction):
        if not await is_support(interaction):
            await interaction.response.send_message(
                "Somente a equipe pode concluir uma entrega.",
                ephemeral=True,
            )
            return

        order = await db_fetchone(
            "SELECT * FROM orders WHERE id = ?",
            (self.order_id,),
        )

        if not order:
            await interaction.response.send_message(
                "Pedido não encontrado.",
                ephemeral=True,
            )
            return

        if order["status"] == "delivered":
            await interaction.response.send_message(
                "Este pedido já foi marcado como entregue.",
                ephemeral=True,
            )
            return

        if order["status"] != "paid":
            await interaction.response.send_message(
                "Confirme o pagamento antes de marcar a entrega.",
                ephemeral=True,
            )
            return

        complete, missing = await recipients_are_complete(order)

        if not complete or not int(order["recipients_complete"] or 0):
            await interaction.response.send_message(
                (
                    "O cliente ainda não concluiu a distribuição dos produtos.\n\n"
                    f"{missing or 'Peça para clicar em **Concluir nomes**.'}"
                ),
                ephemeral=True,
            )
            return

        await db_execute(
            """
            UPDATE orders
            SET status = 'delivered',
                delivered_at = ?,
                delivered_by = ?,
                recipient_locked = 1
            WHERE id = ?
            """,
            (
                utc_now(),
                interaction.user.id,
                self.order_id,
            ),
        )

        updated_order = await db_fetchone(
            "SELECT * FROM orders WHERE id = ?",
            (self.order_id,),
        )

        items = json.loads(updated_order["items_json"])
        delivery_names = await recipients_text(self.order_id)

        embed = discord.Embed(
            title=f"📦 Pedido #{self.order_id:06d} entregue",
            description=(
                f"Entrega concluída por {interaction.user.mention}.\n\n"
                f"{order_items_text(items)}\n\n"
                f"### 🎮 Destinatários\n{delivery_names}\n\n"
                f"**Valor total:** {brl(float(updated_order['total_brl']))}"
            ),
            color=THEME_GREEN,
            timestamp=datetime.now(timezone.utc),
        )

        await interaction.response.send_message(embed=embed)

        await send_delivery_announcement(
            updated_order,
            interaction.user,
        )

        log = discord.Embed(
            title="📦 Entrega concluída",
            description=(
                f"**Pedido:** #{self.order_id:06d}\n"
                f"**Cliente:** <@{updated_order['user_id']}>\n"
                f"**Entregue por:** {interaction.user.mention}\n\n"
                f"### Destinatários\n{delivery_names}\n\n"
                f"### Itens\n{order_items_text(items)}\n\n"
                f"**Total:** {brl(float(updated_order['total_brl']))}"
            ),
            color=THEME_GREEN,
            timestamp=datetime.now(timezone.utc),
        )
        await send_log(log)

        if isinstance(interaction.channel, discord.TextChannel):
            await update_order_progress(
                self.order_id,
                interaction.channel,
            )

        try:
            customer = await bot.fetch_user(int(updated_order["user_id"]))
            await customer.send(
                embed=discord.Embed(
                    title="🎉 Pedido entregue!",
                    description=(
                        f"Seu pedido **#{self.order_id:06d}** foi entregue.\n"
                        "Obrigado por comprar conosco! 💜"
                    ),
                    color=THEME_GREEN,
                )
            )
        except discord.DiscordException:
            pass

        await request_review(updated_order)

        if isinstance(interaction.channel, discord.TextChannel):
            await interaction.channel.send(
                "🔒 Este ticket será fechado automaticamente em **10 segundos**."
            )
            await asyncio.sleep(10)

            transcript = await create_ticket_transcript(
                interaction.channel,
                self.order_id,
            )
            await send_transcript_to_log(
                transcript,
                self.order_id,
                interaction.channel.name,
            )
            await db_execute(
                "UPDATE orders SET channel_id = NULL, closed_at = ? WHERE id = ?",
                (utc_now(), self.order_id),
            )

            try:
                await interaction.channel.delete(
                    reason=(
                        f"Pedido #{self.order_id:06d} entregue; "
                        "fechamento automático"
                    )
                )
            except discord.DiscordException:
                pass

    async def close_ticket(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            return

        if interaction.user.id != OWNER_USER_ID:
            await interaction.response.send_message(
                "Somente o dono da loja pode fechar tickets manualmente.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            (
                "⚠️ **Confirmar fechamento?**\n"
                "O bot salvará o transcript, fará um backup do banco e "
                "depois apagará este canal."
            ),
            view=ConfirmCloseView(self.order_id, interaction.user.id),
            ephemeral=True,
        )


async def finalize_order(
    interaction: discord.Interaction,
    payer_bank: str,
) -> None:
    payer_email = MERCADO_PAGO_PAYER_EMAIL
    items = await get_cart(interaction.user.id)

    if not items:
        await interaction.followup.send("Seu carrinho está vazio.", ephemeral=True)
        return

    if interaction.guild is None:
        await interaction.followup.send(
            "Use o carrinho dentro do servidor.",
            ephemeral=True,
        )
        return

    category, error = await get_ticket_category(interaction.guild)
    if category is None:
        await interaction.followup.send(
            f"Não foi possível abrir o ticket: {error}",
            ephemeral=True,
        )
        return

    existing = discord.utils.find(
        lambda c: (
            isinstance(c, discord.TextChannel)
            and c.topic
            and f"user_id={interaction.user.id}" in c.topic
        ),
        category.channels,
    )

    pending_database_order = await db_fetchone(
        """
        SELECT id, channel_id
        FROM orders
        WHERE user_id = ?
          AND status IN ('pending', 'payment_review')
          AND channel_id IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (interaction.user.id,),
    )

    if existing:
        await interaction.followup.send(
            f"Você já possui um ticket aberto: {existing.mention}",
            ephemeral=True,
        )
        return

    if pending_database_order:
        channel_id = int(pending_database_order["channel_id"])
        database_channel = interaction.guild.get_channel(channel_id)

        if database_channel:
            await interaction.followup.send(
                (
                    "Você já possui um pedido aguardando pagamento: "
                    f"{database_channel.mention}"
                ),
                ephemeral=True,
            )
            return

        # Corrige referência antiga caso o canal tenha sido apagado manualmente.
        await db_execute(
            "UPDATE orders SET channel_id = NULL WHERE id = ?",
            (int(pending_database_order["id"]),),
        )

    support_role = interaction.guild.get_role(SUPPORT_ROLE_ID)
    bot_member = interaction.guild.me

    if support_role is None or bot_member is None:
        await interaction.followup.send(
            "Cargo de suporte ou bot não encontrado.",
            ephemeral=True,
        )
        return

    total_robux, _, total_quantity = cart_totals(items)

    # Confere o estoque novamente no checkout.
    for item in items:
        await ensure_stock(item["item_key"], int(item["quantity"]))

    subtotal_brl, total_brl, discount_code, discount_percent = (
        await calculate_checkout(interaction.user.id, total_robux)
    )

    payment_fee, charged_total = calculate_payment_fee(total_brl)
    payment_fee_brl = float(payment_fee)
    charged_total_brl = float(charged_total)

    async with DB_LOCK:
        with sqlite3.connect(DATABASE_PATH) as db:
            cursor = db.execute("""
                INSERT INTO orders
                (
                    user_id, guild_id, status, total_robux, total_brl,
                    items_json, created_at, discount_code,
                    discount_percent, subtotal_brl, mp_payer_email,
                    payment_fee_brl, charged_total_brl, payer_bank,
                    manual_payment_review
                )
                VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                interaction.user.id,
                interaction.guild.id,
                total_robux,
                total_brl,
                json.dumps(items, ensure_ascii=False),
                utc_now(),
                discount_code,
                discount_percent,
                subtotal_brl,
                payer_email,
                payment_fee_brl,
                charged_total_brl,
                payer_bank,
                1 if money_decimal(charged_total_brl) >= MANUAL_PAYMENT_THRESHOLD else 0,
            ))
            order_id = cursor.lastrowid
            db.commit()

    mp_success, mp_payment, mp_error = await create_mercado_pago_pix(
        order_id,
        charged_total_brl,
        payer_email,
    )

    if not mp_success:
        await db_execute(
            "DELETE FROM orders WHERE id = ?",
            (order_id,),
        )
        error_embed = discord.Embed(
            title="❌ Não foi possível gerar o PIX",
            description=(
                "O Mercado Pago recusou os dados da cobrança.\n\n"
                f"```text\n{mp_error[:1800]}\n```\n"
                "O pedido não foi criado e nenhum pagamento foi cobrado."
            ),
            color=THEME_RED,
        )

        await interaction.followup.send(
            embed=error_embed,
            ephemeral=True,
        )
        return

    await db_execute(
        """
        UPDATE orders
        SET mp_order_id = ?,
            mp_payment_id = ?,
            mp_status = ?,
            mp_status_detail = ?,
            mp_external_reference = ?,
            mp_idempotency_key = ?,
            mp_pix_code = ?,
            mp_ticket_url = ?
        WHERE id = ?
        """,
        (
            mp_payment["mp_order_id"],
            mp_payment["mp_payment_id"],
            mp_payment["mp_status"],
            mp_payment["mp_status_detail"],
            mp_payment["external_reference"],
            mp_payment["idempotency_key"],
            mp_payment["pix_code"],
            mp_payment["ticket_url"],
            order_id,
        ),
    )

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
        ),
        support_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
        ),
        bot_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            attach_files=True,
        ),
    }

    try:
        ticket = await interaction.guild.create_text_channel(
            name=f"pedido-{order_id:06d}-{safe_name(interaction.user.display_name)}",
            category=category,
            overwrites=overwrites,
            topic=f"user_id={interaction.user.id};order_id={order_id}",
            reason=f"Pedido #{order_id:06d}",
        )
    except discord.DiscordException as error:
        await db_execute("DELETE FROM orders WHERE id = ?", (order_id,))
        await interaction.followup.send(
            f"Erro ao criar ticket: `{error}`",
            ephemeral=True,
        )
        return

    await db_execute(
        "UPDATE orders SET channel_id = ? WHERE id = ?",
        (ticket.id, order_id),
    )

    order_embed = discord.Embed(
        title=f"🧾 Pedido #{order_id:06d}",
        description=(
            f"Olá, {interaction.user.mention}! Seu pedido foi criado.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⏳ **STATUS: AGUARDANDO PAGAMENTO**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{cart_text(items)}\n\n"
            + (
                f"🎟️ **Cupom:** `{discount_code}` "
                f"({discount_percent:g}% OFF)\n"
                f"**Subtotal antes do cupom:** {brl(subtotal_brl)}\n"
                if discount_code
                else ""
            )
            + "### 💰 Resumo do pagamento\n"
            f"**Total dos produtos:** {brl(total_brl)}\n"
            f"**Taxa de processamento "
            f"({MERCADO_PAGO_FEE_PERCENT:g}%):** "
            f"+ {brl(payment_fee_brl)}\n"
            f"## Total do PIX: {brl(charged_total_brl)}\n\n"
            "ℹ️ A taxa é referente ao processamento do pagamento "
            "pelo Mercado Pago.\n\n"
            f"🏦 **Banco informado:** {payer_bank}\n\n"
            "### 📌 Instruções\n"
            "`1.` Pague usando o PIX exclusivo deste pedido.\n"
            "`2.` Aguarde o Mercado Pago identificar o pagamento.\n"
            "`3.` Envie o comprovante completo neste ticket.\n"
            + (
                "`4.` Este pedido exige conferência manual.\n\n"
                if money_decimal(charged_total_brl) >= MANUAL_PAYMENT_THRESHOLD
                else "`4.` Depois, configure os usernames do Roblox.\n\n"
            )
            + "⚠️ Cada pedido possui um PIX próprio. Não reutilize "
            "o código de outro pedido."
        ),
        color=THEME_ORANGE,
        timestamp=datetime.now(timezone.utc),
    )
    history_orders, history_spent, first_order = await customer_history(
        interaction.user.id
    )
    first_order_text = (
        datetime.fromisoformat(first_order).strftime("%d/%m/%Y")
        if first_order
        else "Primeira compra"
    )
    order_embed.add_field(
        name="👤 Histórico do cliente",
        value=(
            f"Pedidos concluídos: **{history_orders}**\n"
            f"Total já gasto: **{brl(history_spent)}**\n"
            f"Cliente desde: **{first_order_text}**"
        ),
        inline=False,
    )

    order_embed.add_field(
        name="🧾 Valor da cobrança",
        value=(
            f"Produtos: **{brl(total_brl)}**\n"
            f"Taxa de processamento: **+ {brl(payment_fee_brl)}**\n"
            f"Total a pagar: **{brl(charged_total_brl)}**"
        ),
        inline=False,
    )

    order_embed.add_field(
        name="🔑 PIX Copia e Cola",
        value=f"```text\n{mp_payment['pix_code']}\n```",
        inline=False,
    )
    if MERCADO_PAGO_TEST_MODE:
        order_embed.add_field(
            name="🧪 Ambiente de teste",
            value=(
                "⚠️ Esta é uma cobrança simulada de **R$ 50,00**. "
                "O Mercado Pago pode aprová-la automaticamente, "
                "sem que exista pagamento real."
            ),
            inline=False,
        )
    order_embed.add_field(
        name="⏳ Validade do PIX",
        value=(
            "A cobrança expira em aproximadamente **30 minutos**. "
            "Caso expire, solicite uma nova cobrança à equipe."
        ),
        inline=False,
    )
    order_embed.add_field(
        name="🔎 Identificador Mercado Pago",
        value=f"`{mp_payment['mp_order_id']}`",
        inline=False,
    )
    apply_branding(
        order_embed,
        footer=f"{STORE_NAME} • Pedido #{order_id:06d} • Pagamento via PIX",
    )

    qr_bytes = b""
    qr_base64 = mp_payment.get("qr_base64", "")

    if qr_base64:
        try:
            qr_bytes = base64.b64decode(qr_base64)
        except (ValueError, TypeError):
            qr_bytes = b""

    payment_view = MercadoPagoLinkView(
        mp_payment.get("ticket_url", "")
    )

    if qr_bytes:
        qr_path = Path(f"mp_pix_{order_id:06d}.png")
        qr_path.write_bytes(qr_bytes)
        qr_file = discord.File(
            qr_path,
            filename=f"pix_{order_id:06d}.png",
        )
        order_embed.set_image(
            url=f"attachment://pix_{order_id:06d}.png"
        )
        payment_message = await ticket.send(
            content=interaction.user.mention,
            embed=order_embed,
            file=qr_file,
            view=payment_view,
        )
        try:
            qr_path.unlink()
        except OSError:
            pass
    else:
        payment_message = await ticket.send(
            content=interaction.user.mention,
            embed=order_embed,
            view=payment_view,
        )

    instruction_message = await ticket.send(
        "📋 **No celular, toque e segure na próxima mensagem "
        "para copiar o PIX:**"
    )
    pix_code_message = await ticket.send(mp_payment["pix_code"])

    await db_execute(
        """
        UPDATE orders
        SET payment_message_id = ?,
            pix_instruction_message_id = ?,
            pix_code_message_id = ?
        WHERE id = ?
        """,
        (
            payment_message.id,
            instruction_message.id,
            pix_code_message.id,
            order_id,
        ),
    )

    await ticket.send(
        "🛠️ **Comandos da equipe**\n"
        "Use `/pago`, `/entregue`, `/fechar`, `/pix`, `/pedido`, "
        "`/comprovante` ou `/cancelar` neste ticket."
    )

    # O estoque e o uso do cupom só são confirmados depois
    # que o Mercado Pago aprovar o pagamento.
    await clear_cart(interaction.user.id)

    await interaction.followup.send(
        f"Pedido criado: {ticket.mention}",
        ephemeral=True,
    )

    log = discord.Embed(
        title="🧾 Novo pedido criado",
        description=(
            f"**Pedido:** #{order_id:06d}\n"
            f"**Cliente:** {interaction.user.mention}\n"
            f"**Canal:** {ticket.mention}\n\n"
            f"### Itens\n"
            f"{order_items_text(items)}\n\n"
            f"**Quantidade total:** {total_quantity}\n"
            f"**Total em Robux:** {format_number(total_robux)}\n"
            f"**Valor total:** {brl(total_brl)}"
        ),
        color=THEME_ORANGE,
        timestamp=datetime.now(timezone.utc),
    )
    await send_log(log)


class BankSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
        super().__init__(
            placeholder="Selecione o banco usado no PIX",
            options=[
                discord.SelectOption(label="Nubank", value="Nubank", emoji="💜"),
                discord.SelectOption(label="Caixa", value="Caixa", emoji="🏦"),
                discord.SelectOption(label="Itaú", value="Itaú", emoji="🏦"),
                discord.SelectOption(label="Bradesco", value="Bradesco", emoji="🏦"),
                discord.SelectOption(label="Santander", value="Santander", emoji="🏦"),
                discord.SelectOption(label="Banco do Brasil", value="Banco do Brasil", emoji="🏦"),
                discord.SelectOption(label="Mercado Pago", value="Mercado Pago", emoji="💳"),
                discord.SelectOption(label="Inter", value="Inter", emoji="⚠️"),
                discord.SelectOption(label="PicPay", value="PicPay", emoji="⚠️"),
                discord.SelectOption(label="Outro banco", value="Outro banco", emoji="🏦"),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Este carrinho pertence a outra pessoa.",
                ephemeral=True,
            )
            return

        bank = self.values[0]
        if bank in {"Inter", "PicPay"}:
            await interaction.response.edit_message(
                content=(
                    "❌ **Forma de pagamento não aceita**\n\n"
                    f"Não aceitamos pagamentos pelo **{bank}**.\n"
                    "Escolha outro banco para continuar."
                ),
                embed=None,
                view=BankSelectView(self.owner_id),
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await finalize_order(interaction, bank)


class BankSelectView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.add_item(BankSelect(owner_id))


# ============================================================
# CARRINHO
# ============================================================

class RemoveItemSelect(discord.ui.Select):
    def __init__(self, owner_id: int, items: list[dict]):
        self.owner_id = owner_id

        options = [
            discord.SelectOption(
                label=f"{item['quantity']}x {item['name']}"[:100],
                description=(
                    f"Remover 1 unidade • "
                    f"{format_number(item['robux'])} Robux cada"
                )[:100],
                value=item["item_key"],
                emoji=item["emoji"],
            )
            for item in items[:25]
        ]

        super().__init__(
            placeholder="Escolha um item para remover",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Este carrinho pertence a outra pessoa.",
                ephemeral=True,
            )
            return

        message = await remove_one(self.owner_id, self.values[0])
        await interaction.response.edit_message(
            content=message,
            embed=await make_cart_embed(self.owner_id),
            view=CartView(self.owner_id),
        )


class RemoveItemView(discord.ui.View):
    def __init__(self, owner_id: int, items: list[dict]):
        super().__init__(timeout=180)
        self.add_item(RemoveItemSelect(owner_id, items))


class CartView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id

    async def owner_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Este carrinho pertence a outra pessoa.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Continuar comprando",
        emoji="➕",
        style=discord.ButtonStyle.primary,
    )
    async def continue_shopping(self, interaction, button):
        if not await self.owner_check(interaction):
            return

        await interaction.response.send_message(
            embed=main_store_embed(),
            view=MainStoreView(),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Remover item",
        emoji="➖",
        style=discord.ButtonStyle.secondary,
    )
    async def remove_item(self, interaction, button):
        if not await self.owner_check(interaction):
            return

        items = await get_cart(self.owner_id)
        if not items:
            await interaction.response.send_message(
                "Seu carrinho está vazio.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Escolha o item para remover:",
            embed=await make_cart_embed(self.owner_id),
            view=RemoveItemView(self.owner_id, items),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Limpar carrinho",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
    )
    async def clear(self, interaction, button):
        if not await self.owner_check(interaction):
            return

        await clear_cart(self.owner_id)
        await interaction.response.edit_message(
            content="Carrinho esvaziado.",
            embed=await make_cart_embed(self.owner_id),
            view=CartView(self.owner_id),
        )

    @discord.ui.button(
        label="Finalizar compra",
        emoji="💳",
        style=discord.ButtonStyle.success,
    )
    async def checkout(self, interaction, button):
        if not await self.owner_check(interaction):
            return

        if await reject_if_store_closed(interaction):
            return

        await interaction.response.send_message(
            (
                "🏦 **Qual banco você usará para pagar?**\n\n"
                "⚠️ Não aceitamos **Banco Inter** ou **PicPay**."
            ),
            view=BankSelectView(self.owner_id),
            ephemeral=True,
        )


# ============================================================
# MENUS DE PRODUTOS
# ============================================================

class ProductSelect(discord.ui.Select):
    def __init__(
        self,
        game: str,
        category: str,
        prefix: str,
        products: dict,
        placeholder: str,
    ):
        self.game = game
        self.category = category
        self.prefix = prefix
        self.products = products

        options = []
        for product_id, product in products.items():
            robux = int(product["robux"])
            options.append(
                discord.SelectOption(
                    label=(
                        f"{product['name']} • {format_number(robux)} Robux "
                        f"• {brl(robux_to_brl(robux))}"
                    )[:100],
                    description=(
                        f"{format_number(robux)} Robux — "
                        f"você paga {brl(robux_to_brl(robux))}"
                    )[:100],
                    value=product_id,
                    emoji=product.get("emoji", "🛒"),
                )
            )

        super().__init__(
            placeholder=placeholder[:150],
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if await reject_if_store_closed(interaction):
            return

        product_id = self.values[0]
        product = self.products[product_id]
        item_key = f"{self.prefix}:{product_id}"

        current = await db_fetchone(
            "SELECT quantity FROM cart_items WHERE user_id = ? AND item_key = ?",
            (interaction.user.id, item_key),
        )
        desired = (int(current["quantity"]) if current else 0) + 1

        try:
            await ensure_stock(item_key, desired)
        except OutOfStockError as error:
            await interaction.response.send_message(
                f"❌ {error}",
                ephemeral=True,
            )
            return

        quantity = await add_to_cart(
            interaction.user.id,
            item_key,
            self.game,
            self.category,
            product,
        )

        await interaction.response.send_message(
            (
                f"✅ **{product['name']}** adicionado ao carrinho.\n"
                f"Quantidade deste produto: **{quantity}**"
            ),
            embed=await make_cart_embed(interaction.user.id),
            view=CartView(interaction.user.id),
            ephemeral=True,
        )


class ProductView(discord.ui.View):
    def __init__(self, game, category, prefix, products, placeholder):
        super().__init__(timeout=300)
        self.add_item(
            ProductSelect(game, category, prefix, products, placeholder)
        )


def category_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=(
            f"{description}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💱 **Conversão:** R$ 27,00 por 1.000 Robux\n"
            "🛒 Escolha um produto para adicioná-lo ao carrinho.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=THEME_PURPLE,
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(
        name="🔐 Compra segura",
        value=(
            "O pagamento é realizado via PIX dentro de um ticket privado. "
            "A entrega só é concluída após a confirmação."
        ),
        inline=False,
    )

    return apply_branding(
        embed,
        footer=f"{STORE_NAME} • Selecione uma opção no menu abaixo",
    )


# ============================================================
# SUBMENUS
# ============================================================

class BloxCategorySelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Escolha uma categoria do Blox Fruits",
            options=[
                discord.SelectOption(
                    label="Gamepasses",
                    value="passes",
                    emoji="🎫",
                ),
                discord.SelectOption(
                    label="Frutas permanentes — página 1",
                    value="fruits_1",
                    emoji="🍎",
                ),
                discord.SelectOption(
                    label="Frutas permanentes — página 2",
                    value="fruits_2",
                    emoji="🐉",
                ),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        fruit_pages = chunks(BLOX_FRUITS)

        if self.values[0] == "passes":
            products = BLOX_GAMEPASSES
            category = "Gamepasses"
            prefix = "blox_pass"
        elif self.values[0] == "fruits_1":
            products = fruit_pages[0]
            category = "Frutas permanentes — página 1"
            prefix = "blox_fruit"
        else:
            products = fruit_pages[1]
            category = "Frutas permanentes — página 2"
            prefix = "blox_fruit"

        await interaction.response.send_message(
            embed=category_embed(f"🐉 Blox Fruits • {category}", "Produtos disponíveis."),
            view=ProductView(
                "Blox Fruits",
                category,
                prefix,
                products,
                "Escolha um produto",
            ),
            ephemeral=True,
        )


class BloxCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(BloxCategorySelect())


class AnimeCategorySelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Escolha uma categoria do Anime Expeditions",
            options=[
                discord.SelectOption(label="Gamepasses", value="passes", emoji="🎫"),
                discord.SelectOption(label="Bundles", value="bundles", emoji="📦"),
                discord.SelectOption(label="Trait Crystals", value="traits", emoji="💎"),
                discord.SelectOption(label="Villain Coins", value="coins", emoji="🔴"),
                discord.SelectOption(label="Potions", value="potions", emoji="🧪"),
                discord.SelectOption(label="Gems", value="gems", emoji="💠"),
                discord.SelectOption(label="Stat Packs", value="stats", emoji="🔷"),
                discord.SelectOption(label="Equipment Packs", value="equipment", emoji="🟧"),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        mapping = {
            "passes": ("Gamepasses", "anime_pass", ANIME_GAMEPASSES),
            "bundles": ("Bundles", "anime_bundle", ANIME_BUNDLES),
            "traits": ("Trait Crystals", "anime_trait", ANIME_TRAIT_CRYSTALS),
            "coins": ("Villain Coins", "anime_coin", ANIME_VILLAIN_COINS),
            "potions": ("Potions", "anime_potion", ANIME_POTIONS),
            "gems": ("Gems", "anime_gem", ANIME_GEMS),
            "stats": ("Stat Packs", "anime_stat", ANIME_STAT_PACKS),
            "equipment": (
                "Equipment Packs",
                "anime_equipment",
                ANIME_EQUIPMENT_PACKS,
            ),
        }

        category, prefix, products = mapping[self.values[0]]

        await interaction.response.send_message(
            embed=category_embed(
                f"⚔️ Anime Expeditions • {category}",
                "Preços originais, sem o desconto de 20%.",
            ),
            view=ProductView(
                "Anime Expeditions",
                category,
                prefix,
                products,
                "Escolha um produto",
            ),
            ephemeral=True,
        )


class AnimeCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AnimeCategorySelect())




class CustomProductModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Produto de outro jogo")

        self.game_name = discord.ui.TextInput(
            label="Nome do jogo",
            placeholder="Exemplo: Anime Fighters",
            min_length=2,
            max_length=50,
            required=True,
        )

        self.product_name = discord.ui.TextInput(
            label="Nome do passe ou produto",
            placeholder="Exemplo: VIP",
            min_length=2,
            max_length=80,
            required=True,
        )

        self.robux_value = discord.ui.TextInput(
            label="Valor em Robux",
            placeholder="Exemplo: 450",
            min_length=1,
            max_length=9,
            required=True,
        )

        self.add_item(self.game_name)
        self.add_item(self.product_name)
        self.add_item(self.robux_value)

    async def on_submit(self, interaction: discord.Interaction):
        if await reject_if_store_closed(interaction):
            return

        robux_text = str(self.robux_value.value).strip()
        robux_text = robux_text.replace(".", "").replace(",", "").replace(" ", "")

        if not robux_text.isdigit():
            await interaction.response.send_message(
                "❌ Digite somente números no valor em Robux.",
                ephemeral=True,
            )
            return

        robux = int(robux_text)

        if robux < 1:
            await interaction.response.send_message(
                "❌ O valor em Robux precisa ser maior que zero.",
                ephemeral=True,
            )
            return

        if robux > 10_000_000:
            await interaction.response.send_message(
                "❌ O valor informado é muito alto.",
                ephemeral=True,
            )
            return

        game = str(self.game_name.value).strip()
        product_name = str(self.product_name.value).strip()

        product = {
            "name": product_name,
            "robux": robux,
            "emoji": "🎮",
        }

        safe_game = re.sub(r"[^a-z0-9]+", "_", game.lower()).strip("_")
        safe_product = re.sub(
            r"[^a-z0-9]+",
            "_",
            product_name.lower(),
        ).strip("_")

        item_key = (
            f"custom:{interaction.user.id}:"
            f"{safe_game}:{safe_product}:{robux}"
        )

        quantity = await add_to_cart(
            user_id=interaction.user.id,
            item_key=item_key,
            game=game,
            category="Produto personalizado",
            product=product,
        )

        price = robux_to_brl(robux)

        await interaction.response.send_message(
            (
                "✅ **Produto personalizado adicionado ao carrinho!**\n\n"
                f"**Jogo:** {game}\n"
                f"**Produto:** {product_name}\n"
                f"**Preço:** {format_number(robux)} Robux\n"
                f"**Valor calculado:** {brl(price)}\n"
                f"**Quantidade deste produto:** {quantity}"
            ),
            embed=await make_cart_embed(interaction.user.id),
            view=CartView(interaction.user.id),
            ephemeral=True,
        )


# ============================================================
# EMOJIS PERSONALIZADOS DO SERVIDOR
# ============================================================

CUSTOM_EMOJI_NAMES = {
    "logo": "karlitos",
    "anime": "anime",
    "avaliacao": "avaliacao",
    "carrinho": "carrinho",
    "cliente": "clientes",
    "comprar": "comprar",
    "entrega": "entrega",
    "estatistica": "estatistica",
    "estoque": "estoque",
    "fechar_ticket": "fecharticket",
    "gakuran": "gakuran",
    "offline": "offline",
    "online": "online",
    "outro_jogo": "outros_jogos",
    "pedido": "pedido",
    "pix": "pix",
    "robux": "robux",
    "seguranca": "seguranca",
    "suporte": "suporte",
    "ticket": "ticket",
    "vip": "vip",
    "aguardando": "aguardando",
}

EMOJI_FALLBACKS = {
    "logo": "💜",
    "anime": "⚔️",
    "avaliacao": "⭐",
    "carrinho": "🛒",
    "cliente": "👤",
    "comprar": "🛍️",
    "entrega": "📦",
    "estatistica": "📈",
    "estoque": "📦",
    "fechar_ticket": "🔒",
    "gakuran": "🎡",
    "offline": "🔴",
    "online": "🟢",
    "outro_jogo": "🎮",
    "pedido": "🧾",
    "pix": "💠",
    "robux": "💎",
    "seguranca": "🛡️",
    "suporte": "🎧",
    "ticket": "🎫",
    "vip": "👑",
    "aguardando": "⏳",
}

CUSTOM_EMOJI_ALIASES = {
    # Aceita variações comuns do nome usado no Discord.
    "outro_jogo": (
        "outros_jogos",
        "outrosjogos",
        "outros_jogo",
        "outrojogo",
        "outro_jogo",
    ),
}

def find_custom_emoji(
    guild: Optional[discord.Guild],
    key: str,
) -> Optional[discord.Emoji]:
    if guild is None:
        return None

    primary_name = CUSTOM_EMOJI_NAMES.get(key, key)
    possible_names = (primary_name, *CUSTOM_EMOJI_ALIASES.get(key, ()))

    for emoji_name in dict.fromkeys(possible_names):
        emoji = discord.utils.get(guild.emojis, name=emoji_name)
        if emoji is not None:
            return emoji

    # Última tentativa: ignora underscores e diferenças entre maiúsculas.
    normalized_targets = {
        name.replace("_", "").lower()
        for name in possible_names
    }
    for emoji in guild.emojis:
        normalized_name = emoji.name.replace("_", "").lower()
        if normalized_name in normalized_targets:
            return emoji

    return None

def emoji_text(guild: Optional[discord.Guild], key: str) -> str:
    emoji = find_custom_emoji(guild, key)
    if emoji is not None:
        return str(emoji)
    return EMOJI_FALLBACKS.get(key, "✨")

def select_emoji(
    guild: Optional[discord.Guild],
    key: str,
) -> str | discord.PartialEmoji:
    emoji = find_custom_emoji(guild, key)
    if emoji is not None:
        return emoji
    return EMOJI_FALLBACKS.get(key, "✨")


class MainGameSelect(discord.ui.Select):
    def __init__(self, guild: Optional[discord.Guild] = None):
        super().__init__(
            placeholder="Escolha um jogo e comece sua compra",
            custom_id="store:main_game_select",
            options=[
                discord.SelectOption(
                    label="Blox Fruits",
                    value="blox",
                    emoji=select_emoji(guild, "robux"),
                    description="Gamepasses e frutas permanentes.",
                ),
                discord.SelectOption(
                    label="Gakuran",
                    value="gakuran",
                    emoji=select_emoji(guild, "gakuran"),
                    description="Pacotes de 50 até 1.000 giros.",
                ),
                discord.SelectOption(
                    label="Anime Expeditions",
                    value="anime",
                    emoji=select_emoji(guild, "anime"),
                    description="Gamepasses, bundles e outros itens.",
                ),
                discord.SelectOption(
                    label="Outro jogo",
                    value="other_game",
                    emoji=select_emoji(guild, "outro_jogo"),
                    description="Pedido personalizado para outro jogo.",
                ),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        guild = interaction.guild

        if selected == "other_game":
            await interaction.response.send_modal(CustomProductModal())
            return

        if selected == "blox":
            await interaction.response.send_message(
                embed=category_embed(
                    f"{emoji_text(guild, 'robux')} Blox Fruits",
                    "Escolha Gamepasses ou Frutas permanentes.",
                ),
                view=BloxCategoryView(),
                ephemeral=True,
            )
            return

        if selected == "gakuran":
            await interaction.response.send_message(
                embed=category_embed(
                    f"{emoji_text(guild, 'gakuran')} Gakuran • Giros",
                    "Pacotes de 50 a 1.000 giros. Cada giro equivale a 3 Robux.",
                ),
                view=ProductView(
                    "Gakuran",
                    "Giros",
                    "gakuran_spin",
                    GAKURAN_SPINS,
                    "Escolha a quantidade de giros",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=category_embed(
                f"{emoji_text(guild, 'anime')} Anime Expeditions",
                "Escolha uma categoria da loja.",
            ),
            view=AnimeCategoryView(),
            ephemeral=True,
        )


class MainStoreView(discord.ui.View):
    def __init__(self, guild: Optional[discord.Guild] = None):
        super().__init__(timeout=None)
        self.add_item(MainGameSelect(guild))



def main_store_embed(
    guild: Optional[discord.Guild] = None,
) -> discord.Embed:
    logo = emoji_text(guild, "logo")
    comprar = emoji_text(guild, "comprar")
    robux = emoji_text(guild, "robux")
    gakuran = emoji_text(guild, "gakuran")
    anime = emoji_text(guild, "anime")
    outro = emoji_text(guild, "outro_jogo")
    pix = emoji_text(guild, "pix")
    ticket = emoji_text(guild, "ticket")
    seguranca = emoji_text(guild, "seguranca")
    carrinho = emoji_text(guild, "carrinho")
    entrega = emoji_text(guild, "entrega")
    online = emoji_text(guild, "online")

    embed = discord.Embed(
        title=f"{logo} {STORE_NAME.upper()}",
        description=(
            "### Sua loja de confiança para Roblox\n"
            "Escolha um jogo no menu abaixo, monte seu carrinho e "
            "finalize sua compra com segurança.\n\n"
            f"{pix} **PIX automático**  •  "
            f"{ticket} **Ticket privado**  •  "
            f"{seguranca} **Pedido registrado**\n"
            f"{online} Consulte o funcionamento atual usando `/status`."
        ),
        color=THEME_PURPLE,
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(
        name=f"{robux} Blox Fruits",
        value="Gamepasses e frutas permanentes.",
        inline=True,
    )
    embed.add_field(
        name=f"{gakuran} Gakuran",
        value="Pacotes de 50 até 1.000 giros.",
        inline=True,
    )
    embed.add_field(
        name=f"{anime} Anime Expeditions",
        value="Bundles, Gems, Potions, Gamepasses e mais.",
        inline=True,
    )
    embed.add_field(
        name=f"{outro} Outro jogo",
        value="Informe jogo, produto e valor em Robux.",
        inline=True,
    )
    embed.add_field(
        name=f"{robux} Conversão",
        value=(
            "**1.000 Robux = R$ 27,00**\n"
            "O preço é calculado automaticamente."
        ),
        inline=True,
    )
    embed.add_field(
        name=f"{entrega} Benefícios",
        value=(
            f"{pix} PIX rápido\n"
            f"{ticket} Atendimento por ticket\n"
            f"{seguranca} Compra acompanhada"
        ),
        inline=True,
    )
    embed.add_field(
        name=f"{carrinho} Como comprar",
        value=(
            f"`1.` {comprar} Escolha um jogo\n"
            "`2.` Adicione os produtos ao carrinho\n"
            "`3.` Finalize para abrir seu ticket\n"
            "`4.` Escolha o banco e realize o PIX\n"
            "`5.` Envie o comprovante e informe o username"
        ),
        inline=False,
    )

    embed.set_image(url="attachment://karlitos_store_banner_animado.gif")

    return apply_branding(
        embed,
        footer=(
            f"{STORE_NAME} • Use /carrinho para consultar seus produtos"
        ),
    )


# ============================================================
# COMANDOS
# ============================================================

async def build_status_embed() -> discord.Embed:
    text, color = await store_status_text()
    opened = await is_store_open()
    mode = await get_store_mode()

    mode_names = {
        "automatico": "🕒 Horário automático",
        "aberta": "🟢 Aberta manualmente",
        "fechada": "🔴 Fechada manualmente",
    }

    embed = discord.Embed(
        title="📊 Status da loja",
        description=(
            f"{text}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 Pagamentos: "
            f"{'✅ Disponíveis' if opened else '❌ Indisponíveis'}\n"
            f"📦 Entregas: "
            f"{'✅ Em funcionamento' if opened else '⏸️ Pausadas'}\n"
            f"🎫 Novos pedidos: "
            f"{'✅ Liberados' if opened else '❌ Bloqueados'}\n"
            f"⚙️ Modo: **{mode_names[mode]}**"
        ),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    return apply_branding(
        embed,
        footer=f"{STORE_NAME} • Atualização automática",
    )


async def update_status_message() -> None:
    if not STATUS_CHANNEL_ID:
        return

    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(STATUS_CHANNEL_ID)
        except discord.DiscordException:
            return

    if not isinstance(channel, discord.TextChannel):
        return

    row = await db_fetchone(
        "SELECT value FROM store_settings WHERE key = 'status_message_id'"
    )
    message = None

    if row and row["value"]:
        try:
            message = await channel.fetch_message(int(row["value"]))
        except discord.DiscordException:
            message = None

    embed = await build_status_embed()

    if message:
        await message.edit(embed=embed)
    else:
        message = await channel.send(embed=embed)
        await db_execute(
            """
            INSERT INTO store_settings (key, value)
            VALUES ('status_message_id', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(message.id),),
        )


@tasks.loop(minutes=1)
async def status_loop():
    await update_status_message()


@status_loop.before_loop
async def before_status_loop():
    await bot.wait_until_ready()


@bot.event
async def setup_hook():
    init_database()

    existing_mode = await db_fetchone(
        "SELECT value FROM store_settings WHERE key = 'store_mode'"
    )
    if not existing_mode:
        await db_execute(
            """
            INSERT INTO store_settings (key, value)
            VALUES ('store_mode', 'automatico')
            """
        )

    BACKUP_DIRECTORY.mkdir(parents=True, exist_ok=True)
    TRANSCRIPT_DIRECTORY.mkdir(parents=True, exist_ok=True)

    bot.add_view(MainStoreView())

    # Restaura os botões dos tickets após reinício do bot.
    active_orders = await db_fetchall(
        "SELECT id, user_id, status, recipient_locked FROM orders WHERE channel_id IS NOT NULL"
    )
    for order in active_orders:
        order_id = int(order["id"])
        owner_id = int(order["user_id"])
        if (
            order["status"] == "paid"
            and not int(order["recipient_locked"] or 0)
        ):
            bot.add_view(RecipientManagerView(order_id, owner_id))
            bot.add_view(RecipientReviewView(order_id, owner_id))
        if order["status"] == "delivered":
            bot.add_view(ReviewView(order_id, owner_id))

    try:
        startup_backup = await create_database_backup("inicializacao")
        print(f"[BACKUP] Inicialização: {startup_backup}")
    except Exception as error:
        print(f"[BACKUP] Falha na inicialização: {error}")

    if not automatic_backup_loop.is_running():
        automatic_backup_loop.start()
    if not status_loop.is_running():
        status_loop.start()
    if not mercado_pago_poll_loop.is_running():
        mercado_pago_poll_loop.start()

    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)


@bot.event
async def on_ready():
    print("=" * 60)
    print(f"Bot conectado como: {bot.user}")
    print(f"Servidor: {GUILD_ID}")
    print(f"Categoria de tickets: {TICKET_CATEGORY_ID}")
    print(f"Cargo de suporte: {SUPPORT_ROLE_ID}")
    print(f"Banco de dados: {DATABASE_PATH}")
    print(f"Servidores privados: {PRIVATE_SERVERS_PATH}")

    if PRIVATE_SERVERS_PATH.is_file():
        configured_servers = load_private_servers()
        total_servers = sum(
            len(servers)
            for servers in configured_servers.values()
        )
        print(
            f"Links privados carregados: {total_servers}"
        )
    else:
        print(
            "[AVISO] private_servers.json não foi encontrado. "
            "Os links privados não serão exibidos."
        )
    print(f"Canal de logs: {LOG_CHANNEL_ID or 'desativado'}")
    print(f"Canal de entregas: {DELIVERY_CHANNEL_ID or 'desativado'}")
    print("Mercado Pago: único PIX ativo (PIX dinâmico)")
    print(
        "Ambiente Mercado Pago: "
        + ("TESTE" if MERCADO_PAGO_TEST_MODE else "PRODUÇÃO")
    )
    print("E-mail operacional do Mercado Pago: configurado")
    print(
        f"Taxa repassada ao cliente: "
        f"{MERCADO_PAGO_FEE_PERCENT}%"
        + (
            f" + R$ {MERCADO_PAGO_FIXED_FEE}"
            if MERCADO_PAGO_FIXED_FEE
            else ""
        )
    )
    print(
        f"Conferência manual a partir de: "
        f"R$ {MANUAL_PAYMENT_THRESHOLD}"
    )
    print(
        f"Consulta de pagamentos: a cada "
        f"{MERCADO_PAGO_POLL_SECONDS} segundos"
    )
    print("=" * 60)


@bot.tree.command(
    name="painel",
    description="Publica o painel profissional da loja",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def painel(interaction: discord.Interaction):
    if not SHOP_MENU_GIF_PATH.is_file():
        await interaction.response.send_message(
            (
                "O GIF do painel não foi encontrado em:\n"
                f"`{SHOP_MENU_GIF_PATH}`\n\n"
                "Coloque o arquivo `karlitos_store_banner_animado.gif` "
                "na mesma pasta do `main.py`."
            ),
            ephemeral=True,
        )
        return

    banner = discord.File(
        SHOP_MENU_GIF_PATH,
        filename="karlitos_store_banner_animado.gif",
    )
    await interaction.response.send_message(
        embed=main_store_embed(interaction.guild),
        view=MainStoreView(interaction.guild),
        file=banner,
    )


def build_server_rules_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📜 Regras da Karlitos Store",
        description=(
            "Para manter o servidor seguro, organizado e agradável para todos, "
            "siga as regras abaixo.\n\n"
            "**1️⃣ Respeite todos os membros.**\n"
            "Não serão toleradas ofensas, provocações, preconceito ou comportamento tóxico.\n\n"
            "**2️⃣ Não envie spam, links ou divulgação.**\n"
            "Evite mensagens repetidas e não divulgue servidores, lojas ou redes sociais sem autorização.\n\n"
            "**3️⃣ Não marque membros ou o proprietário sem necessidade.**\n"
            "Use as marcações apenas quando o assunto realmente exigir atendimento.\n\n"
            "**4️⃣ Use cada canal somente para sua finalidade.**\n"
            "Mantenha conversas, compras, avaliações e suporte nos canais corretos.\n\n"
            "**5️⃣ Não compartilhe informações pessoais.**\n"
            "Proteja seus dados, senhas, códigos e informações de pagamento.\n\n"
            "**6️⃣ Tentativas de golpe resultam em banimento.**\n"
            "Fraudes, falsos comprovantes e tentativas de enganar membros ou a loja não serão toleradas.\n\n"
            "**7️⃣ Siga as orientações da administração.**\n"
            "As decisões da equipe devem ser respeitadas para manter o servidor organizado."
        ),
        color=THEME_PURPLE,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="✅ Concordância",
        value=(
            "Ao permanecer no servidor, você declara que leu e concorda com estas regras."
        ),
        inline=False,
    )
    return apply_branding(
        embed,
        footer=f"{STORE_NAME} • Segurança, respeito e organização",
    )


def build_purchase_rules_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛒 Regras de compra",
        description=(
            "Leia com atenção antes de realizar sua compra na Karlitos Store.\n\n"
            "**1️⃣ Confira o produto antes de efetuar o pagamento.**\n"
            "Verifique o nome, a quantidade, o jogo e o valor do pedido.\n\n"
            "**2️⃣ Informe corretamente o username do Roblox.**\n"
            "A entrega será feita exatamente para a conta confirmada no ticket.\n\n"
            "**3️⃣ Não aceitamos pagamentos pelo Inter ou PicPay.**\n"
            "Escolha outro banco para pagamentos que exigem conferência.\n\n"
            "**4️⃣ Envie o comprovante completo quando solicitado.**\n"
            "O comprovante precisa mostrar valor, data, horário e identificação da transação.\n\n"
            "**5️⃣ Compras a partir de R$ 100,00 exigem conferência manual.**\n"
            "A entrega ocorrerá após a validação feita pelo proprietário.\n\n"
            "**6️⃣ Após confirmar os destinatários, os nomes não poderão ser alterados.**\n"
            "Revise cuidadosamente todos os usernames antes de confirmar.\n\n"
            "**7️⃣ Após a entrega, não realizamos trocas por username informado incorretamente.**\n"
            "A responsabilidade pela conferência do nome é do comprador.\n\n"
            "**8️⃣ O prazo de entrega começa após a confirmação do pagamento.**\n"
            "Pagamentos pendentes ou em análise não iniciam o prazo.\n\n"
            "**9️⃣ Não abra vários tickets para o mesmo pedido.**\n"
            "Mantenha todo o atendimento no ticket original.\n\n"
            "**🔟 Tentativas de contestação fraudulenta resultarão em banimento e registro das provas.**"
        ),
        color=THEME_DARK_PURPLE,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="🛡️ Segurança • Confiança • Compromisso",
        value="Agradecemos por escolher a Karlitos Store.",
        inline=False,
    )
    return apply_branding(
        embed,
        footer=f"{STORE_NAME} • Leia antes de comprar",
    )


async def publish_rules_panel(
    interaction: discord.Interaction,
    banner_path: Path,
    banner_filename: str,
    embed: discord.Embed,
) -> None:
    if interaction.user.id != OWNER_USER_ID:
        await interaction.response.send_message(
            "Somente o dono da loja pode publicar este painel.",
            ephemeral=True,
        )
        return

    if not banner_path.is_file():
        await interaction.response.send_message(
            (
                "O banner não foi encontrado em:\n"
                f"`{banner_path}`"
            ),
            ephemeral=True,
        )
        return

    banner = discord.File(banner_path, filename=banner_filename)
    embed.set_image(url=f"attachment://{banner_filename}")
    await interaction.response.send_message(embed=embed, file=banner)


@bot.tree.command(
    name="regras_servidor",
    description="Publica o painel de regras do servidor",
    guild=discord.Object(id=GUILD_ID),
)
async def regras_servidor(interaction: discord.Interaction):
    await publish_rules_panel(
        interaction,
        RULES_SERVER_BANNER_PATH,
        "regras_servidor_karlitos.png",
        build_server_rules_embed(),
    )


@bot.tree.command(
    name="regras_compra",
    description="Publica o painel de regras de compra",
    guild=discord.Object(id=GUILD_ID),
)
async def regras_compra(interaction: discord.Interaction):
    await publish_rules_panel(
        interaction,
        RULES_PURCHASE_BANNER_PATH,
        "regras_compra_karlitos.png",
        build_purchase_rules_embed(),
    )


@bot.tree.command(
    name="carrinho",
    description="Abre o seu carrinho de compras",
    guild=discord.Object(id=GUILD_ID),
)
async def carrinho(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=await make_cart_embed(interaction.user.id),
        view=CartView(interaction.user.id),
        ephemeral=True,
    )


@bot.tree.command(
    name="pedido",
    description="Mostra o resumo do pedido atual ou consulta pelo número",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(numero="Número do pedido; deixe vazio dentro de um ticket")
async def pedido(
    interaction: discord.Interaction,
    numero: Optional[int] = None,
):
    if numero is None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use este comando dentro de um ticket ou informe o número.",
                ephemeral=True,
            )
            return
        order = await db_fetchone(
            "SELECT * FROM orders WHERE channel_id = ?",
            (interaction.channel.id,),
        )
        if order:
            numero = int(order["id"])
    else:
        order = await db_fetchone("SELECT * FROM orders WHERE id = ?", (numero,))

    if not order:
        await interaction.response.send_message(
            "Pedido não encontrado.",
            ephemeral=True,
        )
        return

    if order["user_id"] != interaction.user.id and not await is_support(interaction):
        await interaction.response.send_message(
            "Você não pode consultar este pedido.",
            ephemeral=True,
        )
        return

    if order["status"] == "delivered":
        status = "📦 Entregue"
    elif order["status"] == "paid":
        status = "✅ Pago"
    elif order["status"] == "payment_review":
        status = "🔎 Aguardando conferência manual"
    elif order["status"] == "cancelled":
        status = "❌ Cancelado"
    else:
        status = "⏳ Aguardando pagamento"

    roblox_line = (
        f"\nUsuário Roblox original: **{order['roblox_username']}**"
        if order["roblox_username"]
        else "\nUsuário Roblox original: **Ainda não informado**"
    )

    embed = discord.Embed(
        title=f"Pedido #{numero:06d}",
        description=(
            f"Status: **{status}**\n"
            f"Total em Robux: **{format_number(order['total_robux'])}**\n"
            f"Total: **{brl(order['total_brl'])}**"
            f"{roblox_line}"
        ),
        color=(
            THEME_GREEN
            if order["status"] in {"paid", "delivered"}
            else (
                THEME_RED
                if order["status"] == "cancelled"
                else THEME_ORANGE
            )
        ),
    )

    recipients = await recipients_text(numero)
    embed.add_field(
        name="🎮 Destinatários",
        value=recipients,
        inline=False,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)



async def current_ticket_order(
    interaction: discord.Interaction,
) -> Optional[sqlite3.Row]:
    if not isinstance(interaction.channel, discord.TextChannel):
        return None
    return await db_fetchone(
        "SELECT * FROM orders WHERE channel_id = ?",
        (interaction.channel.id,),
    )


async def require_current_ticket(
    interaction: discord.Interaction,
) -> Optional[sqlite3.Row]:
    order = await current_ticket_order(interaction)
    if order is None:
        await interaction.response.send_message(
            "Use este comando dentro de um ticket de compra ativo.",
            ephemeral=True,
        )
    return order


@bot.tree.command(
    name="pago",
    description="Confirma manualmente o pagamento do ticket atual",
    guild=discord.Object(id=GUILD_ID),
)
async def pago(interaction: discord.Interaction):
    order = await require_current_ticket(interaction)
    if order is None:
        return
    await TicketAdminView(int(order["id"])).mark_paid(interaction)


@bot.tree.command(
    name="entregue",
    description="Marca o pedido atual como entregue",
    guild=discord.Object(id=GUILD_ID),
)
async def entregue(interaction: discord.Interaction):
    order = await require_current_ticket(interaction)
    if order is None:
        return
    await TicketAdminView(int(order["id"])).mark_delivered(interaction)


@bot.tree.command(
    name="fechar",
    description="Fecha o ticket atual com transcript e backup",
    guild=discord.Object(id=GUILD_ID),
)
async def fechar(interaction: discord.Interaction):
    order = await require_current_ticket(interaction)
    if order is None:
        return
    await TicketAdminView(int(order["id"])).close_ticket(interaction)


@bot.tree.command(
    name="pix",
    description="Envia a chave PIX manual da loja no ticket atual",
    guild=discord.Object(id=GUILD_ID),
)
async def pix(interaction: discord.Interaction):
    order = await require_current_ticket(interaction)
    if order is None:
        return
    if interaction.user.id != OWNER_USER_ID:
        await interaction.response.send_message(
            "Somente o dono da loja pode enviar a chave PIX manual.",
            ephemeral=True,
        )
        return
    if not MANUAL_PIX_KEY:
        await interaction.response.send_message(
            "Configure `MANUAL_PIX_KEY` no arquivo `.env`.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="💠 Chave PIX da loja",
        description=(
            f"**Chave PIX:**\n```{MANUAL_PIX_KEY}```\n"
            + (f"**Titular:** {MANUAL_PIX_HOLDER}\n" if MANUAL_PIX_HOLDER else "")
            + "Envie o comprovante neste ticket após o pagamento."
        ),
        color=THEME_PURPLE,
    )
    apply_branding(embed, footer=f"{STORE_NAME} • Pedido #{int(order['id']):06d}")
    await interaction.response.send_message(
        content=f"<@{int(order['user_id'])}>",
        embed=embed,
    )


@bot.tree.command(
    name="comprovante",
    description="Pede ao cliente o comprovante do PIX",
    guild=discord.Object(id=GUILD_ID),
)
async def comprovante(interaction: discord.Interaction):
    order = await require_current_ticket(interaction)
    if order is None:
        return
    if not await is_support(interaction):
        await interaction.response.send_message(
            "Somente a equipe pode usar este comando.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"<@{int(order['user_id'])}>, envie o **comprovante do PIX** "
        "neste ticket para conferência, por favor. 📸"
    )


@bot.tree.command(
    name="cancelar",
    description="Cancela o pedido do ticket atual",
    guild=discord.Object(id=GUILD_ID),
)
async def cancelar(interaction: discord.Interaction):
    order = await require_current_ticket(interaction)
    if order is None:
        return
    if interaction.user.id != OWNER_USER_ID:
        await interaction.response.send_message(
            "Somente o dono da loja pode cancelar pedidos.",
            ephemeral=True,
        )
        return
    if order["status"] == "delivered":
        await interaction.response.send_message(
            "Um pedido entregue não pode ser cancelado.",
            ephemeral=True,
        )
        return
    if order["status"] == "cancelled":
        await interaction.response.send_message(
            "Este pedido já está cancelado.",
            ephemeral=True,
        )
        return
    await db_execute(
        "UPDATE orders SET status = 'cancelled' WHERE id = ?",
        (int(order["id"]),),
    )
    await interaction.response.send_message(
        f"❌ Pedido **#{int(order['id']):06d}** cancelado por "
        f"{interaction.user.mention}."
    )
    await send_log(discord.Embed(
        title="❌ Pedido cancelado",
        description=(
            f"**Pedido:** #{int(order['id']):06d}\n"
            f"**Cliente:** <@{int(order['user_id'])}>\n"
            f"**Cancelado por:** {interaction.user.mention}"
        ),
        color=THEME_RED,
        timestamp=datetime.now(timezone.utc),
    ))


@bot.tree.command(
    name="reabrir",
    description="Reabre um pedido fechado em um novo canal",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(numero="Número do pedido que será reaberto")
async def reabrir(interaction: discord.Interaction, numero: int):
    if interaction.user.id != OWNER_USER_ID:
        await interaction.response.send_message(
            "Somente o dono da loja pode reabrir pedidos.",
            ephemeral=True,
        )
        return
    order = await db_fetchone("SELECT * FROM orders WHERE id = ?", (numero,))
    if not order:
        await interaction.response.send_message("Pedido não encontrado.", ephemeral=True)
        return
    if order["channel_id"]:
        existing = interaction.guild.get_channel(int(order["channel_id"]))
        if existing:
            await interaction.response.send_message(
                f"Este pedido já possui um ticket aberto: {existing.mention}",
                ephemeral=True,
            )
            return

    category, error = await get_ticket_category(interaction.guild)
    if error or category is None:
        await interaction.response.send_message(
            f"Não foi possível acessar a categoria de tickets: {error}",
            ephemeral=True,
        )
        return
    customer = interaction.guild.get_member(int(order["user_id"]))
    if customer is None:
        try:
            customer = await interaction.guild.fetch_member(int(order["user_id"]))
        except discord.DiscordException:
            customer = None

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_channels=True, manage_messages=True,
        ),
    }
    if customer:
        overwrites[customer] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
        )
    support_role = interaction.guild.get_role(SUPPORT_ROLE_ID)
    if support_role:
        overwrites[support_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
        )

    channel = await interaction.guild.create_text_channel(
        name=f"pedido-{numero:06d}",
        category=category,
        overwrites=overwrites,
        topic=f"Pedido reaberto #{numero:06d} • Cliente {int(order['user_id'])}",
        reason=f"Pedido #{numero:06d} reaberto por {interaction.user}",
    )
    await db_execute(
        "UPDATE orders SET channel_id = ?, closed_at = NULL WHERE id = ?",
        (channel.id, numero),
    )
    await channel.send(
        content=f"<@{int(order['user_id'])}>",
        embed=discord.Embed(
            title=f"🔓 Pedido #{numero:06d} reaberto",
            description=(
                f"Ticket reaberto por {interaction.user.mention}.\n\n"
                "Use `/pedido` para visualizar novamente o resumo."
            ),
            color=THEME_PURPLE,
        ),
    )
    await interaction.response.send_message(
        f"Ticket reaberto: {channel.mention}",
        ephemeral=True,
    )


@bot.tree.command(
    name="pendentes",
    description="Lista pedidos aguardando pagamento ou entrega",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def pendentes(interaction: discord.Interaction):
    rows = await db_fetchall(
        """
        SELECT id, user_id, channel_id, status, total_brl, total_robux,
               roblox_username, created_at
        FROM orders
        WHERE status IN ('pending', 'payment_review', 'paid')
        ORDER BY id ASC
        LIMIT 25
        """
    )

    if not rows:
        await interaction.response.send_message(
            "Não há pedidos pendentes.",
            ephemeral=True,
        )
        return

    lines = []
    for row in rows:
        status = "Pagamento" if row["status"] == "pending" else "Entrega"
        channel_text = f"<#{row['channel_id']}>" if row["channel_id"] else "sem canal"
        username = row["roblox_username"] or "não informado"
        lines.append(
            f"**#{int(row['id']):06d}** • aguardando {status}\\n"
            f"└ Cliente: <@{row['user_id']}> • Canal: {channel_text}\\n"
            f"└ {format_number(int(row['total_robux']))} Robux • "
            f"{brl(float(row['total_brl']))} • Roblox: `{username}`"
        )

    embed = discord.Embed(
        title="📋 Pedidos pendentes",
        description="\\n\\n".join(lines)[:4000],
        color=THEME_ORANGE,
        timestamp=datetime.now(timezone.utc),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="backup",
    description="Cria um backup imediato do banco da loja",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def backup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        path = await create_database_backup(
            f"manual_{interaction.user.id}"
        )
    except Exception as error:
        await interaction.followup.send(
            f"Falha ao criar backup: `{error}`",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        "Backup criado com sucesso.",
        file=discord.File(path, filename=path.name),
        ephemeral=True,
    )


@bot.tree.command(
    name="cancelar_pedido",
    description="Cancela um pedido pendente e fecha o ticket",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(numero="Número do pedido pendente")
async def cancelar_pedido(
    interaction: discord.Interaction,
    numero: int,
):
    order = await db_fetchone(
        "SELECT * FROM orders WHERE id = ?",
        (numero,),
    )

    if not order:
        await interaction.response.send_message(
            "Pedido não encontrado.",
            ephemeral=True,
        )
        return

    is_owner = interaction.user.id == OWNER_USER_ID
    is_customer = interaction.user.id == int(order["user_id"])

    if not is_owner and not is_customer:
        await interaction.response.send_message(
            "Você não pode cancelar este pedido.",
            ephemeral=True,
        )
        return

    if order["status"] != "pending":
        await interaction.response.send_message(
            "Somente pedidos aguardando pagamento podem ser cancelados.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

    channel = None
    if order["channel_id"]:
        channel = interaction.guild.get_channel(
            int(order["channel_id"])
        )

    if isinstance(channel, discord.TextChannel):
        transcript = await create_ticket_transcript(
            channel,
            numero,
        )
        await send_transcript_to_log(
            transcript,
            numero,
            channel.name,
        )

    await db_execute(
        """
        UPDATE orders
        SET status = 'cancelled',
            channel_id = NULL,
            closed_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (utc_now(), numero),
    )

    await interaction.followup.send(
        f"Pedido **#{numero:06d}** cancelado.",
        ephemeral=True,
    )

    if isinstance(channel, discord.TextChannel):
        try:
            await channel.delete(
                reason=(
                    f"Pedido #{numero:06d} cancelado por "
                    f"{interaction.user}"
                )
            )
        except discord.DiscordException:
            pass


@bot.tree.command(
    name="verificar_pagamento",
    description="Consulta agora o pagamento de um pedido no Mercado Pago",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(numero="Número do pedido")
async def verificar_pagamento(
    interaction: discord.Interaction,
    numero: int,
):
    if interaction.user.id != OWNER_USER_ID:
        await interaction.response.send_message(
            "Somente o dono da loja pode usar este comando.",
            ephemeral=True,
        )
        return

    order = await db_fetchone(
        "SELECT * FROM orders WHERE id = ?",
        (numero,),
    )

    if not order:
        await interaction.response.send_message(
            "Pedido não encontrado.",
            ephemeral=True,
        )
        return

    if not order["mp_order_id"]:
        await interaction.response.send_message(
            "Este pedido não possui cobrança do Mercado Pago.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

    success, mp_data, error = await get_mercado_pago_order(
        str(order["mp_order_id"])
    )

    if not success:
        await interaction.followup.send(
            f"Falha ao consultar: `{error}`",
            ephemeral=True,
        )
        return

    payment = extract_mp_payment(mp_data)
    approved = mercado_pago_is_approved(mp_data)

    if approved and order["status"] == "pending":
        await approve_order_from_mercado_pago(order, mp_data)

    await interaction.followup.send(
        (
            f"**Status local:** `{order['status']}`\\n"
            f"**Status Mercado Pago:** "
            f"`{mp_data.get('status', 'desconhecido')}`\\n"
            f"**Detalhe:** "
            f"`{mp_data.get('status_detail', '')}`\\n"
            f"**Pagamento:** "
            f"`{payment.get('status', '')}`\\n"
            f"**Aprovado:** {'sim' if approved else 'não'}"
        ),
        ephemeral=True,
    )


@bot.tree.command(
    name="servidores",
    description="Envia novamente os servidores privados de um pedido",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(numero="Número do pedido")
async def servidores(
    interaction: discord.Interaction,
    numero: int,
):
    if interaction.user.id != OWNER_USER_ID:
        await interaction.response.send_message(
            "Somente o dono da loja pode reenviar os servidores.",
            ephemeral=True,
        )
        return

    order = await db_fetchone(
        "SELECT * FROM orders WHERE id = ?",
        (numero,),
    )

    if not order:
        await interaction.response.send_message(
            "Pedido não encontrado.",
            ephemeral=True,
        )
        return

    if not interaction.channel:
        await interaction.response.send_message(
            "Use este comando dentro do ticket.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    sent = await send_private_servers(
        interaction.channel,
        order,
        int(order["user_id"]),
    )

    await interaction.followup.send(
        (
            "Servidores enviados novamente."
            if sent
            else "Nenhum servidor configurado para esse pedido."
        ),
        ephemeral=True,
    )


@bot.tree.command(
    name="excluir_venda",
    description="Apaga uma venda de teste do banco de dados",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(
    numero="Número do pedido",
    devolver_estoque="Devolver os produtos ao estoque configurado",
)
async def excluir_venda(
    interaction: discord.Interaction,
    numero: int,
    devolver_estoque: bool = True,
):
    if interaction.user.id != OWNER_USER_ID:
        await interaction.response.send_message(
            "Somente o dono da loja pode excluir vendas.",
            ephemeral=True,
        )
        return

    order = await db_fetchone(
        "SELECT * FROM orders WHERE id = ?",
        (numero,),
    )

    if not order:
        await interaction.response.send_message(
            "Venda não encontrada.",
            ephemeral=True,
        )
        return

    channel_id = order["channel_id"]

    if channel_id:
        channel = interaction.guild.get_channel(int(channel_id))
        if channel is not None:
            await interaction.response.send_message(
                (
                    "Este pedido ainda possui um ticket aberto. "
                    "Feche o ticket antes de excluir a venda."
                ),
                ephemeral=True,
            )
            return

    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        backup_path = await create_database_backup(
            f"antes_excluir_{numero:06d}"
        )
    except Exception:
        backup_path = None

    items = json.loads(order["items_json"])

    if devolver_estoque and order["status"] in {"paid", "delivered"}:
        for item in items:
            item_key = str(item["item_key"])
            quantity = int(item["quantity"])
            stock = await get_inventory(item_key)

            if stock >= 0:
                await db_execute(
                    "UPDATE inventory SET stock = stock + ? WHERE item_key = ?",
                    (quantity, item_key),
                )

    if (
        order["discount_code"]
        and order["status"] in {"paid", "delivered"}
    ):
        await db_execute(
            """
            UPDATE coupons
            SET uses = MAX(uses - 1, 0)
            WHERE code = ?
            """,
            (order["discount_code"],),
        )

    await db_execute(
        "DELETE FROM reviews WHERE order_id = ?",
        (numero,),
    )
    await db_execute(
        "DELETE FROM order_recipients WHERE order_id = ?",
        (numero,),
    )
    await db_execute(
        "DELETE FROM orders WHERE id = ?",
        (numero,),
    )

    message = (
        f"✅ Venda **#{numero:06d}** excluída do banco.\n"
        f"Estoque devolvido: **{'sim' if devolver_estoque else 'não'}**."
    )

    if backup_path:
        message += f"\nBackup criado: `{backup_path.name}`"

    await interaction.followup.send(
        message,
        ephemeral=True,
    )


@bot.tree.command(
    name="vendas",
    description="Mostra um resumo das vendas registradas",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def vendas(interaction: discord.Interaction):
    summary = await db_fetchone(
        """
        SELECT
            COUNT(*) AS total_orders,
            SUM(CASE WHEN status IN ('paid', 'delivered') THEN 1 ELSE 0 END) AS paid_orders,
            COALESCE(SUM(CASE WHEN status IN ('paid', 'delivered') THEN total_brl ELSE 0 END), 0) AS revenue,
            COALESCE(SUM(CASE WHEN status IN ('paid', 'delivered') THEN total_robux ELSE 0 END), 0) AS robux
        FROM orders
        """
    )

    embed = discord.Embed(
        title="📊 Resumo da loja",
        color=THEME_PURPLE,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Pedidos registrados",
        value=str(int(summary["total_orders"] or 0)),
    )
    embed.add_field(
        name="Pagos/entregues",
        value=str(int(summary["paid_orders"] or 0)),
    )
    embed.add_field(
        name="Faturamento registrado",
        value=brl(float(summary["revenue"] or 0)),
        inline=False,
    )
    embed.add_field(
        name="Robux vendidos",
        value=format_number(int(summary["robux"] or 0)),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="cupom",
    description="Aplica um cupom ao seu carrinho",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(codigo="Código do cupom")
async def cupom(interaction: discord.Interaction, codigo: str):
    code_value = codigo.strip().upper()
    coupon = await db_fetchone(
        "SELECT * FROM coupons WHERE code = ? AND active = 1",
        (code_value,),
    )

    if not coupon:
        await interaction.response.send_message(
            "Cupom inválido ou inativo.",
            ephemeral=True,
        )
        return

    if coupon["max_uses"] is not None and int(coupon["uses"]) >= int(coupon["max_uses"]):
        await interaction.response.send_message(
            "Este cupom atingiu o limite de usos.",
            ephemeral=True,
        )
        return

    await db_execute(
        """
        INSERT INTO cart_coupons (user_id, code)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET code = excluded.code
        """,
        (interaction.user.id, code_value),
    )

    await interaction.response.send_message(
        f"🎟️ Cupom `{code_value}` aplicado: "
        f"**{float(coupon['percent']):g}% de desconto**.",
        embed=await make_cart_embed(interaction.user.id),
        ephemeral=True,
    )


@bot.tree.command(
    name="criar_cupom",
    description="Cria ou atualiza um cupom",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def criar_cupom(
    interaction: discord.Interaction,
    codigo: str,
    percentual: app_commands.Range[float, 1, 100],
    limite: Optional[int] = None,
):
    code_value = codigo.strip().upper()
    await db_execute(
        """
        INSERT INTO coupons (code, percent, active, max_uses, uses)
        VALUES (?, ?, 1, ?, 0)
        ON CONFLICT(code) DO UPDATE SET
            percent = excluded.percent,
            active = 1,
            max_uses = excluded.max_uses
        """,
        (code_value, float(percentual), limite),
    )
    await interaction.response.send_message(
        f"Cupom `{code_value}` configurado com {percentual:g}% de desconto.",
        ephemeral=True,
    )


@bot.tree.command(
    name="estoque",
    description="Define o estoque de um produto pelo item_key",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def estoque(
    interaction: discord.Interaction,
    item_key: str,
    quantidade: int,
):
    if quantidade < -1:
        await interaction.response.send_message(
            "Use -1 para estoque ilimitado ou um número maior/igual a zero.",
            ephemeral=True,
        )
        return

    await db_execute(
        """
        INSERT INTO inventory (item_key, stock)
        VALUES (?, ?)
        ON CONFLICT(item_key) DO UPDATE SET stock = excluded.stock
        """,
        (item_key, quantidade),
    )
    value = "ilimitado" if quantidade == -1 else str(quantidade)
    await interaction.response.send_message(
        f"Estoque de `{item_key}` definido como **{value}**.",
        ephemeral=True,
    )


@bot.tree.command(
    name="loja",
    description="Abre, fecha ou ativa o horário automático",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.describe(
    modo="Escolha como a loja deve funcionar"
)
@app_commands.choices(
    modo=[
        app_commands.Choice(
            name="🟢 Abrir loja",
            value="aberta",
        ),
        app_commands.Choice(
            name="🔴 Fechar loja",
            value="fechada",
        ),
        app_commands.Choice(
            name="🕒 Voltar ao horário automático",
            value="automatico",
        ),
    ]
)
async def loja(
    interaction: discord.Interaction,
    modo: app_commands.Choice[str],
):
    if interaction.user.id != OWNER_USER_ID:
        await interaction.response.send_message(
            "Somente o dono da loja pode alterar o funcionamento.",
            ephemeral=True,
        )
        return

    await db_execute(
        """
        INSERT INTO store_settings (key, value)
        VALUES ('store_mode', ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """,
        (modo.value,),
    )

    await update_status_message()

    messages = {
        "aberta": (
            "🟢 **Loja aberta manualmente.**\n"
            "As compras estão liberadas, mesmo fora do horário."
        ),
        "fechada": (
            "🔴 **Loja fechada manualmente.**\n"
            "Novos produtos e pedidos estão bloqueados."
        ),
        "automatico": (
            "🕒 **Horário automático ativado.**\n"
            "A loja seguirá novamente os horários configurados."
        ),
    }

    await interaction.response.send_message(
        messages[modo.value],
        embed=await build_status_embed(),
        ephemeral=True,
    )


@bot.tree.command(
    name="status",
    description="Mostra o status atual da loja",
    guild=discord.Object(id=GUILD_ID),
)
async def status(interaction: discord.Interaction):
    await update_status_message()
    await interaction.response.send_message(
        embed=await build_status_embed(),
        ephemeral=True,
    )


@bot.tree.command(
    name="clientes",
    description="Mostra os clientes que mais compraram",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def clientes(interaction: discord.Interaction):
    rows = await db_fetchall(
        """
        SELECT user_id, COUNT(*) AS purchases, SUM(total_brl) AS spent
        FROM orders
        WHERE status IN ('paid', 'delivered')
        GROUP BY user_id
        ORDER BY spent DESC
        LIMIT 10
        """
    )

    description = "\n".join(
        f"`{position}.` <@{row['user_id']}> — "
        f"**{int(row['purchases'])} pedidos** • "
        f"**{brl(float(row['spent']))}**"
        for position, row in enumerate(rows, start=1)
    ) or "Ainda não existem clientes com compras concluídas."

    embed = discord.Embed(
        title="🏆 Melhores clientes",
        description=description,
        color=THEME_GOLD,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="produtos",
    description="Mostra os produtos mais vendidos",
    guild=discord.Object(id=GUILD_ID),
)
@app_commands.checks.has_permissions(administrator=True)
async def produtos(interaction: discord.Interaction):
    orders = await db_fetchall(
        "SELECT items_json FROM orders WHERE status IN ('paid', 'delivered')"
    )
    totals: dict[str, int] = {}

    for order in orders:
        for item in json.loads(order["items_json"]):
            name = item["name"]
            totals[name] = totals.get(name, 0) + int(item["quantity"])

    top = sorted(totals.items(), key=lambda pair: pair[1], reverse=True)[:10]
    description = "\n".join(
        f"`{position}.` **{name}** — {quantity} unidade(s)"
        for position, (name, quantity) in enumerate(top, start=1)
    ) or "Nenhum produto vendido ainda."

    embed = discord.Embed(
        title="📈 Produtos mais vendidos",
        description=description,
        color=THEME_PURPLE,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@painel.error
async def painel_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "Somente administradores podem publicar o painel.",
            ephemeral=True,
        )
        return
    raise error



@bot.tree.error
async def global_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "Você não possui permissão para usar este comando."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"Aguarde {error.retry_after:.1f} segundos e tente novamente."
    else:
        original = getattr(error, "original", error)
        print(f"[ERRO DE COMANDO] {type(original).__name__}: {original}")
        message = "Ocorreu um erro inesperado. A equipe pode conferir o terminal do bot."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.DiscordException:
        pass


def run_bot() -> None:
    """Inicia o bot usando o token configurado no arquivo .env."""
    bot.run(DISCORD_TOKEN)

