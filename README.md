# ⚓ Bot de Discord — One Piece TCG (op.tcg)

Bot en español para servidores de Discord que busca cartas de *One Piece Card Game*,
muestra precios de **Cardmarket (inglés)** con comparativa de **vendedores de España**,
lleva el registro de **cartas prestadas** y gestiona **colecciones** con listados
filtrables e imágenes en grande.

## Comandos

| Comando | Qué hace |
| --- | --- |
| `/buscar carta:<código o nombre>` | Imagen de la carta + precios de Cardmarket en EUR (Trend/Media/Mínimo) y, si el proveedor lo permite, comparativa **España** (mín. near-mint de vendedores españoles). |
| `/precio carta:<código o nombre>` | Solo la comparativa de precios. |
| `/prestar carta:<...> a:<@usuario> nota?` | Registra una carta prestada (quién, a quién, nota). |
| `/devolver id:<ID>` o `carta:<código> a:<@usuario>` | Marca préstamo(s) como devueltos. |
| `/prestamos [usuario] [historial]` | Lista los préstamos activos (o historial) del servidor. |
| `/listado [set] [nombre] [color] [categoría] [rareza] [número] [poder_min] [poder_max] [orden]` | Listado de cartas con cualquier combinación de filtros; las imágenes salen en **cuadrícula en grande**, paginada con botones. |
| `/coleccion add|remove|ver` | Gestiona tu colección (la de otros usuarios también se puede ver y filtrar). |
| `/importar archivo:<CSV>` | Importa colección desde CSV (`card_code,card_name,qty`). |
| `/exportar` | Descarga tu colección como CSV. |
| `/op-sync` | Estado de la sincronización con la app OP.TCG. |

## Requisitos

- Python 3.10+
- Cuenta de Discord y una aplicación creada en el [Developer Portal](https://discord.com/developers/applications)
- Cuenta de Cardmarket (gratis) para los precios
- (Recomendado) Clave de la API comunitaria de cartas [optcg-api](https://github.com/arjunkai/optcg-api)

## Puesta en marcha

```bash
# 1. Clona / descomprime este proyecto y entra
cd optcg-discord-bot

# 2. Dependencias
pip install -r requirements.txt

# 3. Configuración
copy .env.example .env     # Windows
# cp .env.example .env     # Linux/macOS

# 4. Rellena el .env (ver abajo) y arranca
python bot.py
```

### 1. Token de Discord
1. Ve a [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**.
2. Pestaña **Bot** → **Reset Token** → cópialo a `DISCORD_TOKEN`.
3. Pestaña **OAuth2 → URL Generator** → marca `applications.commands` y `bot` → copia la URL y ábrela para invitar al bot a tus servidores. Esa URL de invitación es lo que hace que funcione en **varios servidores** (los comandos son globales).

### 2. Precios de Cardmarket (proveedor alternativo)

> ⚠️ **La API oficial de Cardmarket está cerrada a nuevas altas** (sin fecha de
> reapertura), así que el bot obtiene los precios de un proveedor alternativo:

- **BerryWallet** (recomendado, gratis): precios de Cardmarket en EUR. Regístrate en
  [pokewallet.io/dashboard](https://pokewallet.io/dashboard) (sin tarjeta) y pon la
  clave en `BERRYWALLET_API_KEY`. Límites: 100 req/h y 1.000/día. **No** incluye
  desglose por país.
- **RapidAPI «CardMarket API TCG»** (para la comparativa **España**): añade el
  mínimo near-mint de vendedores de **España**. Suscríbete al **plan free** (100
  req/día) en [rapidapi.com](https://rapidapi.com/tcggopro/api/cardmarket-api-tcg) y
  pon la clave en `RAPIDAPI_KEY`.
- **API oficial** (futuro): si algún día Cardmarket vuelve a aceptar accesos, rellena
  los 4 tokens de [My Applications](https://www.cardmarket.com/es/Account/MyApplications)
  y pon `PRICE_PROVIDER=cardmarket`: recuperarás el filtro exacto por **idioma
  inglés** y las ofertas por **país**.

La selección se hace con `PRICE_PROVIDER=auto` (prioridad: berrywallet > rapidapi >
cardmarket). El filtro fino *por idioma inglés* solo existe con la API oficial; los
proveedores dan el precio de mercado de Cardmarket en EUR. Sin ninguna clave, el bot
sigue funcionando pero `/buscar` muestra la carta sin precios y explica qué falta.

### 3. optcg-api (recomendado para /listado y filtros ricos)
`/listado` (filtros por color, poder, rareza...) y la ficha completa de `/buscar` usan
[optcg-api](https://github.com/arjunkai/optcg-api), un proyecto comunitario MIT. Su
instancia pública está restringida: pide acceso **no comercial** abriendo un issue o
escribiendo a su autor, o despliega tu propia instancia (el código es MIT) y pon la URL
en `OPTCG_API_URL`.

Sin clave, el bot **no se rompe**: `/buscar` y `/precio` funcionan con fallback por
nombre vía Cardmarket, y `/listado` avisa de que necesita la clave.

## Despliegue en Render (24/7)

El repo incluye `render.yaml` (Blueprint) y un `Dockerfile`.

> ⚠️ **Aviso importante sobre el plan free de Render (2026):** los servicios free se
> duermen a los 15 minutos sin tráfico entrante, y un bot de Discord solo habla hacia
> afuera — así que en el plan free el bot **se desconectaría**. Para que corra 24/7 de
> verdad necesitas un **plan de pago** (Starter, ~7 €/mes). Además, el filesystem de
> Render es **efímero**: sin disco persistente, tu base de datos de
> préstamos/colecciones se borra en cada despliegue o reinicio.

**Opción A — Blueprint (recomendada):**
1. El repo ya está en GitHub (TeamVictus).
2. En [dashboard.render.com](https://dashboard.render.com) → **New + → Blueprint** → conecta el repo.
3. Render crea el worker `optcg-bot`. Rellena en el dashboard las variables secretas:
   `DISCORD_TOKEN`, `BERRYWALLET_API_KEY`, `RAPIDAPI_KEY`, `OPTCG_API_KEY`.
4. **Para persistir los datos:** sube el servicio a plan de pago, crea un **disco
   persistente** montado en `/data` (pestaña *Disks*) y define `DB_PATH=/data/optcg_bot.db`.
5. **Deploy** → en unos minutos el bot se conecta.

**Opción B — Manual:** *New + → Web Service* → elige el repo → *Environment: Python* →
*Build: `pip install -r requirements.txt`* → *Start: `python bot.py`* → plan de pago y disco igual que arriba.

**Verificación:** en la pestaña *Logs* del servicio deberías ver
`✅ Comandos slash sincronizados` y `⚓ <bot> conectado a N servidor(es)`.

## Despliegue en Oracle Cloud Always Free (gratis, 24/7) — recomendado

Guía completa paso a paso (crear cuenta, VM, SSH, instalación con un comando,
auto-reinicio con systemd, monitorización con UptimeRobot): **`deploy/oracle/README.md`**.

Instalación en la VM (Ubuntu 24.04):

```bash
curl -sL https://raw.githubusercontent.com/almondiga/TeamVictus/main/deploy/oracle/install_oracle.sh -o install_oracle.sh
chmod +x install_oracle.sh
./install_oracle.sh
```

> El instalador clona el repo en `/opt/optcg-bot`, crea el venv, te pide las claves la
> primera vez y deja el bot como servicio `systemd` con auto-reinicio
> (`systemctl status optcg-bot` / `sudo journalctl -u optcg-bot -f`).

## Despliegue en livemy.app (o cualquier host con Procfile)

El repo incluye `Procfile` (`web: python bot.py`), `runtime.txt` (python-3.12) y un
endpoint `/health` en el puerto `$PORT` (8080 por defecto) para health-checks y
keep-alive.

> ⚠️ **Estado real del plan Free de livemy.app (según su propio tutorial, ago 2026):**
> la app solo está **viva 24 h por despliegue** y luego se apaga (código y datos se
> guardan 30 días) — **no es 24/7**. Para el bot corriendo de forma continua hacen falta
> sus planes de pago (Maker, ~10 $/mes). Su blog de julio 2026 decía "free sin dormir",
> pero su documentación más reciente lo contradice: valida tú mismo el plan actual en
> su web antes de decidir.

**Pasos (el plan free sirve como prueba de 24 h):**
1. El repo está en GitHub y es público.
2. [livemy.app](https://livemy.app) → **New project → Connect repo** → autoriza la app de GitHub y elige TeamVictus.
3. **Project Settings → Environment Variables**: `DISCORD_TOKEN`, `BERRYWALLET_API_KEY`, `RAPIDAPI_KEY`, `OPTCG_API_KEY`.
4. **Deploy** → en 2-4 min verás en los logs `✅ Comandos slash sincronizados` y `⚓ <bot> conectado a N servidor(es)`.
5. Health check: `https://tu-app.livemy.site/health` → `{"status": "ok", ...}`.

**Persistencia de la BD:** en el plan free la app se apaga a las 24 h (los datos se
conservan 30 días); en planes de pago, verifica en su dashboard si los archivos
persisten entre despliegues. Antes de cada despliegue/reinicio, saca un backup con
`/exportar` en Discord y reponlo con `/importar` si hiciera falta.

## Sobre la sincronización con la app OP.TCG

La app **OP.TCG no tiene API pública** y extraer la suya por ingeniería inversa viola
sus términos de servicio, por eso **no hay sincronización automática** de la colección
guardada en la app. En su lugar el bot ofrece:

- `/coleccion add` para añadir cartas,
- `/importar` con un CSV (plantilla: `example_import.csv`),
- `/exportar` para llevar tu colección fuera del bot.

Si la app añade exportación de colección en el futuro, el cog `cogs/collection.py` es
el sitio exacto donde añadir el importador, sin tocar el resto del bot.

## Verificación

El proyecto incluye dos pruebas que no requieren red ni credenciales:

```bash
python test_bot_logic.py   # lógica pura: normalización de códigos, filtros España,
                           # base de datos (préstamos y colecciones), cuadrícula de imágenes
python test_smoke.py       # carga el bot y los 10 comandos sin conectar a Discord
```

## Arquitectura

```
bot.py               -> entrada, registro de cogs y sincronización global de comandos
config.py            -> lee el .env
db.py                -> SQLite (préstamos y colecciones, aislados por servidor y usuario)
carddata.py          -> datos de cartas: optcg-api (rico) + fallback Cardmarket
prices.py            -> proveedores de precios: BerryWallet / RapidAPI (España) / API oficial
cardmarket.py        -> cliente de la API oficial de Cardmarket (OAuth1), usado por prices.py
cogs/search.py       -> /buscar /precio
cogs/loans.py        -> /prestar /devolver /prestamos
cogs/listing.py      -> /listado con cuadrícula de imágenes (Pillow) paginada
cogs/collection.py   -> /coleccion /importar /exportar /op-sync
```

## Fuentes de datos

- **Cartas y campos** (color, poder, rareza, categoría, paralelas...): [optcg-api](https://github.com/arjunkai/optcg-api) (MIT; datos originados del sitio oficial de Bandai y TCGPlayer).
- **Imágenes**: sitio oficial de Bandai (`en.onepiece-cardgame.com/images/cardlist/card/…`).
- **Precios Cardmarket (EUR)**: [BerryWallet](https://www.pokewallet.io/berrywallet-docs) (free) o [CardMarket API TCG en RapidAPI](https://rapidapi.com/tcggopro/api/cardmarket-api-tcg) (free, con precio por país ES). La API oficial de Cardmarket está cerrada a nuevas altas.
