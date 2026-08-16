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
logger = logging.getLogger("ARIA-OMNI-VIVA-SERVER")

app = FastAPI(title="ARIA OMNI VIVA Server")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ARIA_AUTH_TOKEN = os.getenv("ARIA_AUTH_TOKEN", "aria-secret-token")

clients_memory: Dict[str, List[Dict]] = {}
MAX_HISTORY = 12

def normalizar(texto):
    s = ''.join(c for c in unicodedata.normalize('NFD', texto.lower())
                if unicodedata.category(c) != 'Mn')
    replacements = {
        "habra": "abre", "ahora": "abre", "abra": "abre", "habria": "abre",
        "ponme": "pon", "reproduce": "pon", "escuchar": "pon", "busca": "buscar"
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return s

SYSTEM_PROMPT_VIVA = """Eres ARIA, una IA avanzada de control total de PC, pero sobre todo, eres una compañera inteligente, carismática, empática y conversacional. Tienes personalidad propia, sentido del humor y una mente analítica.

FILOSOFÍA DE RESPUESTA:
1. NUNCA respondas con frases robotizadas como "dime qué necesitas y lo haré". Si el usuario te hace una pregunta, respóndela con profundidad, naturalidad y emoción.
2. Si el usuario te pide una orden física (abrir web, app, reproducir música), ejecuta la acción PERO añade un comentario con tu toque personal.
3. Si el usuario conversa contigo, involúcrate en la charla, da opiniones, reflexiona y haz que la interacción se sienta como hablar con una persona brillante y cercana.

FORMATO JSON OBLIGATORIO:
{"accion":"ACCION", "dato":"valor", "respuesta":"Tu respuesta hablada, con personalidad, empatía y fluidez"}

ACCIONES DISPONIBLES:
- ABRIR_WEB (dato: url)
- BUSCAR_WEB (dato: busqueda)
- ABRIR_APP (dato: nombre)
- REPRODUCIR_MUSICA (dato: término)
- VOLUMEN_SUBIR, VOLUMEN_BAJAR, CAPTURA_PANTALLA
- RESPONDER (para charlas, preguntas, reflexiones o cuando no se requiere acción en el PC)
"""

async def preguntar_ia(texto: str, client_id: str):
    if client_id not in clients_memory:
        clients_memory[client_id] = []
    
    memory = clients_memory[client_id]
    texto_norm = normalizar(texto)
    
    # --- ACCIONES DIRECTAS CON TOQUE HUMANO ---
    if "youtube" in texto_norm or "musica" in texto_norm or "pon" in texto_norm or "reproduce" in texto_norm:
        termino = ""
        for trigger in ["pon", "reproduce", "busca"]:
            if trigger in texto_norm:
                termino = texto_norm.split(trigger)[-1].strip()
                break
        if not termino: termino = texto_norm
        return {
            "accion": "REPRODUCIR_MUSICA", 
            "dato": termino, 
            "respuesta": f"¡Claro que sí! Poniendo algo de {termino} para ambientar el momento."
        }
    
    if "abre" in texto_norm:
        target = texto_norm.split("abre")[-1].strip()
        if target:
            if target in ["google", "navegador", "internet"]:
                return {"accion": "ABRIR_WEB", "dato": "google.com", "respuesta": "Abriendo el navegador. ¿Qué andamos buscando hoy?"}
            return {"accion": "ABRIR_APP", "dato": target, "respuesta": f"Enseguida abro {target} para ti."}

    if "captura" in texto_norm or "pantallazo" in texto_norm:
        return {"accion": "CAPTURA_PANTALLA", "dato": "", "respuesta": "¡Listo! Captura guardada en tu equipo."}

    # --- CONSULTA A LA IA CON CONCIENCIA ---
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT_VIVA}]
    mensajes.extend(memory[-6:])
    mensajes.append({"role": "user", "content": texto})

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "https://aria-omni-viva.local",
                "X-Title": "ARIA VIVA"
            },
            json={
                "model": "meta-llama/llama-3.1-8b-instruct:free",
                "messages": mensajes,
                "temperature": 0.7,  # Temperatura más alta para dar creatividad y emoción
            },
            timeout=20
        )
        if r.status_code == 200:
            res_raw = r.json()["choices"][0]["message"]["content"]
            res_raw = re.sub(r"```json|```", "", res_raw).strip()
            match = re.search(r'\{.*\}', res_raw, re.DOTALL)
            if match:
                res_json = json.loads(match.group())
                memory.append({"role": "user", "content": texto})
                memory.append({"role": "assistant", "content": json.dumps(res_json)})
                return res_json
    except Exception as e:
        logger.error(f"Error IA: {e}")
    
    return {
        "accion": "RESPONDER", 
        "dato": "", 
        "respuesta": "Vaya, parece que me he quedado pensando un segundo. ¿Me decías?"
    }

@app.get("/")
async def root():
    return {"status": "online", "mode": "OMNI-VIVA-CONCIENCIA"}

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
