import os
import json
import requests
import re
import logging
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

SYSTEM_PROMPT_OMNI = """Eres ARIA ULTIMATE OMNI.
Tu misión es controlar el PC del usuario y responder dudas.

REGLAS DE OMNIPRESENCIA:
1. Si el usuario dice tu nombre (Aria, Haria, Area, etc.) o da una orden directa (abre, busca, pon, dime), DEBES ACTUAR.
2. Si el usuario está hablando de cosas generales que no requieren acción, responde con "IGNORAR".
3. Si tienes dudas de si es para ti, responde amablemente preguntando si necesitas algo.

FORMATO JSON OBLIGATORIO:
{"accion":"ACCION","dato":"valor","respuesta":"lo que dirás","es_para_mi": true/false}

ACCIONES: ABRIR_WEB, BUSCAR_WEB, ABRIR_APP, VOLUMEN_SUBIR, VOLUMEN_BAJAR, MEDIA_PLAY_PAUSE, CAPTURA_PANTALLA, SISTEMA_INFO, RESPONDER, IGNORAR.
"""

async def preguntar_ia(texto: str, client_id: str):
    if client_id not in clients_memory:
        clients_memory[client_id] = []
    
    memory = clients_memory[client_id]
    
    # Detección rápida de Wake Word antes de llamar a la IA para ahorrar tiempo
    wake_words = ["aria", "area", "arya", "haria", "haría", "adia"]
    fuerza_respuesta = any(word in texto.lower() for word in wake_words)

    mensajes = [{"role": "system", "content": SYSTEM_PROMPT_OMNI}]
    mensajes.extend(memory[-4:]) # Contexto corto para velocidad
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
                "temperature": 0.5,
            },
            timeout=15
        )
        if r.status_code == 200:
            res_raw = r.json()["choices"][0]["message"]["content"]
            res_raw = re.sub(r"```json|```", "", res_raw).strip()
            match = re.search(r'\{.*\}', res_raw, re.DOTALL)
            if match:
                res_json = json.loads(match.group())
                
                # Si detectamos wake word pero la IA quería ignorar, forzamos respuesta
                if fuerza_respuesta and res_json.get("accion") == "IGNORAR":
                    res_json["accion"] = "RESPONDER"
                    res_json["respuesta"] = "¿Sí? Dime qué necesitas."
                
                if res_json.get("accion") != "IGNORAR":
                    memory.append({"role": "user", "content": texto})
                    memory.append({"role": "assistant", "content": json.dumps(res_json)})
                
                return res_json
    except Exception as e:
        logger.error(f"Error IA: {e}")
    
    return {"accion": "IGNORAR", "dato": "", "respuesta": ""}

@app.get("/")
async def root():
    return {"status": "online", "mode": "OMNI-V4.1"}

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
                
                # Siempre enviamos la decisión al cliente, incluso si es IGNORAR, 
                # para que el cliente pueda mostrar feedback visual de lo que pasó.
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
