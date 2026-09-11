"""Punto de entrada del bot de Discord para One Piece TCG.

Ejecutar:  python bot.py
Los comandos (slash) se registran de forma global, por lo que el bot funciona en
todos los servidores a los que se invite.
"""
import discord
from discord.ext import commands

import config


class OPCGBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.search")
        await self.load_extension("cogs.loans")
        await self.load_extension("cogs.listing")
        await self.load_extension("cogs.collection")
        # Sincronización global de comandos (puede tardar hasta 1h en propagarse).
        try:
            await self.tree.sync()
            print("✅ Comandos slash sincronizados (globales).")
        except Exception as exc:
            print(f"⚠️ No se pudieron sincronizar los comandos: {exc}")

    async def on_ready(self) -> None:
        print(f"⚓ {self.user} conectado a {len(self.guilds)} servidor(es).")
        print("Comandos: /buscar /precio /prestar /devolver /prestamos /listado "
              "/coleccion add|remove|ver /importar /exportar /op-sync")

    async def on_guild_join(self, guild: discord.Guild) -> None:
        # Sincroniza los comandos en el servidor nuevo al instante.
        try:
            await self.tree.sync(guild=guild)
        except Exception:
            pass


bot = OPCGBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction,
                               error: discord.app_commands.AppCommandError) -> None:
    if isinstance(error, discord.app_commands.CommandNotFound):
        return
    print(f"Error en /{interaction.command.name if interaction.command else '?'}: {error}")
    try:
        mensaje = "⚠️ Algo falló al ejecutar el comando. Revisa el .env (credenciales de Cardmarket / optcg-api)."
        if interaction.response.is_done():
            await interaction.followup.send(mensaje, ephemeral=True)
        else:
            await interaction.response.send_message(mensaje, ephemeral=True)
    except Exception:
        pass


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("❌ Falta DISCORD_TOKEN en el .env")
    bot.run(config.DISCORD_TOKEN)
