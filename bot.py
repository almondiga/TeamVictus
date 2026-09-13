"""Punto de entrada del bot de Discord para One Piece TCG.

Ejecutar:  python bot.py
Los comandos (slash) se registran de forma global, por lo que el bot funciona en
todos los servidores a los que se invite.

Además expone un endpoint HTTP /health (puerto $PORT o 8080) para que sea compatible
con hosts tipo web (Render free + UptimeRobot, livemy.app, etc.) y poder monitorizar
que el proceso está vivo.
"""
import os
import socket

import discord
from aiohttp import web
from discord.ext import commands

import config


def _cerrojo_instancia() -> None:
    """Candado anti-duplicados (solo al ejecutar `python bot.py`):
    evita que dos instancias corran a la vez (la tarea programada reinicia el bot
    si se cae; sin esto, un arranque manual mientras la tarea está activa crea una
    segunda instancia que bloquea la BD SQLite y pelea por la conexión de Discord)."""
    _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock_socket.bind(("127.0.0.1", 8181))
        _lock_socket.listen(1)
    except OSError:
        raise SystemExit(
            "❌ Ya hay otra instancia del bot en marcha (puerto de bloqueo 127.0.0.1:8181 ocupado). "
            "Detén la otra antes de arrancar de nuevo.")


class OPCGBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        # Nota: todos los comandos son slash; no se usa message_content
        # (es un intent privilegiado y evita tocar el Developer Portal).
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        self.loop.create_task(self._health_server())
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

    async def _health_server(self) -> None:
        """Mini servidor HTTP para health-checks de la plataforma de hosting."""
        app = web.Application()

        async def health(request: web.Request) -> web.Response:
            return web.json_response({
                "status": "ok",
                "bot": str(self.user) if self.user else "conectando",
                "guilds": len(self.guilds),
            })

        async def index(request: web.Request) -> web.Response:
            return web.Response(text="OP TCG Discord Bot online ⚓")

        app.router.add_get("/health", health)
        app.router.add_get("/", index)
        port = int(os.getenv("PORT", "8080"))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"🩺 Health endpoint activo en :{port}/health")

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
    nombre = interaction.command.name if interaction.command else "?"
    print(f"Error en /{nombre}: {error!r}", flush=True)
    try:
        with open("bot_errors.log", "a", encoding="utf-8") as f:
            f.write(f"[{__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S}] /{nombre}: {error!r}\n")
    except Exception:
        pass
    detalle = str(error)[:180]
    mensaje = (f"⚠️ Algo falló al ejecutar el comando.\n"
               f"**Detalle:** {detalle or error.__class__.__name__}\n"
               f"*Si el detalle menciona credenciales, revisa el .env (Cardmarket / optcg-api).*")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(mensaje, ephemeral=True)
        else:
            await interaction.response.send_message(mensaje, ephemeral=True)
    except Exception:
        pass


if __name__ == "__main__":
    _cerrojo_instancia()
    if not config.DISCORD_TOKEN:
        raise SystemExit("❌ Falta DISCORD_TOKEN en el .env")
    bot.run(config.DISCORD_TOKEN)
