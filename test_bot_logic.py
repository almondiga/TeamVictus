"""Verificación funcional (sin red): lógica pura de los módulos del bot."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
config.DB_PATH = os.path.join(tempfile.gettempdir(), "optcg_test.db")
if os.path.exists(config.DB_PATH):
    os.remove(config.DB_PATH)

import db
from carddata import normalize_code, _clean
from cardmarket import CardmarketClient

fallos = []

def check(nombre, cond, detalle=""):
    print(f"{'OK ' if cond else 'FALLO'}  {nombre} {detalle}")
    if not cond:
        fallos.append(nombre)

# --- normalize_code ---
check("normalize OP1-1", normalize_code("op1-1") == "OP01-001", normalize_code("op1-1"))
check("normalize OP01-001", normalize_code("OP01-001") == "OP01-001")
check("normalize EB-1-2", normalize_code("EB-1-2") == "EB01-002")
check("normalize P-1", normalize_code("P-1") == "P-001")
check("normalize DON-1", normalize_code("DON-1") == "DON-001")
check("normalize ST21-010", normalize_code("st21-010") == "ST21-010")
check("no-code texto", normalize_code("Luffy") is None)

# --- Cardmarket helpers (datos sintéticos) ---
articulos = [
    {"language": {"idLanguage": 1}, "price": 3.0, "count": 2,
     "seller": {"country": "Spain"}},
    {"language": {"idLanguage": 1}, "price": 2.5, "count": 1,
     "seller": {"country": "Germany"}},
    {"language": {"idLanguage": 4}, "price": 1.9, "count": 5,
     "seller": {"country": "Spain"}},   # español: debe quedar fuera
    {"language": {"idLanguage": 1}, "price": 4.0, "count": 3,
     "seller": {"country": "Spain"}},
]
es = CardmarketClient.spanish_offers(articulos)
check("espanol_offers filtra idioma+pais", len(es) == 2, f"n={len(es)}")
check("min_price", CardmarketClient.min_price(es) == 3.0,
      CardmarketClient.min_price(es))

productos = [
    {"enName": "Monkey.D.Luffy", "idProduct": 1},
    {"enName": "Monkey.D.Luffy (SEC)", "idProduct": 2},
]
best = CardmarketClient.best_product(productos, "Monkey.D.Luffy", None)
check("best_product exacto", best["idProduct"] == 1)
best2 = CardmarketClient.best_product(productos, "Monkey.D.Luffy", "OP01-025")
check("best_product por nombre", best2["idProduct"] == 1)

# --- db: préstamos ---
id1 = db.add_loan(111, 100, 200, "OP01-001", "Roronoa Zoro", "volver el viernes")
id2 = db.add_loan(111, 100, 300, "ST01-001", "Monkey.D.Luffy", None)
id3 = db.add_loan(222, 100, 200, "OP01-001", "Roronoa Zoro", None)  # otro servidor
check("prestamos activos", len(db.list_loans(111)) == 2)
check("aislamiento por servidor", len(db.list_loans(222)) == 1)
check("filtro por usuario", len(db.list_loans(111, user_id=200)) == 1)
check("devolver", db.return_loan(111, id1, 200) is True)
check("devolver repetido es False", db.return_loan(111, id1, 200) is False)
check("historial muestra 2", len(db.list_loans(111, active_only=False)) == 2)
check("activos quedan 1", len(db.list_loans(111)) == 1)
check("no se toca otro servidor", len(db.list_loans(222)) == 1)

# --- db: colección ---
db.add_to_collection(111, 100, "OP01-001", "Roronoa Zoro", 1)
db.add_to_collection(111, 100, "OP01-001", "Roronoa Zoro", 3)
db.add_to_collection(111, 200, "OP01-119", "Monkey.D.Luffy", 1)
n_unicas, n_total = db.collection_count(111, 100)
check("coleccion acumula qty", (n_unicas, n_total) == (1, 4), f"{n_unicas}/{n_total}")
check("coleccion aislamiento", db.collection_count(111, 200)[1] == 1)
quitadas = db.remove_from_collection(111, 100, "OP01-001", 2)
check("remove parcial", quitadas == 2 and db.collection_count(111, 100)[1] == 2)
quitadas = db.remove_from_collection(111, 100, "OP01-001", 5)
check("remove total borra fila", quitadas == 2 and db.collection_count(111, 100)[1] == 0)
n = db.import_collection(111, 300, [("op02-001", "Portgas.D.Ace", 2), ("op02-001", None, 1)])
check("import acumula", n == 2 and db.collection_count(111, 300)[1] == 3)

# --- cuadrícula (si Pillow está instalado) ---
try:
    from cogs.listing import _construir_cuadricula
    from carddata import CARD_KEYS
    fake = []
    for i in range(6):
        c = {k: None for k in CARD_KEYS}
        c.update({"id": f"OP01-{i:03d}", "name": f"Carta {i}", "image_url": "http://no-existe.invalid/x.png"})
        fake.append(c)
    buf = _construir_cuadricula(fake)
    from PIL import Image
    img = Image.open(buf)
    check("cuadricula genera PNG", img.format == "PNG" and img.size[0] > 500,
          f"{img.size}")
except ImportError as e:
    print(f"SKIP  cuadricula (Pillow no instalado): {e}")

# --- proveedores de precios (sin red) ---
import prices

class FakeResp:
    def __init__(self, payload):
        self._p = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._p

class FakeSession:
    def __init__(self, payload):
        self._p = payload
        self.calls = []
    def get(self, url, **kw):
        self.calls.append((url, kw))
        return FakeResp(self._p)

# BerryWallet: respuesta con precios de Cardmarket en EUR
payload_bw = {"data": [{
    "card_number": "OP01-001", "name": "Roronoa Zoro", "rarity": "L",
    "cardmarket": {"product_name": "Roronoa Zoro (OP01-001)",
                   "product_url": "https://www.cardmarket.com/en/OnePiece/Products/123",
                   "prices": {"avg": 1.64, "low": 0.50, "trend": 1.70}}}]}
sess_bw = FakeSession(payload_bw)
prov_bw = prices.BerryWalletProvider("clave_test", session=sess_bw)
res_bw = prov_bw.get_prices("Roronoa Zoro", "OP01-001")
check("berrywallet trend", res_bw.trend == 1.70, res_bw)
check("berrywallet avg/low", res_bw.avg == 1.64 and res_bw.low == 0.50)
check("berrywallet link", res_bw.link and "cardmarket" in res_bw.link)
check("berrywallet sin pais", res_bw.es_disponible is False)
check("berrywallet selecciona por codigo", sess_bw.calls[0][1]["params"]["q"] == "OP01-001")

# RapidAPI: respuesta con precio por país ES
payload_rapi = [{
    "name": "Roronoa Zoro", "card_code_number": "OP01-001",
    "prices": {"cardmarket": {"currency": "EUR", "lowest_near_mint": 2.0,
                              "lowest_near_mint_ES": 3.2,
                              "7d_average": 1.9, "30d_average": 1.8}}}]
sess_rapi = FakeSession(payload_rapi)
prov_rapi = prices.RapidApiCMProvider("clave_test", session=sess_rapi)
res_rapi = prov_rapi.get_prices("Roronoa Zoro", "OP01-001")
check("rapidapi es_price", res_rapi.es_disponible and res_rapi.es_price == 3.2,
      res_rapi.es_price)
check("rapidapi trend 7d", res_rapi.trend == 1.9)
check("rapidapi low", res_rapi.low == 2.0)

# fábrica sin ninguna clave -> error claro
config.PRICE_PROVIDER = "auto"
prov, err = prices.build_price_provider()
check("factory sin claves -> (None, error)", prov is None and err and "BERRYWALLET" in err, err)

# caché: segunda llamada no repite request
sess_bw.calls.clear()
prov_bw.get_prices("Roronoa Zoro", "OP01-001")
check("cache evita segunda llamada", len(sess_bw.calls) == 0)

print()
if fallos:
    print(f"❌ {len(fallos)} fallo(s): {fallos}")
    sys.exit(1)
print("✅ TODAS LAS VERIFICACIONES OK")
