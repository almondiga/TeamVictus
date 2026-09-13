"""Comando /buscar: imagen de la carta + precios Cardmarket.

La API oficial de Cardmarket está cerrada a nuevas altas, así que los precios se
obtienen del proveedor configurado en el .env (ver prices.py):
- BerryWallet (gratis): precios Cardmarket EUR, sin desglose por país.
- RapidAPI: añade el mínimo near-mint de vendedores de España.
- API oficial: solo si algún día se dispone de acceso (filtro exacto idioma EN + país).
"""
from __future__ import annotations

import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands
import requests

import config
from carddata import CardData, CardDataFallback, build_card_data
from prices import PriceResult, build_price_provider, URL_MKM_SEARCH

URL_MKM_SEARCH = URL_MKM_SEARCH


class SearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cards: CardData | CardDataFallback = build_card_data()
        self.prices, self.precio_error = build_price_provider()

    # ------------------------------------------------------------------

    @app_commands.command(
        name="buscar",
        description="Busca una carta por código o nombre: imagen + precios Cardmarket (EUR y España)",
    )
    @app_commands.describe(carta="Código (OP01-001, ST01-001...), nombre o token (op01, luffy)")
    async def buscar(self, interaction: discord.Interaction, carta: str) -> None:
        await interaction.response.defer(ephemeral=False)
        try:
            card = await asyncio.to_thread(self.cards.resolve_card, carta)
        except Exception as exc:
            await interaction.followup.send(
                f"❌ No pude buscar la carta: {exc}", ephemeral=True)
            return

        if card:
            await self._enviar_carta(interaction, card)
            return

        # ---- búsqueda tokenizada: 'op01' o 'luffy' -> listado de coincidencias ----
        try:
            matches = await asyncio.to_thread(self.cards.search_cards, carta, MAX_COINCIDENCIAS)
        except Exception as exc:
            await interaction.followup.send(
                f"❌ No pude buscar coincidencias: {exc}", ephemeral=True)
            return

        if not matches:
            await interaction.followup.send(
                f"❌ No encontré ninguna carta con «{carta}». Revisa el código "
                f"(p. ej. OP01-001), el nombre o prueba con /listado.",
                ephemeral=True)
            return

        if len(matches) == 1:
            await self._enviar_carta(interaction, matches[0])
            return

        total = len(matches)
        aviso = ""
        if total > MAX_COINCIDENCIAS:
            aviso = (f"\nMostrando las **{MAX_COINCIDENCIAS}** primeras de **{total}**. "
                     f"Para verlas todas usa `/listado` con más filtros (p. ej. `/listado set:{carta.upper()}`).")
        vista = SelectCartaView(self, matches, carta)
        await interaction.followup.send(
            content=f"🔎 **{total} coincidencias** para «{carta}». Elige una:{aviso}",
            view=vista)

    # ------------------------------------------------------------------

    async def _precio_para(self, card: dict) -> tuple[PriceResult | None, str | None]:
        """Consulta precios al proveedor configurado. Devuelve (precio, error)."""
        if self.prices is None:
            return None, self.precio_error
        try:
            precio = await asyncio.to_thread(
                self.prices.get_prices, card.get("name") or "", card.get("id"))
            return precio, None
        except Exception as exc:
            return None, str(exc)

    async def _embed_carta(self, card: dict, precio: PriceResult | None,
                           precio_error: str | None) -> tuple[discord.Embed, list[discord.File], str | None]:
        """Construye el embed de una carta (imagen + campos + precios)."""
        embed = discord.Embed(
            title=f"{card.get('name', '?')}",
            description=f"`{card.get('id', '?')}`",
            color=config.COLOR_PRIMARY,
        )
        sets = card.get("sets") or []
        set_label = sets[0].get("label") if sets else "—"
        colors = ", ".join(card["colors"]) if card.get("colors") else "—"
        embed.add_field(name="Set", value=set_label, inline=True)
        embed.add_field(name="Rareza", value=card.get("rarity") or "—", inline=True)
        embed.add_field(name="Categoría", value=card.get("category") or "—", inline=True)
        if card.get("color") is None and colors != "—":
            embed.add_field(name="Color", value=colors, inline=True)
        tipos = ", ".join(card["types"]) if card.get("types") else None
        if tipos:
            embed.add_field(name="Tipo", value=tipos[:100], inline=True)
        if card.get("cost") is not None or card.get("power") is not None:
            embed.add_field(
                name="Coste / Poder",
                value=f"{card.get('cost') or '—'} / {card.get('power') or '—'}",
                inline=True)

        link = None
        if precio:
            lineas = [f"**Trend:** {fmt_euro(precio.trend)}",
                      f"**Media:** {fmt_euro(precio.avg)}",
                      f"**Mínimo:** {fmt_euro(precio.low)}"]
            embed.add_field(name="💶 Cardmarket (EUR)", value="\n".join(lineas), inline=True)

            if precio.es_disponible:
                es = f"**Mín. NM España:** {fmt_euro(precio.es_price)}"
                if precio.es_count:
                    es += f" · {precio.es_count} artículos"
                embed.add_field(name="🇪🇸 Comparativa España", value=es, inline=True)
            else:
                embed.add_field(
                    name="🇪🇸 Comparativa España",
                    value="No disponible con este proveedor." + (
                        f"\n*{precio.nota}" if precio.nota else ""),
                    inline=True)
            link = precio.link
        elif precio_error:
            embed.add_field(name="💶 Precios Cardmarket",
                            value=f"⚠️ {precio_error[:200]}", inline=False)

        if card.get("effect"):
            embed.add_field(name="Efecto", value=truncar(card["effect"], 1000), inline=False)
        link = link or (URL_MKM_SEARCH.format(quote_plus(card.get("name") or card.get("id") or ""))
                        if self.prices is not None else None)
        if link:
            embed.add_field(name="Cardmarket", value=f"[Ver en Cardmarket]({link})", inline=False)

        # ---------- imagen ----------
        archivos: list[discord.File] = []
        img_url = card.get("image_url")
        if img_url:
            try:
                img = await asyncio.to_thread(_descargar_imagen, img_url)
            except Exception:
                img = None
            if img:
                embed.set_image(url="attachment://carta.png")
                archivos.append(discord.File(img, filename="carta.png"))
        return embed, archivos, link

    async def _enviar_carta(self, interaction: discord.Interaction, card: dict,
                            editar: bool = False) -> None:
        """Envía (o edita el mensaje actual con) la ficha completa de una carta."""
        precio, precio_error = await self._precio_para(card)
        embed, archivos, _ = await self._embed_carta(card, precio, precio_error)
        if editar:
            await interaction.response.edit_message(
                content=None, embed=embed, attachments=archivos, view=None)
        else:
            await interaction.followup.send(embed=embed, files=archivos)

    # ------------------------------------------------------------------

    @app_commands.command(name="precio", description="Solo precios Cardmarket (EUR y España) de una carta")
    @app_commands.describe(carta="Código o nombre de la carta")
    async def precio(self, interaction: discord.Interaction, carta: str) -> None:
        await interaction.response.defer()
        if self.prices is None:
            await interaction.followup.send(f"⚠️ Precios no configurados: {self.precio_error}",
                                            ephemeral=True)
            return
        try:
            precio = await asyncio.to_thread(self.prices.get_prices, carta, None)
        except Exception as exc:
            await interaction.followup.send(f"❌ Error consultando precios: {exc}", ephemeral=True)
            return
        if not precio or (precio.trend is None and precio.low is None and not precio.es_disponible):
            await interaction.followup.send(f"❌ No hay precios para «{carta}».", ephemeral=True)
            return

        embed = discord.Embed(title=carta, color=config.COLOR_PRIMARY)
        embed.add_field(
            name="💶 Cardmarket (EUR)",
            value=f"Trend: **{fmt_euro(precio.trend)}** · Media: {fmt_euro(precio.avg)} · "
                  f"Mín: {fmt_euro(precio.low)}",
            inline=False)
        if precio.es_disponible:
            embed.add_field(
                name="🇪🇸 Comparativa España",
                value=f"Mín. NM España: **{fmt_euro(precio.es_price)}**"
                      + (f" · {precio.es_count} artículos" if precio.es_count else ""),
                inline=False)
        elif precio.nota:
            embed.add_field(name="🇪🇸 Comparativa España",
                            value=f"No disponible: {precio.nota}", inline=False)
        if precio.link:
            embed.add_field(name="Cardmarket", value=f"[Ver en Cardmarket]({precio.link})",
                            inline=False)
        await interaction.followup.send(embed=embed)


# ----------------------------- utilidades -----------------------------

MAX_COINCIDENCIAS = 25  # Discord limita los select a 25 opciones


class SelectCartaView(discord.ui.View):
    """Selector desplegable con las coincidencias de una búsqueda tokenizada."""

    def __init__(self, cog: SearchCog, cartas: list[dict], consulta: str) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.cartas = cartas
        self.consulta = consulta
        opciones = []
        for c in cartas[:MAX_COINCIDENCIAS]:
            cid = c.get("id") or "?"
            nombre = (c.get("name") or "?")[:90]
            desc = f"{cid} · {c.get('rarity') or '?'} · {c.get('set_id') or (c.get('sets') or [{}])[0].get('id', '?')}"
            opciones.append(discord.SelectOption(label=nombre, value=cid, description=desc[:95]))
        self.select = discord.ui.Select(
            placeholder=f"Elige una carta ({len(cartas)} coincidencias)",
            options=opciones)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        cid = self.select.values[0]
        card = None
        try:
            card = await asyncio.to_thread(self.cog.cards.resolve_card, cid)
        except Exception:
            card = None
        if not card:
            await interaction.response.edit_message(
                content=f"❌ No pude recuperar la carta «{cid}».", embed=None,
                attachments=[], view=None)
            return
        await self.cog._enviar_carta(interaction, card, editar=True)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if getattr(self, "message", None) is None:
            return
        try:
            await self.message.edit(view=self)
        except Exception:
            pass


def fmt_euro(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f} €"
    except (TypeError, ValueError):
        return str(v)


def truncar(texto: str, max_len: int) -> str:
    texto = (texto or "").strip()
    return texto if len(texto) <= max_len else texto[: max_len - 1] + "…"


def _descargar_imagen(url: str) -> io.BytesIO | None:
    resp = requests.get(url, timeout=15)
    if resp.status_code != 200 or not resp.content:
        return None
    return io.BytesIO(resp.content)


def quote_plus(texto: str) -> str:
    from urllib.parse import quote_plus as _qp
    return _qp(texto)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SearchCog(bot))
