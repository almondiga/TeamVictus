"""Configuración del bot. Lee las variables desde .env (ver .env.example) o del entorno."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # funciona también con variables de entorno del sistema

# --- Discord ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")

# --- Cardmarket (OAuth 1.0a) ---
# Crea una app en https://www.cardmarket.com/es/Account/MyApplications
CARDMARKET_APP_TOKEN = os.getenv("CARDMARKET_APP_TOKEN", "")
CARDMARKET_APP_SECRET = os.getenv("CARDMARKET_APP_SECRET", "")
CARDMARKET_ACCESS_TOKEN = os.getenv("CARDMARKET_ACCESS_TOKEN", "")
CARDMARKET_ACCESS_SECRET = os.getenv("CARDMARKET_ACCESS_SECRET", "")

# --- Datos de cartas: optcg-api (comunidad, MIT) ---
# La instancia pública requiere clave (pedir acceso no comercial por issue/email en
# https://github.com/arjunkai/optcg-api) o desplegar tu propia instancia (código MIT).
OPTCG_API_URL = os.getenv("OPTCG_API_URL", "https://optcg-api.arjunbansal-ai.workers.dev").rstrip("/")
OPTCG_API_KEY = os.getenv("OPTCG_API_KEY", "")

# --- Precios de Cardmarket ---
# La API oficial de Cardmarket está cerrada a nuevas altas, por eso los precios se
# obtienen de proveedores alternativos (ver prices.py).
# PRICE_PROVIDER = auto | berrywallet | rapidapi | cardmarket
#   auto: berrywallet (si hay clave) > rapidapi (si hay clave) > cardmarket (si credenciales)
PRICE_PROVIDER = os.getenv("PRICE_PROVIDER", "auto")
BERRYWALLET_API_KEY = os.getenv("BERRYWALLET_API_KEY", "")  # gratis: pokewallet.io/dashboard
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")                # RapidAPI "CardMarket API TCG" (free 100/día, añade España)

# --- Parámetros Cardmarket (solo si algún día vuelve el acceso oficial) ---
LANG_EN = 1      # Inglés
LANG_ES = 4      # Español
PAIS_ES = "Spain"  # Cardmarket devuelve el país del vendedor en inglés

# --- Base de datos ---
DB_PATH = os.getenv("DB_PATH", "optcg_bot.db")

# --- Presentación ---
PAGE_SIZE_GRID = 6        # Cartas por página en el listado (cuadrícula 3x2)
PAGE_SIZE_LIST = 12       # Cartas por página en listas de texto
MAX_RESULTS_LISTADO = 300  # Tope de resultados del listado para no saturar

# --- Colores de la embeds ---
COLOR_PRIMARY = 0xF7B731
COLOR_OK = 0x2ECC71
COLOR_WARN = 0xE67E22
COLOR_ERROR = 0xE74C3C
