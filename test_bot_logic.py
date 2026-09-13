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

# --- db: préstamos (GLOBALES: visibles en todos los servidores) ---
id1 = db.add_loan(111, 100, 200, "OP01-001", "Roronoa Zoro", "volver el viernes")
id2 = db.add_loan(111, 100, 300, "ST01-001", "Monkey.D.Luffy", None)
id3 = db.add_loan(222, 100, 200, "OP01-001", "Roronoa Zoro", None)  # creado en otro servidor
check("prestamos activos globales (3 de 2 servidores)", len(db.list_loans()) == 3)
check("filtro por usuario cruza servidores", len(db.list_loans(user_id=200)) == 2)
check("devolver", db.return_loan(id1, 200) is True)
check("devolver repetido es False", db.return_loan(id1, 200) is False)
check("historial muestra 3", len(db.list_loans(active_only=False)) == 3)
check("activos quedan 2", len(db.list_loans()) == 2)

# devolver por pareja (orden indiferente) y por "quien devuelve es parte"
id4 = db.add_loan(111, 100, 400, "OP02-001", "Edward.Newgate", None)   # 100 presta a 400
id5 = db.add_loan(111, 400, 100, "OP02-002", "Portgas.D.Ace", None)    # 400 presta a 100
id6 = db.add_loan(111, 100, 500, "OP03-001", None, None)
id7 = db.add_loan(111, 100, 500, "OP03-002", None, None)
check("devolver por pareja (orden directo)",
      db.return_loans_by_pair("OP02-001", 100, 400) == 1)
check("devolver por pareja (orden inverso)",
      db.return_loans_by_pair("OP02-002", 100, 400) == 1)
check("devolver por pareja repetido = 0",
      db.return_loans_by_pair("OP02-001", 100, 400) == 0)
check("devolver por pareja inexistente = 0",
      db.return_loans_by_pair("OP02-001", 100, 500) == 0)
check("devolver sin 'a' (quien devuelve es prestador)",
      db.return_loans_of_user("OP03-001", 100) == 1)
check("devolver sin 'a' (quien devuelve es receptor)",
      db.return_loans_of_user("OP03-002", 500) == 1)
check("pareja cruza servidor (préstamo creado en 222 se devuelve desde cualquier lado)",
      db.return_loans_by_pair("OP01-001", 100, 200) == 1)

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
config.BERRYWALLET_API_KEY = ""
config.RAPIDAPI_KEY = ""
prov, err = prices.build_price_provider()
check("factory sin claves -> (None, error)", prov is None and err and "BERRYWALLET" in err, err)

# caché: segunda llamada no repite request
sess_bw.calls.clear()
prov_bw.get_prices("Roronoa Zoro", "OP01-001")
check("cache evita segunda llamada", len(sess_bw.calls) == 0)

# --- CardTrader: comparativa España (mercado propio, gratis sin tarjeta) ---
class FakeRouteSession:
    def __init__(self, rutas):
        self.rutas = rutas
        self.calls = []
    def get(self, url, **kw):
        self.calls.append((url, kw))
        for clave, payload in self.rutas.items():
            if clave in url:
                return FakeResp(payload)
        return FakeResp([])

payload_ct = {
    "games": [{"id": 7, "name": "One Piece"}, {"id": 1, "name": "Magic: The Gathering"}],
    "expansions": [{"id": 123, "game_id": 7, "code": "OP-01", "name": "Romance Dawn"}],
    "blueprints/export": [{"id": 5001, "name": "Roronoa Zoro (OP01-001)", "expansion_id": 123}],
    "marketplace/products": {"5001": [
        {"id": 1, "quantity": 1, "price": {"cents": 350, "currency": "EUR"},
         "properties_hash": {"condition": "Near Mint", "op_language": "en"},
         "user": {"country_code": "IT"}},
        {"id": 2, "quantity": 3, "price": {"cents": 150, "currency": "EUR"},
         "properties_hash": {"condition": "Near Mint", "op_language": "en"},
         "user": {"country_code": "ES"}},
        {"id": 3, "quantity": 1, "price": {"cents": 400, "currency": "EUR"},
         "properties_hash": {"condition": "Near Mint", "op_foil": True},
         "user": {"country_code": "ES"}},
    ]},
}
sess_ct = FakeRouteSession(payload_ct)
prov_ct = prices.CardTraderProvider("token_test", session=sess_ct)
res_ct = prov_ct.get_prices("Roronoa Zoro", "OP01-001")
check("cardtrader es_price mín no-foil ES", res_ct.es_disponible and res_ct.es_price == 1.5,
      res_ct.es_price)
check("cardtrader es_count solo no-foil ES", res_ct.es_count == 3)
check("cardtrader sin EUR (trend None)", res_ct.trend is None)
check("cardtrader nota aclara mercado propio", res_ct.nota and "no Cardmarket" in res_ct.nota)
check("cardtrader busca por blueprint y language=en",
      any(c[1].get("params", {}).get("blueprint_id") == 5001
          and c[1].get("params", {}).get("language") == "en"
          for c in sess_ct.calls))

# CardTrader sin vendedores ES -> es no disponible
payload_ct_sin = dict(payload_ct)
payload_ct_sin["marketplace/products"] = {"5001": [
    {"id": 1, "quantity": 1, "price": {"cents": 350, "currency": "EUR"},
     "properties_hash": {"condition": "Near Mint", "op_language": "en"},
     "user": {"country_code": "IT"}}]}
res_ct_sin = prices.CardTraderProvider(
    "t", session=FakeRouteSession(payload_ct_sin)).get_prices("Roronoa Zoro", "OP01-001")
check("cardtrader sin ES -> es_disponible False",
      res_ct_sin.es_disponible is False and "Sin vendedores" in (res_ct_sin.nota or ""))

# --- Híbrido: BerryWallet EUR + CardTrader España ---
hyb = prices.HybridProvider(prov_bw, prov_ct)
res_hyb = hyb.get_prices("Roronoa Zoro", "OP01-001")
check("hybrid trend de BerryWallet", res_hyb.trend == 1.70)
check("hybrid es de CardTrader", res_hyb.es_price == 1.5 and res_hyb.es_count == 3)
check("hybrid provider", res_hyb.provider == "berrywallet+cardtrader")

# fábrica: berry + cardtrader -> híbrido; solo cardtrader -> cardtrader
config.BERRYWALLET_API_KEY = "pk_test"
config.CARDTRADER_TOKEN = "ct_token"
config.RAPIDAPI_KEY = ""
prov_h, err_h = prices.build_price_provider()
check("factory berry+cardtrader -> hybrid",
      isinstance(prov_h, prices.HybridProvider), type(prov_h).__name__)
config.BERRYWALLET_API_KEY = ""
prov_ct2, err_ct2 = prices.build_price_provider()
check("factory solo cardtrader -> cardtrader",
      prov_ct2 is not None and prov_ct2.name == "cardtrader",
      prov_ct2.name if prov_ct2 else None)
config.CARDTRADER_TOKEN = ""

# _elegir_con_precios: el primer resultado coincide por código pero sin precios
# -> debe saltar a otra variante del mismo código que sí tenga cardmarket
items_mixtos = [
    {"card_number": "OP01-001", "name": "Roronoa Zoro (Alternate Art)", "cardmarket": None},
    {"card_number": "OP01-001", "name": "Roronoa Zoro", "cardmarket": None},
    {"card_number": "OP01-001", "name": "Roronoa Zoro (001)", "sub_type_name": "Normal",
     "cardmarket": {"prices": {"trend": 2.5, "avg": 2.6, "low": 0.8}}},
]
elegido = prices._elegir_con_precios(items_mixtos, "OP01-001", "Roronoa Zoro")
check("elegir_con_precios salta a variante con precios",
      elegido and elegido["cardmarket"]["prices"]["trend"] == 2.5)
check("elegir_con_precios sin datos devuelve el mejor igualmente",
      prices._elegir_con_precios([{"card_number": "OP01-001", "name": "X",
                                   "cardmarket": None}], "OP01-001", "X") is not None)

# --- CardDataBerry: proveedor de cartas sin optcg (usando BerryWallet) ---
from carddata import CardDataBerry, build_card_data, _set_de_codigo, official_image_url

check("set_de_codigo OP01-001 -> OP-01", _set_de_codigo("OP01-001") == "OP-01")
check("set_de_codigo EB02-019 -> EB-02", _set_de_codigo("EB02-019") == "EB-02")
check("set_de_codigo P-001 -> P", _set_de_codigo("P-001") == "P")
check("set_de_codigo DON-001 -> DON", _set_de_codigo("DON-001") == "DON")

berry_ok_guardado = (config.OPTCG_API_KEY, config.BERRYWALLET_API_KEY, config.LOCAL_DB_PATH)
config.OPTCG_API_KEY = ""
config.BERRYWALLET_API_KEY = "pk_test_falsa"
config.LOCAL_DB_PATH = "no_existe_local.db"
prov_cartas = build_card_data()
check("build_card_data con BerryWallet -> CardDataBerry",
      isinstance(prov_cartas, CardDataBerry))
config.OPTCG_API_KEY, config.BERRYWALLET_API_KEY, config.LOCAL_DB_PATH = berry_ok_guardado

cdb = CardDataBerry(api_key="pk_test_falsa")
item_fake = {
    "card_number": "OP01-001", "name": "Roronoa Zoro (001)",
    "sub_type_name": "Normal", "rarity": "R", "card_type": "Character",
    "ext_color": "Green", "ext_cost": "4", "ext_power": "5000",
    "ext_subtypes": "Straw Hat Crew;East Blue",
    "cardmarket": {"prices": {"trend": 2.56, "avg": 2.64, "low": 0.8},
                   "product_url": "https://cardmarket.com/x"},
}
norm = cdb._normalizar(item_fake)
check("normalizar: id y nombre limpio", norm["id"] == "OP01-001" and norm["name"] == "Roronoa Zoro")
check("normalizar: campos ricos", norm.get("color") is None and norm["colors"] == ["Green"]
      and norm["power"] == 5000 and norm["cost"] == 4)
check("normalizar: categoría/rareza/set", norm["category"] == "Character"
      and norm["rarity"] == "R" and norm["sets"] == [{"id": "OP-01", "label": "OP-01"}])
check("normalizar: imagen oficial Bandai",
      norm["image_url"] == official_image_url("OP01-001"))
check("normalizar: precio trend", norm["price"] == 2.56)

class _FakeSession:
    def __init__(self, items): self._items = items; self.calls = []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        items = self._items
        class R:
            def raise_for_status(self): pass
            def json(self): return {"success": True, "data": items}
        return R()
cdb_fake = CardDataBerry(api_key="pk_test_falsa")
cdb_fake._session = _FakeSession([
    {"card_number": "OP01-001", "name": "Roronoa Zoro (Alternate Art)",
     "sub_type_name": "Parallel", "cardmarket": None},
    {"card_number": "OP01-001", "name": "Roronoa Zoro (001)",
     "sub_type_name": "Normal", "rarity": "R", "card_type": "Character",
     "ext_color": "Green", "ext_power": "5000",
     "cardmarket": {"prices": {"trend": 2.56}}},
])
res = cdb_fake.resolve_card("OP01-001")
check("resolve_card prefiere variante Normal con precios",
      res and res["name"] == "Roronoa Zoro" and res["power"] == 5000)
check("resolve_card usa código normalizado en la query",
      cdb_fake._session.calls and cdb_fake._session.calls[0].get("q") == "OP01-001")

# --- CardDataLocal: catálogo local SQLite (sin claves ni red) ---
import json as _json
import sqlite3
from carddata import CardDataLocal

db_local = os.path.join(tempfile.gettempdir(), "optcg_cards_test.db")
if os.path.exists(db_local):
    os.remove(db_local)
_conn = sqlite3.connect(db_local)
_conn.executescript("""
    CREATE TABLE cards (
        id TEXT PRIMARY KEY, base_id TEXT, parallel INTEGER DEFAULT 0,
        variant_type TEXT, name TEXT, set_id TEXT, pack_id TEXT, rarity TEXT,
        finish TEXT, category TEXT, image_url TEXT, colors TEXT, cost INTEGER,
        power INTEGER, counter INTEGER, attributes TEXT, types TEXT,
        effect TEXT, trigger TEXT);
    CREATE TABLE sets (id TEXT PRIMARY KEY, pack_id TEXT, label TEXT, count INTEGER);
""")
_conn.execute("INSERT INTO sets VALUES ('OP-01','550101','Romance Dawn [OP-01]',121)")
_conn.executemany(
    """INSERT INTO cards (id, parallel, name, set_id, rarity, category, colors,
                          cost, power, types, image_url)
       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
    [
        ("OP01-001", 0, "Roronoa Zoro", "OP-01", "Leader", "Leader",
         _json.dumps(["Red"]), 5, 5000, _json.dumps(["Supernovas", "Straw Hat Crew"]),
         "https://en.onepiece-cardgame.com/images/cardlist/card/OP01-001.png"),
        ("OP01-001_p1", 1, "Roronoa Zoro", "OP-01", "Leader", "Leader",
         _json.dumps(["Red"]), 5, 5000, None,
         "https://en.onepiece-cardgame.com/images/cardlist/card/OP01-001_p1.png"),
        ("OP01-002", 0, "Monkey.D.Luffy", "OP-01", "Rare", "Character",
         _json.dumps(["Red"]), 4, 6000, None,
         "https://en.onepiece-cardgame.com/images/cardlist/card/OP01-002.png"),
        ("EB01-001", 0, "Nami", "EB-01", "Common", "Character",
         _json.dumps(["Blue"]), 2, 3000, None,
         "https://en.onepiece-cardgame.com/images/cardlist/card/EB01-001.png"),
        ("OP02-001", 0, "Edward.Newgate", "OP-02", "Rare", "Character",
         _json.dumps(["Red"]), 5, 7000, _json.dumps(["Whitebeard Pirates"]),
         "https://en.onepiece-cardgame.com/images/cardlist/card/OP02-001.png"),
    ])
_conn.commit()
_conn.close()

cdl = CardDataLocal(db_path=db_local)
c1 = cdl.resolve_card("op1-1")
check("local resolve por código normalizado", c1 and c1["id"] == "OP01-001"
      and c1["name"] == "Roronoa Zoro" and c1["colors"] == ["Red"])
check("local: power/cost/category/rareza", c1["power"] == 5000 and c1["cost"] == 5
      and c1["category"] == "Leader" and c1["rarity"] == "Leader")
check("local: sets con label", c1["sets"] == [{"id": "OP-01", "label": "Romance Dawn [OP-01]",
                                               "pack_id": "550101"}])
check("local: price None (lo da BerryWallet)", c1["price"] is None)
c2 = cdl.resolve_card("Monkey D. Luffy")
check("local resolve por nombre (tolerante a puntos)", c2 and c2["id"] == "OP01-002")
lst = cdl.list_cards(set_id="OP-01", color="Red", rarity="Leader")
check("local listado con filtros combinados",
      len(lst) == 2 and lst[0]["id"] == "OP01-001" and lst[1]["id"] == "OP01-001_p1")
lst2 = cdl.list_cards(set_id="OP-01", sort="power", order="desc", page_size=10)
check("local listado orden por poder desc",
      lst2 and lst2[0]["power"] == 6000)
lst3 = cdl.list_cards(set_id="EB-01")
check("local listado otro set", len(lst3) == 1 and lst3[0]["id"] == "EB01-001")
lst4 = cdl.list_cards(name="nami")
check("local listado por nombre (sin set)", len(lst4) == 1 and lst4[0]["id"] == "EB01-001")

# --- tokenizado: search_cards ---
t1 = cdl.search_cards("op01")
check("token 'op01' -> coincidencias del set OP-01",
      len(t1) >= 3 and all(c["id"].startswith("OP01") for c in t1))
t2 = cdl.search_cards("luffy")
check("token 'luffy' -> Monkey.D.Luffy (tolerante a puntos)",
      any(c["id"] == "OP01-002" for c in t2))
t3 = cdl.search_cards("OP01-00")
check("token código parcial 'OP01-00'",
      any(c["id"] == "OP01-001" for c in t3) and any(c["id"] == "OP01-001_p1" for c in t3))
t4 = cdl.search_cards("")
check("token vacío -> []", t4 == [])

# --- filtro de tipo (arquetipos) ---
tt1 = cdl.list_cards(tipo="supernovas")
check("filtro tipo: 'supernovas' (case-insensitive)",
      len(tt1) == 1 and tt1[0]["id"] == "OP01-001")
tt2 = cdl.list_cards(tipo="whitebeard")
check("filtro tipo: 'whitebeard'",
      len(tt2) == 1 and tt2[0]["id"] == "OP02-001")
tt3 = cdl.list_cards(tipo="straw")
check("filtro tipo parcial: 'straw'",
      any(c["id"] == "OP01-001" for c in tt3))
tt4 = cdl.list_cards(set_id="OP-02", tipo="whitebeard", min_power=6000)
check("filtro tipo combinado con set y poder",
      len(tt4) == 1 and tt4[0]["id"] == "OP02-001")
cdl.close()
os.remove(db_local)

print()
if fallos:
    print(f"❌ {len(fallos)} fallo(s): {fallos}")
    sys.exit(1)
print("✅ TODAS LAS VERIFICACIONES OK")
