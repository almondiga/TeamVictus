"""Comando /buscar: imagen + datos de la carta.

Los precios están deshabilitados por ahora (no hay API de precios disponible:
la API oficial de Cardmarket está cerrada a nuevas altas). Cuando haya un
proveedor funcional (BerryWallet/RapidAPI), basta con poner PRICES_ENABLED=1
en el .env y se reactivan las secciones de precio (EUR y España).
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
        if config.PRICES_ENABLED:
            self.prices, self.precio_error = build_price_provider()
        else:
            self.prices, self.precio_error = None, "Precios deshabilitados (PRICES_ENABLED=0)"

    # ------------------------------------------------------------------

    @app_commands.command(
        name="buscar",
        description="Busca una carta por código o nombre: imagen y datos de la carta",
    )
    @app_commands.describe(carta="Código (OP01-001, ST01-001...) o nombre de la carta")
    async def buscar(self, interaction: discord.Interaction, carta: str) -> None:
        await interaction.response.defer(ephemeral=False)
        try:
            card = await asyncio.to_thread(self.cards.resolve_card, carta)
        except Exception as exc:
            await interaction.followup.send(
                f"❌ No pude buscar la carta: {exc}", ephemeral=True)
            return

        if not card:
            await interaction.followup.send(
                f"❌ No encontré ninguna carta con «{carta}». Revisa el código (p. ej. OP01-001) o el nombre.",
                ephemeral=True)
            return

        # ---------- precios (deshabilitados por defecto; ver config.PRICES_ENABLED) ----------
        precio: PriceResult | None = None
        precio_error: str | None = None
        if config.PRICES_ENABLED:
            precio_error = self.precio_error
            if self.prices is not None:
                try:
                    precio = await asyncio.to_thread(
                        self.prices.get_prices, card.get("name") or carta, card.get("id"))
                except Exception as exc:
                    precio_error = str(exc)

        # ---------- embed ----------
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
        archivos = []
        img_url = card.get("image_url")
        if img_url:
            try:
                img = await asyncio.to_thread(_descargar_imagen, img_url)
                if img:
                    embed.set_image(url="attachment://carta.png")
                    archivos.append(discord.File(img, filename="carta.png"))
            except Exception:
                pass

        await interaction.followup.send(embed=embed, files=archivos)

    # ------------------------------------------------------------------

    @app_commands.command(name="precio", description="Precios de una carta (deshabilitado por ahora)")
    @app_commands.describe(carta="Código o nombre de la carta")
    async def precio(self, interaction: discord.Interaction, carta: str) -> None:
        await interaction.response.defer()
        if not config.PRICES_ENABLED:
            await interaction.followup.send(
                "💶 Los precios están **deshabilitados** por ahora (no hay API de precios "
                "disponible). Se reactivarán cuando haya un proveedor funcional.",
                ephemeral=True)
            return
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
