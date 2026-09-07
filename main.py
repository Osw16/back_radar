from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from pydantic import BaseModel, Field
from trendspy import Trends

# Importación de servicio de IA
from ai_service import explicar_tendencia, generar_brief

# =============================================================================
# 1. CONFIGURACIÓN Y CONSTANTES
# =============================================================================
load_dotenv()

CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS", "*"
    ).split(",")
    if origin.strip()
]

VENTANA_HORAS = 24
UMBRAL_FUERTE = 8.0
UMBRAL_MODERADO = 2.0
TTL_MINUTOS = 30
CACHE_FILE = "cache_tendencias.json"
HEADERS_TRENDS = {"referer": "https://www.google.com/"}

BLACKLIST_MEDIOS = {
    "mshale", "emergente", "el emergente", "meridiano", "líder", "noticias"
}

WHITELIST_TRENDS_TYPES = {
    "Baseball team", "Soccer club", "Baseball player", "Soccer team",
    "Person", "Sports league", "Athlete", "Football player", "Tournament",
}

STOPWORDS = {
    "para", "como", "sobre", "with", "from", "that", "this", "news",
    "says", "will", "after", "more", "have", "their", "they", "what",
    "been", "when", "than", "just", "were", "futbol", "fútbol", "basket",
    "básquet", "beisbol", "béisbol", "baloncesto", "espn", "marca", "diario",
    "resumen", "resultados", "destacadas", "jugadas", "noticias", "deportes",
    "video", "fotos", "vivo", "previa", "cronica", "crónica", "informe",
    "minuto", "los", "las", "el", "la", "un", "una", "y", "o", "a", "de", "en",
    "por", "del", "al", "con", "sus", "su", "fue", "es", "está",
    "lvbp", "mlb", "nba", "futve", "vinotinto", "champions", "league",
    "copa", "libertadores", "selección", "seleccion", "venezolana",
    "federación", "federacion", "liga", "grandes", "ligas", "venex",
    "august", "september", "open", "yvke", "mundial", "ciudad", "valencia",
    "jaime", "macías", "macias",
}


def gnews(q: str, gl: str = "VE") -> str:
    query = q if "when:" in q else f"{q} when:2d"
    return (
        f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}"
        f"&hl=es-419&gl={gl}&ceid={gl}:es-419"
    )


PILARES = {
    "⚾ LVBP & Béisbol": [
        gnews("LVBP", gl="VE"),
        gnews("Leones del Caracas", gl="VE"),
        gnews("Navegantes del Magallanes", gl="VE"),
        gnews("Tiburones de La Guaira", gl="VE"),
        gnews("Cardenales de Lara", gl="VE"),
        gnews("refuerzos LVBP", gl="VE"),
        "https://www.mlb.com/feeds/news/rss.xml",
        "https://www.espn.com/espn/rss/mlb/news",
        "https://www.mlbtraderumors.com/feed",
    ],
    "🇻🇪 Vinotinto & FUTVE": [
        gnews("La Vinotinto", gl="VE"),
        gnews("Liga FUTVE", gl="VE"),
        gnews("Selección Venezolana de Fútbol", gl="VE"),
        gnews("Federación Venezolana de Fútbol", gl="VE"),
        gnews("convocatoria Vinotinto", gl="VE"),
        gnews("fichajes FUTVE", gl="VE"),
    ],
    "⚽ Fútbol Internacional": [
        "https://feeds.bbci.co.uk/sport/football/rss.xml",
        "https://www.espn.com/espn/rss/soccer/news",
        "https://e00-marca.uecdn.es/rss/futbol.xml",
        "https://www.theguardian.com/football/rss",
        "https://www.fabrizioromano.com/feed",
        "https://www.skysports.com/rss/12040",
        gnews("fichajes", gl="ES"),
        gnews("Champions League", gl="ES"),
        gnews("Copa Libertadores", gl="US"),
    ],
    "🌎 Venex (Exterior)": [
        gnews("Salomón Rondón", gl="MX"),
        gnews("Yeferson Soteldo", gl="BR"),
        gnews("Ronald Acuña Jr", gl="US"),
        gnews("Luis Arráez", gl="US"),
        gnews("venezolanos MLB", gl="US"),
        gnews("venezolanos rumores fichaje", gl="US"),
    ],
}


# =============================================================================
# 2. MOTOR RSS & INGESTA
# =============================================================================
def etiqueta_score(score: float) -> str:
    if score >= UMBRAL_FUERTE:
        return "Fuerte"
    if score >= UMBRAL_MODERADO:
        return "Moderado"
    return "En seguimiento..."


def freshness(horas: float, etiqueta: str, titulo: str = "") -> dict:
    t = (titulo or "").lower()
    upcoming = any(
        w in t
        for w in (
            "previa", "se acerca", "convocatoria", "anuncia", "rumor",
            "refuerzo", "fichaje", "próxim", "proxim",
        )
    )
    if horas <= 3 and etiqueta in ("Fuerte", "Moderado"):
        badge, emoji = "AHORA", "🔥"
    elif upcoming:
        badge, emoji = "PRÓXIMAMENTE", "📅"
    elif etiqueta == "En seguimiento...":
        badge, emoji = "EVERGREEN", "♻️"
    else:
        badge, emoji = "ESTA SEMANA", "⏰"
    return {"badge": badge, "emoji": emoji}


def velocity_pct(score: float) -> int:
    return max(8, min(999, round(score * 12.5)))


def extraer_entidades(titulo: str) -> set:
    titulo_limpio = re.sub(r"[^\w\sáéíóúñÁÉÍÓÚÑ]", " ", titulo)
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", titulo_limpio)
    entidades, buffer = set(), []

    def validar_y_agregar(buf):
        if not buf:
            return None
        ent_str = " ".join(buf)
        if (
            buf[-1].lower() not in STOPWORDS
            and len(ent_str) >= 3
            and ent_str.lower() not in BLACKLIST_MEDIOS
        ):
            return ent_str
        return None

    for tok in tokens:
        if tok[0].isupper() and len(tok) >= 3 and tok.lower() not in STOPWORDS:
            buffer.append(tok)
        else:
            valido = validar_y_agregar(buffer)
            if valido:
                entidades.add(valido)
            buffer = []

    valido = validar_y_agregar(buffer)
    if valido:
        entidades.add(valido)
    return entidades


def tags_from_item(item: dict) -> list:
    tags, entidad = set(), (item.get("entidad") or "").lower()
    for titulo in item.get("titulos") or []:
        for e in extraer_entidades(titulo):
            if e.lower() != entidad:
                tags.add(e)
    return list(tags)[:4]


def parse_pubdate(item):
    node = item.find("pubDate")
    if node is None or not node.text:
        return None
    try:
        dt = parsedate_to_datetime(node.text.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def cargar_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if time.time() - data.get("timestamp", 0) < TTL_MINUTOS * 60:
                    resultados = data.get("resultados", {})
                    if all(isinstance(v, list) for v in resultados.values()):
                        return resultados
        except Exception:
            pass
    return {}


def guardar_cache(resultados: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {"timestamp": time.time(), "resultados": resultados},
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


def fetch_top_tendencias(urls: list, top_n: int = 5) -> list:
    ahora = datetime.now(timezone.utc)
    corte = ahora - timedelta(hours=VENTANA_HORAS)
    tendencias = defaultdict(
        lambda: {"menciones": 0, "fuentes": set(), "titulos": [], "ultima": None}
    )

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                root = ET.fromstring(r.read())
            dominio = urllib.parse.urlparse(url).netloc
            for item in root.findall(".//item"):
                pub = parse_pubdate(item)
                if pub is None or pub < corte:
                    continue
                t = item.find("title")
                if t is None or not t.text:
                    continue
                titulo = t.text.strip()
                for e in extraer_entidades(titulo):
                    d = tendencias[e]
                    d["menciones"] += 1
                    d["fuentes"].add(dominio)
                    if not d["titulos"]:
                        d["titulos"].append(titulo)
                    if d["ultima"] is None or pub > d["ultima"]:
                        d["ultima"] = pub
        except Exception:
            pass

    validas = {}
    for k, v in tendencias.items():
        if v["menciones"] < 2:
            continue
        horas = max((ahora - v["ultima"]).total_seconds() / 3600.0, 0.0)
        score = v["menciones"] / (horas + 1.0)
        validas[k] = {
            "menciones": v["menciones"],
            "titulos": v["titulos"],
            "score": round(score, 2),
            "horas": round(horas, 1),
            "etiqueta": etiqueta_score(score),
            "fuentes": list(v["fuentes"]),
        }

    claves_validas = list(validas.keys())
    for k1 in claves_validas:
        for k2 in claves_validas:
            if k1 != k2 and k1 in k2 and k1 in validas and k2 in validas:
                validas[k2]["menciones"] += validas[k1]["menciones"]
                validas[k2]["titulos"].extend(validas[k1]["titulos"])
                del validas[k1]

    ordenadas = sorted(validas.items(), key=lambda x: x[1]["score"], reverse=True)
    resultados = []
    for entidad, v in ordenadas[:top_n]:
        titulo_principal = (v.get("titulos") or [""])[0]
        item = {
            "entidad": entidad,
            **v,
            "freshness": freshness(v["horas"], v["etiqueta"], titulo_principal),
            "velocity_pct": velocity_pct(v["score"]),
            "tags": tags_from_item({"entidad": entidad, "titulos": v.get("titulos")}),
        }
        resultados.append(item)
    return resultados


def get_pilar_feed(pilar: str, top_n: int = 5) -> list:
    cache = cargar_cache()
    if pilar in cache and cache[pilar]:
        return cache[pilar]
    
    urls = PILARES.get(pilar, [])
    tendencias = fetch_top_tendencias(urls, top_n=top_n)
    if tendencias:
        cache = cargar_cache()
        cache[pilar] = tendencias
        guardar_cache(cache)
    return tendencias or []


# =============================================================================
# 3. MOTOR TRENDSPY (GOOGLE TRENDS)
# =============================================================================
def es_topic_valido(row) -> bool:
    tipo = str(row.get("topic_type", "")).strip()
    return tipo in WHITELIST_TRENDS_TYPES


def timeframe_from_days(days: int = 2) -> str:
    fin = date.today()
    inicio = fin - timedelta(days=max(1, int(days)))
    return f"{inicio} {fin}"


TRENDS_CACHE = {}
TRENDS_CACHE_TTL = 6 * 60 * 60  # 6h

def _trends_error_msg(exc: Exception) -> str:
    msg = str(exc).lower()
    if "quota" in msg or "rate" in msg or "429" in msg:
        return "Cuota de Google Trends temporalmente saturada. Prueba en unos minutos."
    if "timeout" in msg or "timed out" in msg:
        return "La consulta a Google Trends tardó demasiado. Intenta de nuevo."
    if "related_queries" in msg or "400" in msg:
        return "Búsquedas relacionadas no disponibles en esta versión de trendspy."
    return f"Error consultando Google Trends: {str(exc)[:140]}"


def _serialize_payload(data):
    if isinstance(data, pd.DataFrame):
        return data.to_dict(orient="records")
    if isinstance(data, dict):
        serialized = {}
        for k, v in data.items():
            if isinstance(v, pd.DataFrame):
                serialized[k] = v.to_dict(orient="records")
            else:
                serialized[k] = v
        return serialized
    return data


def fetch_trends(
    keyword: str,
    kind: str,
    geo: str = "VE",
    timeframe: str | None = None,
    filtrar_topics: bool = True,
) -> dict:
    timeframe = timeframe or timeframe_from_days(2)
    geo = (geo or "VE").strip().upper()
    cache_key = f"{keyword}:{kind}:{geo}:{timeframe}"
    
    # Check cache
    cached = TRENDS_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"] < TRENDS_CACHE_TTL):
        return cached["data"]
    
    tr = Trends()
    time.sleep(2.0)

    try:
        if kind == "topics":
            resultados = tr.related_topics(
                keyword, geo=geo, timeframe=timeframe, headers=HEADERS_TRENDS
            )
            if filtrar_topics and isinstance(resultados, dict):
                for clave, df in list(resultados.items()):
                    if isinstance(df, pd.DataFrame) and not df.empty and "topic_type" in df.columns:
                        resultados[clave] = (
                            df[df.apply(es_topic_valido, axis=1)].copy().reset_index(drop=True)
                        )
            data = _serialize_payload(resultados)

        elif kind == "queries":
            if not hasattr(tr, "related_queries"):
                raise RuntimeError("related_queries no disponible en esta versión de trendspy")
            resultados = tr.related_queries(
                keyword, geo=geo, timeframe=timeframe, headers=HEADERS_TRENDS
            )
            data = _serialize_payload(resultados)
        else:
            raise ValueError(f"Tipo de consulta inválido: {kind}. Debe ser 'topics' o 'queries'.")
        
        # Cache successful result
        TRENDS_CACHE[cache_key] = {"data": data, "ts": time.time()}
        return data
    except Exception as e:
        # If cached exists (stale), return it instead of failing
        if cached:
            return cached["data"]
        raise


# =============================================================================
# 4. APLICACIÓN FASTAPI & ROUTER (PURE REST API)
# =============================================================================
app = FastAPI(
    title="Radar de Tendencias API",
    version="2.0.0",
    description="API REST para detección de tendencias deportivas e inteligencia editorial",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Modelos Pydantic
class ExplainRequest(BaseModel):
    entidad: str = Field(..., description="Nombre de la entidad o deportista")
    titulo: str = Field(..., description="Titular de noticia asociado")


class BriefRequest(BaseModel):
    entidad: str
    titulo: str = ""
    pilar: str = "⚾ LVBP & Béisbol"
    score: float = 0.0
    menciones: int = 0
    etiqueta: str = "En seguimiento..."


# Root & Health Check
@app.get("/")
async def root():
    return {
        "name": "Radar de Tendencias API",
        "version": "2.0.0",
        "docs": "/docs",
        "status": "online",
    }


@app.get("/health", tags=["Salud"])
@app.get("/api/health", tags=["Salud"])
async def health():
    return {"status": "ok"}


# Endpoints Motor RSS
@app.get("/api/rss/pilares", tags=["Motor RSS"])
async def listar_pilares():
    return {"pilares": list(PILARES.keys())}


@app.get("/api/rss/feed", tags=["Motor RSS"])
async def obtener_feed_pilar(
    pilar: str = Query("⚾ LVBP & Béisbol", description="Nombre exacto del pilar"),
    top_n: int = Query(5, ge=1, le=20, description="Cantidad de tendencias"),
):
    if pilar not in PILARES:
        raise HTTPException(
            status_code=400,
            detail=f"Pilar '{pilar}' no válido. Opciones: {list(PILARES.keys())}",
        )
    items = get_pilar_feed(pilar, top_n=top_n)
    return {
        "pilar": pilar,
        "total": len(items),
        "items": items,
    }


@app.post("/api/rss/explain", tags=["Motor RSS - IA"])
async def explicar_entidad_tendencia(req: ExplainRequest):
    explicacion = explicar_tendencia(req.entidad, req.titulo)
    return {
        "entidad": req.entidad,
        "explicacion": explicacion,
    }


@app.post("/api/rss/brief", tags=["Motor RSS - IA"])
async def crear_brief_editorial(req: BriefRequest):
    brief = generar_brief(
        entidad=req.entidad,
        titulo=req.titulo,
        pilar=req.pilar,
        score=req.score,
        menciones=req.menciones,
        etiqueta=req.etiqueta,
    )
    return {
        "entidad": req.entidad,
        "brief": brief,
    }


# Endpoints Motor Trendspy
@app.get("/api/trends/entity", tags=["Motor Trendspy"])
async def consulta_tendencia_entidad(
    keyword: str = Query(..., description="Nombre de la entidad"),
    kind: str = Query("topics", regex="^(topics|queries)$", description="topics o queries"),
):
    try:
        data = fetch_trends(
            keyword=keyword,
            kind=kind,
            geo="VE",
            timeframe=timeframe_from_days(2),
            filtrar_topics=True,
        )
        return {
            "ok": True,
            "keyword": keyword,
            "kind": kind,
            "geo": "VE",
            "data": data,
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "keyword": keyword,
            "kind": kind,
            "geo": "VE",
            "data": None,
            "error": _trends_error_msg(e),
        }


@app.get("/api/trends/custom", tags=["Motor Trendspy - Custom Lab"])
async def sandbox_custom_trends(
    keyword: str = Query(..., description="Término a buscar"),
    kind: str = Query("topics", regex="^(topics|queries)$", description="topics o queries"),
    geo: str = Query("VE", max_length=8, description="Código ISO de país"),
    days: int = Query(2, ge=1, le=90, description="Días de la ventana"),
    filter_sports: bool = Query(True, description="Filtrar por whitelist deportiva"),
):
    tf = timeframe_from_days(days)
    try:
        data = fetch_trends(
            keyword=keyword,
            kind=kind,
            geo=geo,
            timeframe=tf,
            filtrar_topics=filter_sports and (kind == "topics"),
        )
        return {
            "ok": True,
            "keyword": keyword,
            "kind": kind,
            "geo": geo.upper(),
            "timeframe": tf,
            "filter_sports": filter_sports,
            "data": data,
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "keyword": keyword,
            "kind": kind,
            "geo": geo.upper(),
            "timeframe": tf,
            "filter_sports": filter_sports,
            "data": None,
            "error": _trends_error_msg(e),
        }