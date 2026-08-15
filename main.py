import os
import json
import requests
import re
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

# Configuración de Logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ARIA-SERVER")

app = FastAPI(title="ARIA Cloud Server")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ARIA_AUTH_TOKEN = os.getenv("ARIA_AUTH_TOKEN", "aria-secret-token")

clients_memory: Dict[str, List[Dict]] = {}
MAX_HISTORY = 10

SYSTEM_PROMPT = """Eres ARIA ULTIMATE, el asistente de voz definitivo.
Responde SIEMPRE en JSON: {"accion":"ACCION","dato":"valor","respuesta":"lo que dirás"}

ACCIONES:
- ABRIR_WEB, BUSCAR_WEB, ABRIR_APP, ABRIR_JUEGO
- VOLUMEN_SUBIR, VOLUMEN_BAJAR, VOLUMEN_MUTE
- MEDIA_PLAY_PAUSE, MEDIA_SIGUIENTE, MEDIA_ANTERIOR
- VENTANA_MINIMIZAR, VENTANA_MAXIMIZAR, VENTANA_CERRAR, VENTANA_CAMBIAR
- SISTEMA_INFO, CAPTURA_PANTALLA, SISTEMA (apagar, bloquear), RESPONDER
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
                "temperature": 0.5,
            },
            timeout=20
        )
        if r.status_code == 200:
            res_raw = r.json()["choices"][0]["message"]["content"]
            res_raw = re.sub(r"```json|```", "", res_raw).strip()
            res_json = json.loads(res_raw)
            
            memory.append({"role": "user", "content": comando})
            memory.append({"role": "assistant", "content": res_raw})
            if len(memory) > MAX_HISTORY * 2:
                clients_memory[client_id] = memory[-MAX_HISTORY*2:]
            
            return res_json
    except Exception as e:
        logger.error(f"Error IA: {e}")
    return {"accion": "RESPONDER", "dato": "", "respuesta": "Error en el cerebro cloud."}

@app.get("/")
async def root():
    return {"status": "online", "version": "2.0-ultimate"}

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    if token != ARIA_AUTH_TOKEN:
        logger.warning("Intento de conexión con token inválido")
        await websocket.close(code=1008)
        return

    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    logger.info(f"Conexión establecida: {client_id}")

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "command":
                comando = message.get("text")
                logger.info(f"Comando de {client_id}: {comando}")
                
                respuesta_ia = await preguntar_ia(comando, client_id)
                
                await websocket.send_text(json.dumps({
                    "type": "execution",
                    "payload": respuesta_ia
                }))
                
    except WebSocketDisconnect:
        logger.info(f"Desconectado: {client_id}")
    except Exception as e:
        logger.error(f"Error en WS: {e}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
