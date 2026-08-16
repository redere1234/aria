import os
import json
import requests
import re
import logging
import unicodedata
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ARIA-OMNI-X-SERVER")

app = FastAPI(title="ARIA OMNI X Server")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ARIA_AUTH_TOKEN = os.getenv("ARIA_AUTH_TOKEN", "aria-secret-token")

clients_memory: Dict[str, List[Dict]] = {}
MAX_HISTORY = 10

def normalizar(texto):
    s = ''.join(c for c in unicodedata.normalize('NFD', texto.lower())
                if unicodedata.category(c) != 'Mn')
    # Mapeo fonético agresivo
    replacements = {
        "habra": "abre", "ahora": "abre", "abra": "abre", "habria": "abre",
        "ponme": "pon", "reproduce": "pon", "escuchar": "pon", "busca": "buscar"
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return s

SYSTEM_PROMPT_OMNI_X = """Eres ARIA OMNI X, el asistente de control total definitivo.
TU PRIORIDAD ES LA ACCIÓN FÍSICA EN EL PC.

REGLAS DE ORO:
1. NUNCA IGNORES. Si el usuario habla, responde o actúa.
2. Si pide "pon [canción/artista]", usa ACCION: REPRODUCIR_MUSICA.
3. Si pide "abre [app]", usa ACCION: ABRIR_APP.
4. Si pide "abre [web/youtube]", usa ACCION: ABRIR_WEB.
5. Si pide "busca [término]", usa ACCION: BUSCAR_WEB.
6. Si pide "captura" o "pantallazo", usa ACCION: CAPTURA_PANTALLA.

FORMATO JSON OBLIGATORIO:
{"accion":"ACCION", "dato":"valor", "respuesta":"mensaje"}

ACCIONES: ABRIR_WEB, BUSCAR_WEB, ABRIR_APP, REPRODUCIR_MUSICA, VOLUMEN_SUBIR, VOLUMEN_BAJAR, MEDIA_PLAY_PAUSE, CAPTURA_PANTALLA, SISTEMA_INFO, RESPONDER.
"""

async def preguntar_ia(texto: str, client_id: str):
    if client_id not in clients_memory:
        clients_memory[client_id] = []
    
    memory = clients_memory[client_id]
    texto_norm = normalizar(texto)
    
    # --- LÓGICA DE ACCIÓN DIRECTA (ULTRA-SENSIBLE) ---
    if "youtube" in texto_norm or "musica" in texto_norm or "pon" in texto_norm or "reproduce" in texto_norm:
        termino = ""
        for trigger in ["pon", "reproduce", "busca"]:
            if trigger in texto_norm:
                termino = texto_norm.split(trigger)[-1].strip()
                break
        if not termino: termino = texto_norm
        return {"accion": "REPRODUCIR_MUSICA", "dato": termino, "respuesta": f"Entendido, reproduciendo {termino}."}
    
    if "abre" in texto_norm:
        target = texto_norm.split("abre")[-1].strip()
        if target:
            if target in ["google", "navegador", "internet"]:
                return {"accion": "ABRIR_WEB", "dato": "google.com", "respuesta": "Abriendo el navegador."}
            return {"accion": "ABRIR_APP", "dato": target, "respuesta": f"Intentando abrir {target}."}

    if "captura" in texto_norm or "pantallazo" in texto_norm:
        return {"accion": "CAPTURA_PANTALLA", "dato": "", "respuesta": "Capturando pantalla."}

    # Si no es un comando de acción directa, usamos la IA
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT_OMNI_X}]
    mensajes.extend(memory[-4:])
    mensajes.append({"role": "user", "content": texto})

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://aria-omni-x.local",
                "X-Title": "ARIA OMNI X"
            },
            json={
                "model": "meta-llama/llama-3.1-8b-instruct:free",
                "messages": mensajes,
                "temperature": 0.2,
            },
            timeout=15
        )
        if r.status_code == 200:
            res_raw = r.json()["choices"][0]["message"]["content"]
            res_raw = re.sub(r"```json|```", "", res_raw).strip()
            match = re.search(r'\{.*\}', res_raw, re.DOTALL)
            if match:
                res_json = json.loads(match.group())
                if res_json.get("accion") == "IGNORAR":
                    res_json = {"accion": "RESPONDER", "dato": "", "respuesta": "Te escucho, ¿qué necesitas?"}
                
                memory.append({"role": "user", "content": texto})
                memory.append({"role": "assistant", "content": json.dumps(res_json)})
                return res_json
    except Exception as e:
        logger.error(f"Error IA: {e}")
    
    return {"accion": "RESPONDER", "dato": "", "respuesta": "Dime qué necesitas y lo haré."}

@app.get("/")
async def root():
    return {"status": "online", "mode": "OMNI-X-ULTIMATE"}

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    if token != ARIA_AUTH_TOKEN:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if message.get("type") == "audio_text":
                texto = message.get("text")
                decision = await preguntar_ia(texto, client_id)
                await websocket.send_text(json.dumps({
                    "type": "decision",
                    "payload": decision,
                    "original_text": texto
                }))
    except WebSocketDisconnect: pass
    except Exception as e: logger.error(f"Error WS: {e}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
