"""Préstamos de cartas: /prestar, /devolver y /prestamos.

Cada servidor tiene su propio registro (guild_id), y cada entrada guarda quién
presta (lender), a quién (borrower), qué carta (código + nombre) y una nota.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
from carddata import CardData, CardDataFallback, build_card_data


class LoansCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cards: CardData | CardDataFallback = build_card_data()

    # ------------------------------------------------------------------

    @app_commands.command(name="prestar", description="Registra una carta que has prestado")
    @app_commands.describe(
        carta="Código (OP01-001) o nombre de la carta prestada",
        a="¿A quién se la has prestado?",
        nota="Nota opcional (fecha de devolución pactada, etc.)",
    )
    async def prestar(self, interaction: discord.Interaction,
                      carta: str, a: discord.Member, nota: str | None = None) -> None:
        await interaction.response.defer(ephemeral=False)
        try:
            card = await asyncio.to_thread(self.cards.resolve_card, carta)
        except Exception as exc:
            await interaction.followup.send(f"❌ Error al resolver la carta: {exc}", ephemeral=True)
            return

        if not card:
            await interaction.followup.send(
                f"❌ No encontré «{carta}». Puedo guardarla igualmente con el código, "
                f"pero dime el código exacto (p. ej. OP01-001).", ephemeral=True)
            return

        if a.id == interaction.user.id:
            await interaction.followup.send("🤔 No puedes prestarte una carta a ti mismo.", ephemeral=True)
            return

        loan_id = await asyncio.to_thread(
            db.add_loan, interaction.guild_id, interaction.user.id, a.id,
            card["id"] or carta, card.get("name") or carta, nota)
        embed = discord.Embed(
            title="📤 Préstamo registrado",
            color=config.COLOR_OK,
            description=f"`{card.get('id', carta)}` **{card.get('name', carta)}**",
        )
        embed.add_field(name="Prestada a", value=a.mention, inline=True)
        embed.add_field(name="Prestada por", value=interaction.user.mention, inline=True)
        if nota:
            embed.add_field(name="Nota", value=nota, inline=False)
        embed.set_footer(text=f"ID del préstamo: #{loan_id} · usa /devolver id:{loan_id} al recuperarla")
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------

    @app_commands.command(name="devolver", description="Marca un préstamo como devuelto")
    @app_commands.describe(
        id="ID del préstamo (lo ves en /prestamos o al prestar)",
        carta="Alternativa: código de la carta devuelta",
        a="Alternativa: usuario que la devuelve (si usas el filtro por carta)",
    )
    async def devolver(self, interaction: discord.Interaction,
                       id: int | None = None,
                       carta: str | None = None,
                       a: discord.Member | None = None) -> None:
        if id is not None:
            ok = await asyncio.to_thread(db.return_loan, interaction.guild_id, id, interaction.user.id)
            if not ok:
                await interaction.response.send_message(
                    f"❌ No encontré el préstamo #{id} activo del que seas parte.", ephemeral=True)
                return
            await interaction.response.send_message(f"✅ Préstamo #{id} marcado como devuelto.",
                                                    ephemeral=False)
            return

        if carta:
            code = None
            from carddata import normalize_code
            code = normalize_code(carta)
            if not code:
                card = await asyncio.to_thread(self.cards.resolve_card, carta)
                code = card["id"] if card else None
            if not code:
                await interaction.response.send_message(
                    f"❌ No pude identificar el código de «{carta}». Usa el ID del préstamo.", ephemeral=True)
                return
            target = a.id if a else interaction.user.id
            n = await asyncio.to_thread(
                db.return_loans_by_card, interaction.guild_id, code, target, interaction.user.id)
            if n == 0:
                await interaction.response.send_message(
                    f"❌ No hay préstamos activos de `{code}` a {a.mention if a else 'ti'} de los que seas parte.",
                    ephemeral=True)
                return
            await interaction.response.send_message(f"✅ {n} préstamo(s) de `{code}` devueltos.",
                                                    ephemeral=False)
            return

        await interaction.response.send_message(
            "Uso: `/devolver id:123` o `/devolver carta:OP01-001 a:@usuario`", ephemeral=True)

    # ------------------------------------------------------------------

    @app_commands.command(name="prestamos", description="Lista los préstamos activos del servidor")
    @app_commands.describe(usuario="Filtrar por usuario (prestados o recibidos)",
                           historial="Mostrar también los ya devueltos")
    async def prestamos(self, interaction: discord.Interaction,
                        usuario: discord.Member | None = None,
                        historial: bool = False) -> None:
        loans = await asyncio.to_thread(
            db.list_loans, interaction.guild_id,
            user_id=usuario.id if usuario else None,
            active_only=not historial, limit=50)

        if not loans:
            await interaction.response.send_message(
                "📭 No hay préstamos registrados." + (" (con ese filtro)" if usuario else ""),
                ephemeral=False)
            return

        embed = discord.Embed(
            title=f"📋 Préstamos ({'historial' if historial else 'activos'})",
            color=config.COLOR_WARN,
        )
        for row in loans:
            lender = self.bot.get_user(row["lender_id"])
            borrower = self.bot.get_user(row["borrower_id"])
            estado = "✅ devuelto" if row["returned_at"] else "⏳ pendiente"
            nombre = f"`{row['card_code']}` {row['card_name'] or ''}".strip()
            embed.add_field(
                name=f"#{row['id']} · {nombre}",
                value=f"{lender.mention if lender else row['lender_id']} → "
                      f"{borrower.mention if borrower else row['borrower_id']} · "
                      f"{row['lent_at'][:10]} · {estado}"
                      + (f"\n📝 {row['note']}" if row.get("note") else ""),
                inline=False,
            )
        embed.set_footer(text="Para devolver: /devolver id:<ID>")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LoansCog(bot))
