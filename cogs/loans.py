"""Préstamos de cartas: /prestar, /devolver y /prestamos.

Los préstamos son GLOBALES: se guardan en una sola base de datos y se ven en
todos los servidores donde esté el bot (la columna guild_id solo recuerda en
qué servidor se creó cada préstamo).
"""
from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
from carddata import CardData, CardDataFallback, build_card_data, normalize_code


def _codigo_valido(texto: str) -> str | None:
    """Valida que el texto sea un código de carta: `OP01-001` u `OP01 001`.

    Devuelve el código normalizado, o None si no parece un código válido
    (rechaza nombres y códigos incompletos como 'op01')."""
    texto = (texto or "").strip()
    if not texto or not any(sep in texto for sep in ("-", " ")):
        return None
    return normalize_code(texto)


def _dividir_codigos(texto: str) -> tuple[list[str], list[str]]:
    """Divide el parámetro por comas: 'OP01-001, op02 002' -> (códigos válidos, inválidos)."""
    validos: list[str] = []
    invalidos: list[str] = []
    for parte in (texto or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        codigo = _codigo_valido(parte)
        (validos if codigo else invalidos).append(codigo or parte)
    return validos, invalidos


class LoansCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cards: CardData | CardDataFallback = build_card_data()

    # ------------------------------------------------------------------

    @app_commands.command(name="prestar", description="Registra cartas que prestas (o que presta otra persona)")
    @app_commands.describe(
        carta="Código(s) de carta: OP01-001, OP02-002 (varios separados por coma)",
        a="¿A quién se las presta? (quien las recibe)",
        prestador="Quién presta las cartas (por defecto: tú)",
        nota="Nota opcional (fecha de devolución pactada, etc.)",
    )
    async def prestar(self, interaction: discord.Interaction,
                      carta: str, a: discord.Member,
                      prestador: discord.Member | None = None,
                      nota: str | None = None) -> None:
        await interaction.response.defer(ephemeral=False)
        presta = prestador or interaction.user
        if a.id == presta.id:
            await interaction.followup.send("🤔 No puedes prestarte una carta a ti mismo.", ephemeral=True)
            return

        codigos, invalidos = _dividir_codigos(carta)
        if not codigos:
            await interaction.followup.send(
                "❌ En los préstamos solo se admiten **códigos de carta** "
                "(`OP01-001` u `OP01 001`, separados por coma si son varios), no nombres.",
                ephemeral=True)
            return
        if invalidos:
            await interaction.followup.send(
                "❌ Estos códigos no son válidos (solo `OP01-001` u `OP01 001`): "
                f"{', '.join(invalidos)}", ephemeral=True)
            return

        def _resolver(prov, lista):
            return [(c, prov.resolve_card(c)) for c in lista]

        try:
            resueltas = await asyncio.to_thread(_resolver, self.cards, codigos)
        except Exception as exc:
            await interaction.followup.send(f"❌ Error al resolver las cartas: {exc}", ephemeral=True)
            return

        desconocidas = []
        descripcion = []
        for code, card in resueltas:
            if card:
                codigo, nombre = card["id"] or code, card.get("name") or code
            else:
                codigo, nombre = code, code
                desconocidas.append(code)
            await asyncio.to_thread(
                db.add_loan, interaction.guild_id, presta.id, a.id, codigo, nombre, nota)
            descripcion.append(f"`{codigo}` **{nombre}**")

        if desconocidas:
            await interaction.followup.send(
                f"⚠️ Códigos no encontrados en el catálogo (guardados igualmente): "
                f"{', '.join(desconocidas)}", ephemeral=True)

        MAX_LINEAS = 15
        texto = "\n".join(descripcion[:MAX_LINEAS])
        if len(descripcion) > MAX_LINEAS:
            texto += f"\n… y {len(descripcion) - MAX_LINEAS} más"
        embed = discord.Embed(
            title=f"📤 Préstamos registrados ({len(descripcion)})",
            color=config.COLOR_OK,
            description=texto,
        )
        embed.add_field(name="Prestadas a", value=a.mention, inline=True)
        embed.add_field(name="Prestadas por", value=presta.mention, inline=True)
        if nota:
            embed.add_field(name="Nota", value=nota, inline=False)
        embed.set_footer(text="Para devolver: /devolver carta:<código> a:@usuario (o /devolver id:<ID>)")
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------

    @app_commands.command(name="devolver", description="Marca préstamo(s) como devueltos")
    @app_commands.describe(
        id="ID del préstamo (lo ves en /prestamos o al prestar)",
        carta="Código(s) de carta devuelta: OP01-001, OP02-002 (varios separados por coma)",
        a="La otra parte del préstamo (a quien se devuelve la carta)",
        devuelve="Quién devuelve la carta (por defecto: tú)",
    )
    async def devolver(self, interaction: discord.Interaction,
                       id: int | None = None,
                       carta: str | None = None,
                       a: discord.Member | None = None,
                       devuelve: discord.Member | None = None) -> None:
        if id is not None:
            ok = await asyncio.to_thread(db.return_loan, id, interaction.user.id)
            if not ok:
                await interaction.response.send_message(
                    f"❌ No encontré el préstamo #{id} activo del que seas parte.", ephemeral=True)
                return
            await interaction.response.send_message(f"✅ Préstamo #{id} marcado como devuelto.",
                                                    ephemeral=False)
            return

        if carta:
            await interaction.response.defer()
            codigos, invalidos = _dividir_codigos(carta)
            if not codigos:
                await interaction.followup.send(
                    "❌ En las devoluciones solo se admiten **códigos de carta** "
                    "(`OP01-001` u `OP01 001`, separados por coma si son varios), no nombres.",
                    ephemeral=True)
                return
            if invalidos:
                await interaction.followup.send(
                    "❌ Estos códigos no son válidos (solo `OP01-001` u `OP01 001`): "
                    f"{', '.join(invalidos)}", ephemeral=True)
                return

            quien = devuelve or interaction.user  # quién devuelve (por defecto, quien ejecuta)

            if a is not None:
                if a.id == quien.id:
                    await interaction.followup.send(
                        "🤔 La otra parte y quien devuelve no pueden ser la misma persona.", ephemeral=True)
                    return
                lineas = []
                total = 0
                for code in codigos:
                    n = await asyncio.to_thread(
                        db.return_loans_by_pair, code, a.id, quien.id)
                    total += n
                    lineas.append(f"`{code}` → **{n}** devuelto(s)")
                if total == 0:
                    await interaction.followup.send(
                        f"❌ No hay préstamos activos de {', '.join(f'`{c}`' for c in codigos)} "
                        f"entre {a.mention} y {quien.mention}.", ephemeral=True)
                    return
                await interaction.followup.send(
                    f"✅ **{total}** préstamo(s) devueltos entre {a.mention} y {quien.mention}:\n"
                    + "\n".join(lineas))
                return

            # sin 'a': se devuelven las cartas de las que el que devuelve es parte
            lineas = []
            total = 0
            for code in codigos:
                n = await asyncio.to_thread(
                    db.return_loans_of_user, code, quien.id)
                total += n
                lineas.append(f"`{code}` → **{n}** devuelto(s)")
            if total == 0:
                await interaction.followup.send(
                    f"❌ No hay préstamos activos de {', '.join(f'`{c}`' for c in codigos)} "
                    f"en los que {quien.mention} sea parte.", ephemeral=True)
                return
            await interaction.followup.send(
                f"✅ **{total}** préstamo(s) de {quien.mention} devueltos:\n" + "\n".join(lineas))
            return

        await interaction.response.send_message(
            "Uso: `/devolver id:123` o `/devolver carta:OP01-001,OP02-002 a:@usuario`", ephemeral=True)

    # ------------------------------------------------------------------

    @app_commands.command(name="prestamos", description="Lista los préstamos (de todos los servidores)")
    @app_commands.describe(usuario="Filtrar por usuario (prestados o recibidos)",
                           historial="Mostrar también los ya devueltos")
    async def prestamos(self, interaction: discord.Interaction,
                        usuario: discord.Member | None = None,
                        historial: bool = False) -> None:
        loans = await asyncio.to_thread(
            db.list_loans,
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
            guild = interaction.guild
            lender = guild.get_member(row["lender_id"]) if guild else None
            borrower = guild.get_member(row["borrower_id"]) if guild else None
            lender_txt = lender.mention if lender else f"<@{row['lender_id']}>"
            borrower_txt = borrower.mention if borrower else f"<@{row['borrower_id']}>"
            # En la vista por defecto todos están pendientes; el estado solo
            # aporta información cuando se pide el historial (hay devueltos).
            estado = " · ✅ devuelto" if row["returned_at"] else (
                " · ⏳ pendiente" if historial else "")
            nombre = f"`{row['card_code']}` {row['card_name'] or ''}".strip()
            embed.add_field(
                name=f"#{row['id']} · {nombre}",
                value=f"{lender_txt} → {borrower_txt} · "
                      f"{row['lent_at'][:10]}{estado}"
                      + (f"\n📝 {row['note']}" if row["note"] else ""),
                inline=False,
            )
        embed.set_footer(text="Para devolver: /devolver id:<ID>")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LoansCog(bot))
