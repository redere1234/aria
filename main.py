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
logger = logging.getLogger("ARIA-SERVER")

app = FastAPI(title="ARIA Cloud Server")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ARIA_AUTH_TOKEN = os.getenv("ARIA_AUTH_TOKEN", "aria-secret-token")

clients_memory: Dict[str, List[Dict]] = {}
MAX_HISTORY = 10

SYSTEM_PROMPT = """Eres ARIA ULTIMATE, el asistente de voz definitivo.
Responde SIEMPRE en JSON: {"accion":"ACCION","dato":"valor","respuesta":"lo que dirás"}

GUÍA DE ACCIONES:
- ABRIR_WEB: Si pide YouTube, Facebook, o una web (dato: url o nombre).
- BUSCAR_WEB: Si pide buscar algo en internet (dato: términos de búsqueda).
- ABRIR_APP: Si pide abrir Spotify, Discord, Calculadora, etc (dato: nombre app).
- VOLUMEN_SUBIR / VOLUMEN_BAJAR / VOLUMEN_MUTE: Control de audio.
- MEDIA_PLAY_PAUSE: Pausar/reproducir música.
- CAPTURA_PANTALLA: Tomar foto de la pantalla.
- SISTEMA_INFO: Estado de CPU/RAM.
- SISTEMA: dato "bloquear" o "apagar".
- RESPONDER: Para charla normal o si no hay acción física.

IMPORTANTE: Si el usuario dice "abre youtube", usa ABRIR_WEB con dato "youtube.com".
Si dice "busca fotos de perros", usa BUSCAR_WEB con dato "fotos de perros".
"""

async def preguntar_ia(comando: str, client_id: str):
    if client_id not in clients_memory:
        clients_memory[client_id] = []
    
    memory = clients_memory[client_id]
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT}]
    mensajes.extend(memory)
    mensajes.append({"role": "user", "content": comando})

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://aria-cloud.local",
                "X-Title": "ARIA Cloud"
            },
            json={
                "model": "meta-llama/llama-3.1-8b-instruct:free",
                "messages": mensajes,
                "temperature": 0.4,
            },
            timeout=20
        )
        if r.status_code == 200:
            res_raw = r.json()["choices"][0]["message"]["content"]
            # Limpiar posibles bloques de código
            res_raw = re.sub(r"```json|```", "", res_raw).strip()
            # Intentar extraer JSON si hay texto extra
            match = re.search(r'\{.*\}', res_raw, re.DOTALL)
            if match:
                res_json = json.loads(match.group())
                
                memory.append({"role": "user", "content": comando})
                memory.append({"role": assistant_role, "content": match.group()})
                if len(memory) > MAX_HISTORY * 2: clients_memory[client_id] = memory[-MAX_HISTORY*2:]
                
                return res_json
    except Exception as e:
        logger.error(f"Error IA: {e}")
    
    return {"accion": "RESPONDER", "dato": "", "respuesta": "Lo siento, tuve un problema al procesar eso."}

assistant_role = "assistant"

@app.get("/")
async def root():
    return {"status": "online", "version": "3.2-total-control"}

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    if token != ARIA_AUTH_TOKEN:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    logger.info(f"Conexión: {client_id}")

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "command":
                comando = message.get("text")
                logger.info(f"Comando: {comando}")
                respuesta_ia = await preguntar_ia(comando, client_id)
                await websocket.send_text(json.dumps({"type": "execution", "payload": respuesta_ia}))
                
    except WebSocketDisconnect:
        logger.info(f"Desconectado: {client_id}")
    except Exception as e:
        logger.error(f"Error WS: {e}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
