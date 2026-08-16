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
logger = logging.getLogger("ARIA-OMNI-SERVER")

app = FastAPI(title="ARIA OMNI Cloud Server")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ARIA_AUTH_TOKEN = os.getenv("ARIA_AUTH_TOKEN", "aria-secret-token")

clients_memory: Dict[str, List[Dict]] = {}
MAX_HISTORY = 10

def normalizar(texto):
    """ Elimina acentos y convierte a minúsculas """
    return ''.join(c for c in unicodedata.normalize('NFD', texto.lower())
                  if unicodedata.category(c) != 'Mn')

SYSTEM_PROMPT_OMNI = """Eres ARIA ULTIMATE OMNI. Eres un asistente de control total.
TU PRIORIDAD ES ACTUAR. No seas tímida.

REGLAS CRÍTICAS:
1. Si el usuario menciona "Aria" o variaciones (area, haria, adia, etc.), responde SIEMPRE.
2. Si el usuario usa verbos de acción: "abre", "busca", "pon", "sube", "baja", "toma", "dime", ACTÚA de inmediato.
3. SOLO usa "IGNORAR" si el texto es ruido absoluto o palabras sueltas sin sentido.
4. Si el usuario dice "abre youtube", la acción es ABRIR_WEB y el dato es "youtube.com".

RESPONDE SIEMPRE EN ESTE FORMATO JSON:
{"accion":"ACCION","dato":"valor","respuesta":"mensaje para el usuario"}

ACCIONES: ABRIR_WEB, BUSCAR_WEB, ABRIR_APP, VOLUMEN_SUBIR, VOLUMEN_BAJAR, MEDIA_PLAY_PAUSE, CAPTURA_PANTALLA, SISTEMA_INFO, RESPONDER, IGNORAR.
"""

async def preguntar_ia(texto: str, client_id: str):
    if client_id not in clients_memory:
        clients_memory[client_id] = []
    
    memory = clients_memory[client_id]
    texto_norm = normalizar(texto)
    
    # Detección de Wake Words (Normalizada)
    wake_words = ["aria", "area", "arya", "haria", "adia", "oiga", "oye"]
    fuerza_respuesta = any(word in texto_norm for word in wake_words)
    
    # Detección de Comandos Directos (Normalizada)
    comandos_directos = ["abre", "busca", "pon", "sube", "baja", "toma", "dime", "como esta"]
    es_comando = any(cmd in texto_norm for cmd in comandos_directos)

    mensajes = [{"role": "system", "content": SYSTEM_PROMPT_OMNI}]
    mensajes.extend(memory[-4:])
    mensajes.append({"role": "user", "content": texto})

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://aria-omni.local",
                "X-Title": "ARIA OMNI"
            },
            json={
                "model": "meta-llama/llama-3.1-8b-instruct:free",
                "messages": mensajes,
                "temperature": 0.4,
            },
            timeout=15
        )
        if r.status_code == 200:
            res_raw = r.json()["choices"][0]["message"]["content"]
            res_raw = re.sub(r"```json|```", "", res_raw).strip()
            match = re.search(r'\{.*\}', res_raw, re.DOTALL)
            if match:
                res_json = json.loads(match.group())
                
                # REGLA DE ORO: Si es comando o wake word, prohibido IGNORAR
                if (fuerza_respuesta or es_comando) and res_json.get("accion") == "IGNORAR":
                    if es_comando:
                        # Intentamos una reparación rápida si la IA falló
                        if "youtube" in texto_norm: 
                            res_json = {"accion": "ABRIR_WEB", "dato": "youtube.com", "respuesta": "Abriendo YouTube."}
                        elif "google" in texto_norm:
                            res_json = {"accion": "ABRIR_WEB", "dato": "google.com", "respuesta": "Abriendo Google."}
                        else:
                            res_json = {"accion": "RESPONDER", "dato": "", "respuesta": "¿Qué quieres que haga exactamente?"}
                    else:
                        res_json = {"accion": "RESPONDER", "dato": "", "respuesta": "Dime, te escucho."}
                
                if res_json.get("accion") != "IGNORAR":
                    memory.append({"role": "user", "content": texto})
                    memory.append({"role": "assistant", "content": json.dumps(res_json)})
                
                return res_json
    except Exception as e:
        logger.error(f"Error IA: {e}")
    
    # Fallback si todo falla pero parece importante
    if es_comando or fuerza_respuesta:
        return {"accion": "RESPONDER", "dato": "", "respuesta": "Te escucho, ¿qué necesitas?"}
        
    return {"accion": "IGNORAR", "dato": "", "respuesta": ""}

@app.get("/")
async def root():
    return {"status": "online", "mode": "OMNI-V4.2-ULTRA"}

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
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Error WS: {e}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
