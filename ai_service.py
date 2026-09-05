import os
import re
from google import genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"[Gemini Client Init Warning]: {e}")


def explicar_tendencia(entidad: str, titulo: str) -> str:
    """Explica en 1-2 oraciones por qué la entidad es tendencia (Gemini 3.1 Flash Lite)."""
    if not client:
        return f"{entidad} genera conversación en redes tras las noticias recientes sobre: '{titulo}'."
    
    prompt = (
        f"Explica de forma clara, directa y muy humana por qué '{entidad}' es tendencia hoy, "
        f"basándote en este titular: '{titulo}'. "
        f"Responde en español, máximo 2 oraciones."
    )
    try:
        resp = client.chats.create(model="gemini-3.1-flash-lite").send_message(prompt)
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("Respuesta vacía de Gemini")
        return text
    except Exception as e:
        print(f"[Gemini Explain Error]: {e}")
        return f"{entidad} destaca en las tendencias actuales por: '{titulo}'."


def _parse_brief_text(raw_text: str, entidad: str, pilar: str) -> dict:
    parsed = {
        "gancho": "",
        "angulo_1": "",
        "angulo_2": "",
        "angulo_3": "",
        "formato": "Reel 30s",
        "hashtags": [f"#{entidad.replace(' ', '')}", "#Deportes", "#VE"],
    }
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("gancho:"):
            parsed["gancho"] = line.split(":", 1)[1].strip()
        elif lower.startswith("ángulo 1:") or lower.startswith("angulo 1:"):
            parsed["angulo_1"] = line.split(":", 1)[1].strip()
        elif lower.startswith("ángulo 2:") or lower.startswith("angulo 2:"):
            parsed["angulo_2"] = line.split(":", 1)[1].strip()
        elif lower.startswith("ángulo 3:") or lower.startswith("angulo 3:"):
            parsed["angulo_3"] = line.split(":", 1)[1].strip()
        elif lower.startswith("formato:"):
            parsed["formato"] = line.split(":", 1)[1].strip()
        elif lower.startswith("hashtags:"):
            tags = re.findall(r"#\w+", line)
            if tags:
                parsed["hashtags"] = tags

    if not parsed["gancho"]:
        parsed["gancho"] = f"¿Por qué {entidad} está acaparando la atención hoy?"
    if not parsed["angulo_1"]:
        parsed["angulo_1"] = f"El momento clave de {entidad} en {pilar}."
    return parsed


def generar_brief(
    entidad: str,
    titulo: str,
    pilar: str,
    score: float = 0.0,
    menciones: int = 0,
    etiqueta: str = "",
) -> dict:
    """Genera brief editorial para redes sociales estructurado con fallback garantizado."""
    if not client:
        return {
            "gancho": f"{entidad} lidera el radar de contenidos.",
            "angulo_1": f"Clave de su momento actual en {pilar}.",
            "angulo_2": "Reacción de la fanaticada y análisis en redes.",
            "angulo_3": "¿Pico momentáneo o tendencia para toda la semana?",
            "formato": "Reel 30s",
            "hashtags": [f"#{entidad.replace(' ', '')}", "#Deportes", "#VE"],
        }

    prompt = (
        f"Eres un editor de contenidos deportivos para Venezuela.\n"
        f"Tendencia: '{entidad}' | Pilar: {pilar} | Score: {score:.1f} | "
        f"Menciones: {menciones} | Nivel: {etiqueta}\n"
        f"Titular: '{titulo}'\n\n"
        f"Brief en español, formato exacto:\n"
        f"GANCHO:\nÁNGULO 1:\nÁNGULO 2:\nÁNGULO 3:\nFORMATO:\nHASHTAGS:\n"
        f"Solo el brief, sin preámbulo."
    )
    try:
        resp = client.chats.create(model="gemini-3.1-flash-lite").send_message(prompt)
        text = (resp.text or "").strip()
        return _parse_brief_text(text, entidad, pilar)
    except Exception as e:
        print(f"[Gemini Brief Error]: {e}")
        return {
            "gancho": f"{entidad} se posiciona en el radar.",
            "angulo_1": f"Qué implica este momento para {pilar}.",
            "angulo_2": "Detalle y reacciones del reporte.",
            "angulo_3": "¿Pico de 24h o debate de la semana?",
            "formato": "Reel 30s",
            "hashtags": [f"#{entidad.replace(' ', '')}", "#Deportes", "#VE"],
        }
