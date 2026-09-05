# Radar de Tendencias — Backend API (FastAPI)

Servidor REST API asíncrono para detección de tendencias deportivas, análisis de Google Trends e Inteligencia Editorial con Gemini 3.1 Flash Lite.

## 🚀 Ejecución en Local

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

- API Docs (Swagger): `http://localhost:8000/docs`
- Healthcheck: `http://localhost:8000/api/health`

## ☁️ Deploy en Render

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
- **Environment Variables:** `GEMINI_API_KEY`, `CORS_ORIGINS`
