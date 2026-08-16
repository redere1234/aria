import asyncio
from main import preguntar_ia

async def run_tests():
    print("=== INICIANDO PRUEBAS DE LÓGICA ARIA OMNI X ===")
    
    test_cases = [
        "abre youtube",
        "habra youtube",
        "pon musica de bad bunny",
        "busca en google como hacer pizza",
        "abre la calculadora",
        "toma una captura de pantalla",
        "hola como estas",
        "la region y de crueldad" # Ruido
    ]
    
    client_id = "test_client"
    
    for case in test_cases:
        print(f"\n[TEST] Entrada: '{case}'")
        res = await preguntar_ia(case, client_id)
        print(f"[RESULTADO] Acción: {res.get('accion')}, Dato: {res.get('dato')}, Respuesta: {res.get('respuesta')}")

if __name__ == "__main__":
    asyncio.run(run_tests())
