"""Comando /buscar: imagen y datos de la carta, con búsqueda tokenizada.

- Código exacto (OP01-001, ST01-001...) -> ficha directa de esa carta.
- Cualquier otro texto ('op01', 'luffy', nombre parcial) -> búsqueda tokenizada
  de coincidencias: si hay varias, se muestran en una cuadrícula con imágenes
  (paginada) más un selector para abrir la ficha de la que quieras.

Los precios usan BerryWallet (gratis, sin tarjeta): Cardmarket EUR
(Trend/Media/Mínimo). La comparativa por país España solo aparece si hay un
proveedor con desglose por país (RapidAPI, opcional). Se activan con
PRICES_ENABLED=1 en el .env.
"""
from __future__ import annotations

import asyncio
import datetime
import io
import traceback

import discord
from discord import app_commands
from discord.ext import commands
import requests

import config
from carddata import (CardData, CardDataFallback, build_card_data, normalize_code,
                      variante_nombre)
from cogs.listing import GridView
from prices import PriceResult, build_price_provider, URL_MKM_SEARCH

try:
    from arts import ArteMatcher
except Exception:  # PIL/requests ausentes: se degrada sin mapa de artes
    ArteMatcher = None  # type: ignore

URL_MKM_SEARCH = URL_MKM_SEARCH

MAX_COINCIDENCIAS = 25  # tope de coincidencias en la cuadrícula / selector


def _log_error(origen: str, exc: Exception) -> None:
    """Registra un error de las vistas (botones/selector) en bot_errors.log."""
    try:
        with open("bot_errors.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {origen}: {exc!r}\n")
            f.write("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)) + "\n")
    except Exception:
        pass


class SearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cards: CardData | CardDataFallback = build_card_data()
        if config.PRICES_ENABLED:
            self.prices, self.precio_error = build_price_provider()
        else:
            self.prices, self.precio_error = None, "Precios deshabilitados (PRICES_ENABLED=0)"
        self._artes = None  # ArteMatcher (perezoso): arte de cada variante por imagen

    def _matcher_artes(self):
        """Matcher de artes (imagen -> blueprint de CardTrader), si aplica."""
        if getattr(self, "_artes", None) is None and ArteMatcher is not None:
            ct = getattr(self.prices, "ct", None)
            fn = getattr(self.cards, "variants_of", None)
            if ct is not None and fn is not None:
                try:
                    self._artes = ArteMatcher(ct, self.cards)
                except Exception:
                    self._artes = None
        return self._artes

    async def _arte_de(self, card_id: str | None) -> dict | None:
        """Arte resuelto por imagen para UNA variante: {blueprint_id, label} o None."""
        matcher = self._matcher_artes()
        if matcher is None or not card_id:
            return None
        try:
            mapa = await asyncio.to_thread(matcher.mapa_de_carta, card_id)
        except Exception:
            return None
        if not mapa:
            return None
        return mapa.get((card_id or "").upper())

    # ------------------------------------------------------------------

    @app_commands.command(
        name="buscar",
        description="Busca una carta por código o nombre: imagen y datos de la carta",
    )
    @app_commands.describe(carta="Código (OP01-001, ST01-001...), nombre o token (op01, luffy)")
    async def buscar(self, interaction: discord.Interaction, carta: str) -> None:
        await interaction.response.defer(ephemeral=False)

        # Código completo (OP01-001) -> ficha directa si existe.
        code = normalize_code(carta)
        if code:
            try:
                card = await asyncio.to_thread(self.cards.resolve_card, carta)
            except Exception as exc:
                await interaction.followup.send(
                    f"❌ No pude buscar la carta: {exc}", ephemeral=True)
                return
            if card:
                await self._enviar_carta(interaction, card)
                return

        # ---- búsqueda tokenizada: 'op01' o 'luffy' -> coincidencias ----
        try:
            matches = await asyncio.to_thread(
                self.cards.search_cards, carta, MAX_COINCIDENCIAS)
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

        # Varias coincidencias -> cuadrícula con imágenes + selector
        total = len(matches)
        aviso = ""
        if total >= MAX_COINCIDENCIAS:
            aviso = (f"\nMostrando las **{MAX_COINCIDENCIAS}** primeras. "
                     f"Para más resultados usa `/listado` con filtros "
                     f"(p. ej. `/listado set:{carta.upper()}`).")
        vista = BuscarResultadosView(
            self, matches, f"**{total} coincidencias** para «{carta}».{aviso}")
        fichero = await vista.render_pagina(0)
        msg = await interaction.followup.send(
            content=vista.resumen_texto(), file=fichero, view=vista)
        vista.message = msg

    # ------------------------------------------------------------------

    async def _precio_para(self, card: dict) -> tuple[PriceResult | None, str | None, dict | None]:
        """Consulta precios al proveedor configurado. Devuelve (precio, error, arte).

        `arte` es el mapa resuelto por imagen (arts.py) de ESTA variante:
        {'blueprint_id': int, 'label': str} — permite que cada arte cotice con su
        precio real (p. ej. Manga Panel ≠ Wanted ≠ Red Manga en la misma carta).
        """
        if not config.PRICES_ENABLED or self.prices is None:
            return None, None, None
        art = None
        try:
            art = await self._arte_de(card.get("id"))
        except Exception:
            art = None
        try:
            precio = await asyncio.to_thread(
                self.prices.get_prices, card.get("name") or "", card.get("id"), art)
            return precio, None, art
        except Exception as exc:
            return None, str(exc), art

    async def _embed_carta(self, card: dict, precio: PriceResult | None,
                           precio_error: str | None) -> tuple[discord.Embed, list[discord.File], str | None]:
        """Construye el embed de una carta (imagen + campos + precios)."""
        desc = f"`{card.get('id', '?')}`"
        tipos = ", ".join(card["types"]) if card.get("types") else None
        if tipos:
            desc += f" · {tipos[:100]}"
        embed = discord.Embed(
            title=f"{card.get('name', '?')}",
            description=desc,
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
        """Envía (o edita el mensaje actual con) la ficha completa de una carta.

        Si la carta tiene variantes (Normal, Alternate Art, Reprint...), añade un
        paginador ◀ Variante ▶ para recorrerlas, reutilizando los mismos precios.
        """
        precio, precio_error, _ = await self._precio_para(card)

        vista = None
        fn = getattr(self.cards, "variants_of", None)
        if fn:
            try:
                variantes = await asyncio.to_thread(fn, card.get("id") or "")
            except Exception:
                variantes = None
            if variantes and len(variantes) > 1:
                vista = FichaView(self, variantes)
                for i, v in enumerate(variantes):
                    if (v.get("id") or "").upper() == (card.get("id") or "").upper():
                        vista.indice = i
                        break
                vista._actualizar_botones()
                embed, archivos = await vista._embed_indice(vista.indice)
            else:
                embed, archivos, _ = await self._embed_carta(card, precio, precio_error)
        else:
            embed, archivos, _ = await self._embed_carta(card, precio, precio_error)

        if editar:
            # siempre se llama tras interaction.response.defer() (selector del listado)
            await interaction.edit_original_response(
                content=None, embed=embed, attachments=archivos, view=vista)
            if vista:
                vista.message = interaction.message
        else:
            msg = await interaction.followup.send(embed=embed, files=archivos, view=vista)
            if vista:
                vista.message = msg

    # ------------------------------------------------------------------

    @app_commands.command(name="precio", description="Precios de una carta (Cardmarket EUR)")
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


# ----------------------------- vista de resultados -----------------------------

class BuscarResultadosView(GridView):
    """Cuadrícula de coincidencias con imágenes + selector para abrir una ficha."""

    def __init__(self, cog: SearchCog, cartas: list[dict], cabecera: str) -> None:
        super().__init__(cartas, 0, "")
        self.cog = cog
        self.cabecera = cabecera
        opciones = []
        for c in cartas[:MAX_COINCIDENCIAS]:
            cid = c.get("id") or "?"
            nombre = (c.get("name") or "?")[:90]
            desc = f"{cid} · {c.get('rarity') or '?'}"
            opciones.append(discord.SelectOption(label=nombre, value=cid, description=desc[:95]))
        self.select = discord.ui.Select(
            placeholder=f"Elige una carta ({len(cartas)} coincidencias)",
            options=opciones)
        self.select.callback = self._on_select
        self.add_item(self.select)

    def resumen_texto(self) -> str:
        return f"{self.cabecera}\n{super().resumen_texto()}"

    async def _on_select(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()  # ACK inmediato; la ficha tarda (imagen + precios)
        cid = self.select.values[0]
        card = None
        try:
            card = await asyncio.to_thread(self.cog.cards.resolve_card, cid)
        except Exception:
            card = None
        if not card:
            await interaction.edit_original_response(
                content=f"❌ No pude recuperar la carta «{cid}».", embed=None,
                attachments=[], view=None)
            return
        await self.cog._enviar_carta(interaction, card, editar=True)


# ----------------------------- variantes de una carta -----------------------------

class FichaView(discord.ui.View):
    """Paginador de variantes de una carta en /buscar: ◀ Variante ▶ cambia la
    imagen y el código (Normal, Alternate Art, Reprint...) manteniendo los precios."""

    def __init__(self, cog: SearchCog, variantes: list[dict]) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.variantes = variantes
        self.indice = 0
        self._precios_cache: dict[int, tuple[PriceResult | None, str | None, dict | None]] = {}
        self._actualizar_botones()

    def _actualizar_botones(self) -> None:
        self.prev.disabled = self.indice <= 0
        self.next.disabled = self.indice >= len(self.variantes) - 1

    async def _precio_indice(self, indice: int) -> tuple[PriceResult | None, str | None, dict | None]:
        """Precios DE ESA VARIANTE (cada variante tiene su propio precio en los
        proveedores: Normal, Alternate Art/Paralela y Reprint cotizan distinto)."""
        if indice not in self._precios_cache:
            card = self.variantes[indice]
            self._precios_cache[indice] = await self.cog._precio_para(card)
        return self._precios_cache[indice]

    async def _embed_indice(self, indice: int) -> tuple[discord.Embed, list[discord.File]]:
        card = self.variantes[indice]
        precio, precio_error, arte = await self._precio_indice(indice)
        embed, archivos, _ = await self.cog._embed_carta(card, precio, precio_error)
        etiqueta = variante_nombre(card, self.variantes)
        # si el arte está resuelto por imagen, muestra el nombre real del arte
        # (p. ej. 'Manga Panel Alternate Art') en lugar de 'Alternate Art 1/2'
        if arte and (arte.get("label") or ""):
            etiqueta = arte["label"]
        embed.set_footer(
            text=f"Variante {indice + 1}/{len(self.variantes)} · {etiqueta}")
        await self._anotar_cotizacion_compartida(indice, precio, embed)
        return embed, archivos

    async def _anotar_cotizacion_compartida(self, indice: int,
                                            precio: PriceResult | None,
                                            embed: discord.Embed) -> None:
        """Si varias variantes del mismo tipo comparten cotización (los proveedores no
        distinguen los artes individuales), lo indica en el footer para que no parezca
        que el precio «no se actualiza»."""
        try:
            if precio is None or precio.trend is None:
                return
            card = self.variantes[indice]
            tipo = ((card.get("variant_type") or "").strip()
                    or (card.get("finish") or "").strip())
            if not tipo:
                return
            hermanos = [i for i, v in enumerate(self.variantes)
                        if i != indice and ((v.get("variant_type") or "").strip()
                                            or (v.get("finish") or "").strip()) == tipo]
            if not hermanos:
                return
            mismo = 1
            for i in hermanos:
                p, _, _ = await self._precio_indice(i)
                if p and p.trend == precio.trend:
                    mismo += 1
            if mismo == len(hermanos) + 1 and mismo >= 2:
                viejo = embed.footer.text or ""
                embed.set_footer(
                    text=f"{viejo}\n⚠️ Las {mismo} variantes «{tipo}» comparten "
                         "cotización (mismo arte o artes sin distinguir).")
        except Exception:
            pass

    async def _mostrar(self, interaction: discord.Interaction, indice: int) -> None:
        # ACK inmediato: Discord exige responder en <=3 s
        try:
            await interaction.response.defer()
        except Exception as exc:
            _log_error("FichaView._mostrar.defer", exc)
            return
        try:
            if indice < 0 or indice >= len(self.variantes):
                return
            self.indice = indice
            self._actualizar_botones()
            embed, archivos = await self._embed_indice(indice)
            await interaction.edit_original_response(
                content=None, embed=embed, attachments=archivos, view=self)
        except Exception as exc:
            _log_error(f"FichaView._mostrar variante {indice + 1}", exc)
            try:
                await interaction.edit_original_response(
                    content=f"⚠️ No pude mostrar la variante {indice + 1}: {str(exc)[:120]}",
                    view=self)
            except Exception:
                pass

    @discord.ui.button(label="◀ Variante", style=discord.ButtonStyle.secondary, row=0)
    async def prev(self, interaction: discord.Interaction,
                   button: discord.ui.Button) -> None:
        await self._mostrar(interaction, self.indice - 1)

    @discord.ui.button(label="Variante ▶", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction,
                   button: discord.ui.Button) -> None:
        await self._mostrar(interaction, self.indice + 1)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if getattr(self, "message", None) is None:
            return
        try:
            await self.message.edit(view=self)
        except Exception:
            pass


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
