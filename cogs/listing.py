"""Comando /listado: busca cartas con cualquier combinación de filtros y muestra
una cuadrícula de imágenes en grande, paginada con botones.

Filtros: set, nombre, color, categoría, rareza, número (código OP), poder mínimo/máximo.
"""
from __future__ import annotations

import asyncio
import io
from concurrent.futures import ThreadPoolExecutor

import discord
from discord import app_commands
from discord.ext import commands
import requests
from PIL import Image, ImageDraw, ImageFont

import config
from carddata import (COLORS, RARITIES, CATEGORIES, CardData, build_card_data)

_executor = ThreadPoolExecutor(max_workers=8)

GRID_COLS = 3
GRID_ROWS = 2
TILE_W = 230
TILE_H = 320
PAD = 14
FOOTER = 34


class ListadoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cards: CardData = build_card_data()

    # ------------------------------------------------------------------

    @app_commands.command(name="listado", description="Busca cartas con filtros y muestra las imágenes en grande")
    @app_commands.describe(
        set="Set (OP-01, ST-01, EB-01...)",
        nombre="Texto en el nombre",
        color="Color de la carta",
        categoria="Leader, Character, Event, Stage, Don",
        rareza="Rareza",
        numero="Número de carta (p. ej. 001, OP01-001)",
        poder_min="Poder mínimo",
        poder_max="Poder máximo",
        orden="Ordenar por (id, name, price, power, cost)",
        ascendente="Orden ascendente (sí) o descendente (no)",
    )
    @app_commands.choices(
        color=[app_commands.Choice(name=c, value=c) for c in COLORS],
        categoria=[app_commands.Choice(name=c, value=c) for c in CATEGORIES],
        rareza=[app_commands.Choice(name=c, value=c) for c in RARITIES],
        orden=[app_commands.Choice(name=o, value=o) for o in ("id", "name", "price", "power", "cost")],
    )
    async def listado(self, interaction: discord.Interaction,
                      set: str | None = None,
                      nombre: str | None = None,
                      color: app_commands.Choice[str] | None = None,
                      categoria: app_commands.Choice[str] | None = None,
                      rareza: app_commands.Choice[str] | None = None,
                      numero: str | None = None,
                      poder_min: int | None = None,
                      poder_max: int | None = None,
                      orden: app_commands.Choice[str] | None = None,
                      ascendente: bool = True) -> None:
        await interaction.response.defer()

        if not isinstance(self.cards, CardData):
            await interaction.followup.send(
                "⚠️ /listado necesita la API de cartas (optcg-api). Configura `OPTCG_API_KEY` "
                "en el .env o despliega tu propia instancia. /buscar sigue funcionando sin ella.",
                ephemeral=True)
            return

        try:
            cartas = await asyncio.to_thread(
                self.cards.list_cards,
                set_id=_normalizar_set(set),
                name=nombre or None,
                color=color.value if color else None,
                category=categoria.value if categoria else None,
                rarity=rareza.value if rareza else None,
                min_power=poder_min,
                sort=orden.value if orden else "id",
                order="asc" if ascendente else "desc",
                page_size=config.MAX_RESULTS_LISTADO,
            )
        except PermissionError as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(f"❌ Error consultando las cartas: {exc}", ephemeral=True)
            return

        # Filtros que la API no soporta directamente (client-side)
        if numero:
            num = numero.strip().upper()
            cartas = [c for c in cartas if num in (c.get("id") or "").upper()]
        if poder_max is not None:
            cartas = [c for c in cartas
                      if c.get("power") is None or (c["power"] or 0) <= poder_max]

        if not cartas:
            await interaction.followup.send("🔍 No hay cartas que cumplan esos filtros.", ephemeral=False)
            return

        if len(cartas) > config.MAX_RESULTS_LISTADO:
            cartas = cartas[: config.MAX_RESULTS_LISTADO]

        view = GridView(cartas, 0, _resumen_filtros(set, nombre, color, categoria, rareza,
                                                    numero, poder_min, poder_max))
        await interaction.followup.send(
            content=view.resumen_texto(),
            file=await view.render_pagina(0),
            view=view,
        )


# ----------------------------- helpers -----------------------------

def _normalizar_set(texto: str | None) -> str | None:
    if not texto:
        return None
    t = texto.strip().upper()
    import re
    m = re.fullmatch(r"([A-Z]{1,4})-?(\d{1,2})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return t


def _resumen_filtros(*args) -> str:
    etiquetas = []
    nombres = ["Set", "Nombre", "Color", "Categoría", "Rareza", "Número", "Poder ≥", "Poder ≤"]
    for nombre, valor in zip(nombres, args):
        if valor:
            v = valor.value if hasattr(valor, "value") else valor
            etiquetas.append(f"{nombre}: **{v}**")
    return " · ".join(etiquetas) if etiquetas else "Sin filtros"


def _descargar(url: str) -> Image.Image | None:
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return None
        return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception:
        return None


def _construir_cuadricula(cartas: list[dict]) -> io.BytesIO:
    """Genera una imagen PNG con las cartas en cuadrícula 3x2 (6 por página)."""
    ancho = GRID_COLS * TILE_W + (GRID_COLS + 1) * PAD
    alto = GRID_ROWS * (TILE_H + FOOTER) + (GRID_ROWS + 1) * PAD
    canvas = Image.new("RGB", (ancho, alto), (35, 39, 42))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 15)
        font_small = ImageFont.truetype("arial.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
        font_small = font

    for i, card in enumerate(cartas[: GRID_COLS * GRID_ROWS]):
        fila, col = divmod(i, GRID_COLS)
        x = PAD + col * (TILE_W + PAD)
        y = PAD + fila * (TILE_H + FOOTER + PAD)

        img = _descargar(card.get("image_url") or "")
        if img:
            img.thumbnail((TILE_W, TILE_H))
            ox = x + (TILE_W - img.width) // 2
            oy = y + (TILE_H - img.height) // 2
            canvas.paste(img, (ox, oy))
        else:
            draw.rectangle([x, y, x + TILE_W, y + TILE_H], fill=(60, 64, 67),
                           outline=(90, 94, 98))
            draw.text((x + 8, y + 8), card.get("id") or "?", font=font, fill=(255, 255, 255))

        precio = f" · ${card.get('price')}" if card.get("price") else ""
        etiqueta = f"{card.get('id') or ''}{precio}"
        draw.text((x + 2, y + TILE_H + 6), etiqueta, font=font, fill=(245, 200, 80))
        draw.text((x + 2, y + TILE_H + 6 + 18),
                  (card.get("name") or "")[:26], font=font_small, fill=(230, 230, 230))

    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    buf.seek(0)
    return buf


# ----------------------------- vista paginada -----------------------------

class GridView(discord.ui.View):
    def __init__(self, cartas: list[dict], pagina: int, resumen: str) -> None:
        super().__init__(timeout=180)
        self.cartas = cartas
        self.pagina = pagina
        self.resumen = resumen
        self._actualizar_botones()

    def _actualizar_botones(self) -> None:
        total_pags = max(1, (len(self.cartas) + config.PAGE_SIZE_GRID - 1) // config.PAGE_SIZE_GRID)
        self.prev.disabled = self.pagina <= 0
        self.next.disabled = self.pagina >= total_pags - 1

    def resumen_texto(self) -> str:
        total_pags = max(1, (len(self.cartas) + config.PAGE_SIZE_GRID - 1) // config.PAGE_SIZE_GRID)
        return (f"**{len(self.cartas)} cartas** · {self.resumen}\n"
                f"Página **{self.pagina + 1}/{total_pags}**")

    async def render_pagina(self, pagina: int) -> discord.File:
        self.pagina = pagina
        self._actualizar_botones()
        trozo = self.cartas[pagina * config.PAGE_SIZE_GRID: (pagina + 1) * config.PAGE_SIZE_GRID]
        buf = await asyncio.to_thread(_construir_cuadricula, trozo)
        return discord.File(buf, filename="listado.png")

    async def _editar(self, interaction: discord.Interaction, pagina: int) -> None:
        fichero = await self.render_pagina(pagina)
        await interaction.response.edit_message(content=self.resumen_texto(), attachments=[fichero],
                                                view=self)
        self.message = interaction.message

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary, row=0)
    async def prev(self, interaction: discord.Interaction,
                   button: discord.ui.Button) -> None:
        await self._editar(interaction, self.pagina - 1)

    @discord.ui.button(label="Siguiente ▶", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction,
                   button: discord.ui.Button) -> None:
        await self._editar(interaction, self.pagina + 1)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if getattr(self, "message", None) is None:
            return
        try:
            await self.message.edit(view=self)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ListadoCog(bot))
