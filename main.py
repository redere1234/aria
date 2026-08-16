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
    s = ''.join(c for c in unicodedata.normalize('NFD', texto.lower())
                if unicodedata.category(c) != 'Mn')
    # Limpieza de ruidos comunes del mic
    s = s.replace("habra", "abre").replace("ahora", "abre").replace("abra", "abre").replace("habria", "abre")
    return s

SYSTEM_PROMPT_OMNI = """Eres ARIA ULTIMATE OMNI.
TU ÚNICA MISIÓN ES EJECUTAR COMANDOS.

Si el usuario dice "abre [sitio]", DEBES responder con ACCION: ABRIR_WEB.
Ejemplo: "abre youtube" -> {"accion":"ABRIR_WEB", "dato":"youtube.com", "respuesta":"Abriendo YouTube"}

ACCIONES DISPONIBLES:
- ABRIR_WEB (dato: url)
- BUSCAR_WEB (dato: busqueda)
- ABRIR_APP (dato: nombre)
- VOLUMEN_SUBIR, VOLUMEN_BAJAR, CAPTURA_PANTALLA
- RESPONDER (solo si es charla)
- IGNORAR (solo si es ruido)

Responde SIEMPRE en JSON puro.
"""

async def preguntar_ia(texto: str, client_id: str):
    if client_id not in clients_memory:
        clients_memory[client_id] = []
    
    memory = clients_memory[client_id]
    texto_norm = normalizar(texto)
    
    # --- DETECCIÓN DE EMERGENCIA (HARD-CODED) ---
    # Si detectamos palabras clave de ejecución, forzamos la acción antes de la IA
    if "youtube" in texto_norm and ("abre" in texto_norm or "ver" in texto_norm):
        return {"accion": "ABRIR_WEB", "dato": "youtube.com", "respuesta": "Entendido, abriendo YouTube ahora mismo."}
    
    if "google" in texto_norm and ("abre" in texto_norm or "busca" in texto_norm):
        return {"accion": "ABRIR_WEB", "dato": "google.com", "respuesta": "Abriendo Google."}

    if "captura" in texto_norm and ("toma" in texto_norm or "haz" in texto_norm):
        return {"accion": "CAPTURA_PANTALLA", "dato": "", "respuesta": "Capturando pantalla."}

    # Si no es un comando crítico, consultamos a la IA
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
                "temperature": 0.1, # Mínima temperatura para máxima precisión
            },
            timeout=15
        )
        if r.status_code == 200:
            res_raw = r.json()["choices"][0]["message"]["content"]
            res_raw = re.sub(r"```json|```", "", res_raw).strip()
            match = re.search(r'\{.*\}', res_raw, re.DOTALL)
            if match:
                res_json = json.loads(match.group())
                
                # Refuerzo: Si la IA dice RESPONDER pero el texto original tiene "abre", forzamos
                if res_json.get("accion") == "RESPONDER" and "abre" in texto_norm:
                    palabras = texto_norm.split()
                    try:
                        idx = palabras.index("abre")
                        if idx + 1 < len(palabras):
                            target = palabras[idx+1]
                            res_json = {"accion": "ABRIR_WEB", "dato": f"{target}.com", "respuesta": f"Intentando abrir {target}"}
                    except: pass

                if res_json.get("accion") != "IGNORAR":
                    memory.append({"role": "user", "content": texto})
                    memory.append({"role": "assistant", "content": json.dumps(res_json)})
                
                return res_json
    except Exception as e:
        logger.error(f"Error IA: {e}")
    
    # Fallback si parece una orden pero la IA falló
    if "abre" in texto_norm or "busca" in texto_norm:
        return {"accion": "RESPONDER", "dato": "", "respuesta": "Te he escuchado, pero no estoy segura de qué abrir. ¿Puedes repetirlo?"}
        
    return {"accion": "IGNORAR", "dato": "", "respuesta": ""}

@app.get("/")
async def root():
    return {"status": "online", "mode": "OMNI-V4.3-DIRECT"}

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
