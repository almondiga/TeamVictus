"""Smoke test: comprueba que el bot y todos los cogs se cargan sin conexión."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 1) Construcción del bot
from bot import OPCGBot
bot = OPCGBot()
print("OK  bot construido")

# 2) Carga de cogs (equivale a lo que hace setup_hook, sin sincronizar)
import asyncio


async def main():
    await bot.load_extension("cogs.search")
    await bot.load_extension("cogs.loans")
    await bot.load_extension("cogs.listing")
    await bot.load_extension("cogs.collection")
    print(f"OK  cogs cargados: {[c.qualified_name for c in bot.cogs.values()]}")

    # Comandos registrados
    cmds = [c.name for c in bot.tree.get_commands()]
    esperados = {"buscar", "precio", "prestar", "devolver", "prestamos",
                 "listado", "importar", "exportar", "op-sync", "coleccion"}
    faltan = esperados - set(cmds)
    assert not faltan, f"faltan comandos: {faltan}"
    print(f"OK  comandos registrados ({len(cmds)}): {sorted(cmds)}")


asyncio.run(main())
print("✅ SMOKE TEST OK")
