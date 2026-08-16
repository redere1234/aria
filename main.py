import os
import json
import requests
import re
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ARIA-OMNI-SERVER")

app = FastAPI(title="ARIA OMNI Cloud Server")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ARIA_AUTH_TOKEN = os.getenv("ARIA_AUTH_TOKEN", "aria-secret-token")

clients_memory: Dict[str, List[Dict]] = {}
MAX_HISTORY = 15

# SYSTEM PROMPT AVANZADO PARA DETECCIÓN DE INTENCIÓN
SYSTEM_PROMPT_OMNI = """Eres ARIA ULTIMATE OMNI, una IA integrada en el PC del usuario.
Tu objetivo es detectar si el usuario te está hablando a ti o si está haciendo un comentario general.

REGLAS DE OMNIPRESENCIA:
1. Analiza el texto recibido. Si el usuario da una orden clara (ej. "abre...", "busca...", "pon...", "dime...") o hace una pregunta, ACTÚA.
2. Si el texto parece ruido de fondo o una conversación con otra persona que no requiere tu intervención, responde con la acción "IGNORAR".
3. Responde SIEMPRE en JSON: {"accion":"ACCION","dato":"valor","respuesta":"lo que dirás","confianza":0.0-1.0}

ACCIONES DISPONIBLES:
- ABRIR_WEB, BUSCAR_WEB, ABRIR_APP, ABRIR_JUEGO
- VOLUMEN_SUBIR, VOLUMEN_BAJAR, VOLUMEN_MUTE
- MEDIA_PLAY_PAUSE, MEDIA_SIGUIENTE, MEDIA_ANTERIOR
- VENTANA_MINIMIZAR, VENTANA_MAXIMIZAR, VENTANA_CERRAR, VENTANA_CAMBIAR
- SISTEMA_INFO, CAPTURA_PANTALLA, SISTEMA (bloquear, apagar)
- RESPONDER (para charla o dudas)
- IGNORAR (si no es para ti)
- CONFIRMAR (para acciones críticas como apagar)

Si detectas una orden pero no estás 100% seguro, usa la acción "RESPONDER" preguntando si quieres que ejecutes dicha acción.
"""

async def clasificar_intencion(texto: str, client_id: str):
    if client_id not in clients_memory:
        clients_memory[client_id] = []
    
    memory = clients_memory[client_id]
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT_OMNI}]
    # Añadimos contexto reciente para entender si la conversación sigue
    mensajes.extend(memory[-6:]) 
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
                "temperature": 0.3, # Baja temperatura para mayor precisión en comandos
            },
            timeout=15
        )
        if r.status_code == 200:
            res_raw = r.json()["choices"][0]["message"]["content"]
            res_raw = re.sub(r"```json|```", "", res_raw).strip()
            match = re.search(r'\{.*\}', res_raw, re.DOTALL)
            if match:
                res_json = json.loads(match.group())
                
                # Solo guardamos en memoria si no fue ignorado
                if res_json.get("accion") != "IGNORAR":
                    memory.append({"role": "user", "content": texto})
                    memory.append({"role": "assistant", "content": match.group()})
                    if len(memory) > MAX_HISTORY * 2: clients_memory[client_id] = memory[-MAX_HISTORY*2:]
                
                return res_json
    except Exception as e:
        logger.error(f"Error OMNI Engine: {e}")
    
    return {"accion": "IGNORAR", "dato": "", "respuesta": ""}

@app.get("/")
async def root():
    return {"status": "online", "mode": "OMNI-INTENT", "version": "4.0"}

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    if token != ARIA_AUTH_TOKEN:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    logger.info(f"Sesión OMNI Iniciada: {client_id}")

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "audio_text":
                texto = message.get("text")
                logger.info(f"Analizando: {texto}")
                
                # El motor decide si actuar o ignorar
                decision = await clasificar_intencion(texto, client_id)
                
                if decision.get("accion") != "IGNORAR":
                    await websocket.send_text(json.dumps({
                        "type": "execution",
                        "payload": decision
                    }))
                
    except WebSocketDisconnect:
        logger.info(f"Sesión Finalizada: {client_id}")
    except Exception as e:
        logger.error(f"Error OMNI WS: {e}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
