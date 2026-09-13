"""Colección personal: /coleccion add|remove|ver, /importar, /exportar y /op-sync.

NOTA sobre OP.TCG: la app no tiene API pública ni exportación de colección, por lo
que la sincronización automática no es posible de forma legítima. La alternativa es
gestionar la colección aquí (manual o importando un CSV). Si la app añade exportación
en el futuro, este cog es donde se añadiría el importador.
"""
from __future__ import annotations

import asyncio
import csv
import io

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
from carddata import CardData, CardDataFallback, build_card_data


class CollectionCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cards: CardData | CardDataFallback = build_card_data()

    coleccion = app_commands.Group(
        name="coleccion", description="Gestiona tu colección de cartas")

    # ------------------------------------------------------------------

    @coleccion.command(name="add", description="Añade una carta a tu colección")
    @app_commands.describe(carta="Código (OP01-001) o nombre", cantidad="Cantidad (por defecto 1)")
    async def add(self, interaction: discord.Interaction,
                  carta: str, cantidad: int = 1) -> None:
        if cantidad < 1:
            await interaction.response.send_message("La cantidad debe ser ≥ 1.", ephemeral=True)
            return
        await interaction.response.defer()
        card = await asyncio.to_thread(self.cards.resolve_card, carta)
        if not card:
            await interaction.followup.send(
                f"❌ No encontré «{carta}». Usa el código exacto (p. ej. OP01-001).", ephemeral=True)
            return
        await asyncio.to_thread(
            db.add_to_collection, interaction.guild_id, interaction.user.id,
            card["id"] or carta, card.get("name") or carta, cantidad)
        await interaction.followup.send(
            f"✅ Añadida **{card.get('name')}** (`{card.get('id')}`) ×{cantidad} a tu colección.")

    # ------------------------------------------------------------------

    @coleccion.command(name="remove", description="Quita cartas de tu colección")
    @app_commands.describe(carta="Código de la carta", cantidad="Cantidad a quitar")
    async def remove(self, interaction: discord.Interaction,
                     carta: str, cantidad: int = 1) -> None:
        if cantidad < 1:
            await interaction.response.send_message("La cantidad debe ser ≥ 1.", ephemeral=True)
            return
        from carddata import normalize_code
        code = normalize_code(carta) or carta.upper()
        quitadas = await asyncio.to_thread(
            db.remove_from_collection, interaction.guild_id, interaction.user.id, code, cantidad)
        if quitadas == 0:
            await interaction.response.send_message(
                f"❌ No tienes `{code}` en tu colección.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Quitadas {quitadas} de `{code}` de tu colección.")

    # ------------------------------------------------------------------

    @coleccion.command(name="ver", description="Muestra una colección (la tuya o la de otro usuario)")
    @app_commands.describe(usuario="Usuario del que ver la colección (por defecto tú)",
                           set="Filtrar por set (OP-01...)", color="Filtrar por color")
    @app_commands.choices(color=[app_commands.Choice(name=c, value=c)
                                 for c in ("Red", "Green", "Blue", "Purple", "Black", "Yellow")])
    async def ver(self, interaction: discord.Interaction,
                  usuario: discord.Member | None = None,
                  set: str | None = None,
                  color: app_commands.Choice[str] | None = None) -> None:
        owner = usuario or interaction.user
        items = await asyncio.to_thread(db.list_collection, interaction.guild_id, owner.id)
        if not items:
            await interaction.response.send_message(
                f"📭 {owner.display_name} no tiene cartas en su colección todavía.", ephemeral=False)
            return

        # Filtros client-side (la colección es local)
        if set:
            s = set.strip().upper().replace("-", "")
            items = [i for i in items if i["card_code"].upper().replace("-", "").startswith(s)]
        if color:
            items = [i for i in items if i["card_code"].upper() in _codigos_por_color(color.value)]

        if not items:
            await interaction.response.send_message("🔍 Sin resultados con esos filtros.", ephemeral=False)
            return

        n_unicas, n_total = await asyncio.to_thread(db.collection_count, interaction.guild_id, owner.id)
        embed = discord.Embed(
            title=f"🗂️ Colección de {owner.display_name}",
            description=f"{len(items)} cartas mostradas · {n_total} en total ({n_unicas} únicas)",
            color=config.COLOR_PRIMARY,
        )
        for row in items[: config.PAGE_SIZE_LIST]:
            embed.add_field(
                name=f"`{row['card_code']}` {row['card_name'] or ''}",
                value=f"×{row['qty']}",
                inline=True,
            )
        if len(items) > config.PAGE_SIZE_LIST:
            embed.set_footer(text=f"... y {len(items) - config.PAGE_SIZE_LIST} más. "
                                  "Usa filtros para acotar.")
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------

    @app_commands.command(name="importar", description="Importa tu colección desde un CSV")
    @app_commands.describe(archivo="CSV con columnas: card_code,card_name,qty")
    async def importar(self, interaction: discord.Interaction,
                       archivo: discord.Attachment) -> None:
        await interaction.response.defer(ephemeral=False)
        contenido = (await archivo.read()).decode("utf-8-sig", errors="replace")
        items: list[tuple[str, str, int]] = []
        errores = 0
        for fila in csv.DictReader(io.StringIO(contenido)):
            code = (fila.get("card_code") or "").strip()
            if not code:
                errores += 1
                continue
            try:
                qty = int(float((fila.get("qty") or "1").strip()))
            except ValueError:
                qty = 1
            items.append((code, (fila.get("card_name") or "").strip(), qty))
        if not items:
            await interaction.followup.send(
                "❌ El CSV no tiene filas válidas. Columnas: `card_code,card_name,qty`",
                ephemeral=True)
            return
        n = await asyncio.to_thread(db.import_collection, interaction.guild_id,
                                    interaction.user.id, items)
        await interaction.followup.send(f"✅ Importadas **{n}** cartas a tu colección.")

    # ------------------------------------------------------------------

    @app_commands.command(name="exportar", description="Descarga tu colección como CSV")
    async def exportar(self, interaction: discord.Interaction) -> None:
        items = await asyncio.to_thread(db.list_collection, interaction.guild_id,
                                        interaction.user.id, limit=100000)
        if not items:
            await interaction.response.send_message("📭 Tu colección está vacía.", ephemeral=True)
            return
        salida = io.StringIO()
        salida.write("card_code,card_name,qty\n")
        for row in items:
            salida.write(f"{row['card_code']},{row['card_name'] or ''},{row['qty']}\n")
        await interaction.response.send_message(
            "📎 Tu colección:", file=discord.File(io.BytesIO(salida.getvalue().encode("utf-8")),
                                                  filename="coleccion.csv"))

    # ------------------------------------------------------------------

    @app_commands.command(name="op-sync", description="Sincronización con la app OP.TCG (estado)")
    async def op_sync(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="📱 Sincronización con OP.TCG",
            color=config.COLOR_WARN,
            description=(
                "La app **OP.TCG no tiene API pública** y su extracción no autorizada "
                "viola sus términos de servicio, así que no se puede sincronizar "
                "automáticamente tu colección guardada en ella.\n\n"
                "**Alternativas disponibles:**\n"
                "• `/coleccion add` para añadir cartas una a una\n"
                "• `/importar` con un CSV (`card_code,card_name,qty`) si tienes un listado\n"
                "• `/exportar` para llevarte tu colección del bot a otro sitio\n\n"
                "Si OP.TCG añade exportación de colección en el futuro, se puede añadir "
                "un importador sin tocar el resto del bot."
            ),
        )
        await interaction.response.send_message(embed=embed)


# ----------------------------- helpers -----------------------------

def _codigos_por_color(color: str) -> set[str]:
    """Devuelve códigos de cartas del color indicado (para filtrar colección local).

    Usa el catálogo local (CardDataLocal) cuando existe; si no, BerryWallet.
    Se cachea por proceso.
    """
    global _CACHE_COLOR
    if _CACHE_COLOR is None:
        _CACHE_COLOR = {}
        try:
            prov = build_card_data()
            if isinstance(prov, (CardData, CardDataBerry, CardDataLocal)):
                for c in ("Red", "Green", "Blue", "Purple", "Black", "Yellow"):
                    _CACHE_COLOR[c] = {
                        (card.get("id") or "").upper()
                        for card in prov.list_cards(color=c, page_size=500)
                    }
        except Exception:
            _CACHE_COLOR = {}
    return _CACHE_COLOR.get(color, set())


_CACHE_COLOR = None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CollectionCog(bot))
