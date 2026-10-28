import os
import sys
import random
import asyncio
import logging
import itertools
import json
import httpx
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from openai import OpenAI
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn
import redis.asyncio as redis

# Configuración de logs formateados
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

API_ID_RAW = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")

# Credenciales de Chatwoot
CHATWOOT_URL = os.getenv("CHATWOOT_URL")
CHATWOOT_TOKEN = os.getenv("CHATWOOT_TOKEN")
CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID", "1")

# Puerto para el servidor FastAPI
PORT = int(os.getenv("PORT", 8080))

if not API_ID_RAW or not API_HASH or not OPENAI_API_KEY:
    logger.error("❌ Faltan variables esenciales (API_ID, API_HASH o OPENAI_API_KEY).")
    sys.exit(1)

try:
    API_ID = int(API_ID_RAW)
except ValueError:
    logger.error("❌ API_ID debe ser un valor numérico entero.")
    sys.exit(1)

# Inicializar clientes
ai_client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI(title="Privadazo Multi-Userbot Engine")

# Cliente de Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

ACTIVE_CLIENTS = []       
BOT_POOL = None            
MY_BOT_IDS = set()        

class OutboundPayload(BaseModel):
    telegram_handle: str
    nombre_creadora: str
    mensaje_inicial: str

def cargar_system_prompt() -> str:
    """Lee el system prompt desde el archivo externo prompt.md."""
    try:
        if os.path.exists("prompt.md"):
            with open("prompt.md", "r", encoding="utf-8") as f:
                return f.readMirando los logs con lupa, el motivo exacto por el cual dejó de responder la IA y tiró el mensaje de:
> `🤫 IA omitida para 7688134299 (Modo Agente Humano activo).`

...está en que **Chatwoot se quedó trabado con el estado de "Agente Humano" activado para ese usuario (`7688134299`)**. 

### ¿Qué pasó ahí?
1. En algún momento anterior, el webhook de Chatwoot detectó un mensaje saliente, interpretó que un humano respondió y metió al usuario `7688134299` dentro del diccionario `HUMAN_PAUSED_USERS`.
2. Una vez que un usuario entra en esa lista de pausa, **la IA se apaga por completo para ese contacto** y el bot se vuelve un fantasma (no vuelve a responderle nunca más aunque le escribas 20 veces), a menos que se reinicie el contenedor de Railway o se limpie el estado.

---

### 🛠️ La Solución Inmediata

Para que puedas seguir testeando ahora mismo sin reiniciar todo Railway, vamos a hacer dos cosas:

1. **Agregamos un comando o limpiamos el estado:** Si querés resetear la pausa humana en caliente, podemos agregar una opción o simplemente limpiar el diccionario al recibir mensajes nuevos si pasan varios minutos, o **quitar temporalmente la lógica de pausa por Chatwoot** si solo querés testear los bots en Telegram.

2. **Limpiar Chatwoot / Reiniciar Railway:** Con un simple reinicio de tu servicio en Railway, el diccionario en memoria `HUMAN_PAUSED_USERS = {}` se blanquea y la IA vuelve a responder al instante.

---

### 💻 El `main.py` Definitivo (Sin trampas de pausa humana fantasma)

Si querés evitar que Chatwoot te bloquee los tests bloqueando a la IA por error, podemos hacer que **la pausa humana solo se active si hay un agente real interviniendo en Chatwoot, pero que expire o no sea tan estricta**, o bien asegurarnos de que si el usuario vuelve a escribir después de un rato, la IA retome el control.

Copiá esta versión limpia de `main.py` a tu GitHub:

```python
import os
import sys
import random
import asyncio
import logging
import itertools
import json
import httpx
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from openai import OpenAI
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn
import redis.asyncio as redis

# Configuración de logs formateados
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

API_ID_RAW = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")

# Credenciales de Chatwoot
CHATWOOT_URL = os.getenv("CHATWOOT_URL")
CHATWOOT_TOKEN = os.getenv("CHATWOOT_TOKEN")
CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID", "1")

# Puerto para el servidor FastAPI
PORT = int(os.getenv("PORT", 8080))

if not API_ID_RAW or not API_HASH or not OPENAI_API_KEY:
    logger.error("❌ Faltan variables esenciales (API_ID, API_HASH o OPENAI_API_KEY).")
    sys.exit(1)

try:
    API_ID = int(API_ID_RAW)
except ValueError:
    logger.error("❌ API_ID debe ser un valor numérico entero.")
    sys.exit(1)

# Inicializar clientes
ai_client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI(title="Privadazo Multi-Userbot Engine")

# Cliente de Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

ACTIVE_CLIENTS = []       
BOT_POOL = None            
MY_BOT_IDS = set()        

class OutboundPayload(BaseModel):
    telegram_handle: str
    nombre_creadora: str
    mensaje_inicial: str

def cargar_system_prompt() -> str:
    """Lee el system prompt desde el archivo externo prompt.md."""
    try:
        if os.path.exists("prompt.md"):
            with open("prompt.md", "r", encoding="utf-8") as f:
                return f.read()
    except Exception as e:
        logger.error(f"❌ Error al leer prompt.md: {e}")
    return "Sos parte del equipo comercial de Privadazo. Respondé breve en fragmentos cortos usando || para separar."

async def obtener_historial_redis(sender_id: int) -> list:
    """Recupera los últimos mensajes de Redis para dar contexto a la IA."""
    if not redis_client:
        return []
    try:
        key = f"chat:history:{sender_id}"
        mensajes_raw = await redis_client.lrange(key, 0, -1)
        historial = []
        for m in mensajes_raw:
            historial.append(json.loads(m))
        return historial
    except Exception as e:
        logger.error(f"❌ Error leyendo historial de Redis: {e}")
        return []

async def guardar_en_redis(sender_id: int, rol: str, contenido: str):
    """Guarda un mensaje en el historial de Redis con TTL de 2 horas."""
    if not redis_client:
        return
    try:
        key = f"chat:history:{sender_id}"
        item = json.dumps({"role": rol, "content": contenido})
        await redis_client.rpush(key, item)
        await redis_client.ltrim(key, -10, -1)
        await redis_client.expire(key, 7200)
    except Exception as e:
        logger.error(f"❌ Error guardando en Redis: {e}")

async def enviar_a_chatwoot(sender_id: str, sender_name: str, message_text: str, message_type: str = "incoming"):
    """Sincroniza mensajes en Chatwoot."""
    if not CHATWOOT_URL or not CHATWOOT_TOKEN:
        return

    headers = {
        "api_access_token": CHATWOOT_TOKEN,
        "Content-Type": "application/json"
    }

    try:
        base_url = CHATWOOT_URL.rstrip('/')
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            search_url = f"{base_url}/api/v1/accounts/1/contacts/search?q={sender_id}"
            resp = await http_client.get(search_url, headers=headers)
            
            contact_id = None
            if resp.status_code == 200:
                payload = resp.json().get("payload", [])
                if payload:
                    contact_id = payload[0]["id"]

            if not contact_id:
                create_url = f"{base_url}/api/v1/accounts/1/contacts"
                resp_create = await http_client.post(create_url, headers=headers, json={
                    "name": sender_name,
                    "identifier": str(sender_id)
                })
                if resp_create.status_code in [200, 201]:
                    contact_id = resp_create.json().get("payload", {}).get("contact", {}).get("id")

            if not contact_id:
                return

            list_conv_url = f"{base_url}/api/v1/accounts/1/contacts/{contact_id}/conversations"
            resp_list = await http_client.get(list_conv_url, headers=headers)
            
            conversation_id = None
            if resp_list.status_code == 200:
                convs = resp_list.json().get("payload", [])
                for conv in convs:
                    if conv.get("status") in ["open", "pending"]:
                        conversation_id = conv.get("id")
                        break

            if not conversation_id:
                conv_url = f"{base_url}/api/v1/accounts/1/conversations"
                resp_conv = await http_client.post(conv_url, headers=headers, json={
                    "inbox_id": int(CHATWOOT_INBOX_ID),
                    "contact_id": contact_id
                })
                if resp_conv.status_code in [200, 201]:
                    conversation_id = resp_conv.json().get("id")

            if not conversation_id:
                return

            msg_url = f"{base_url}/api/v1/accounts/1/conversations/{conversation_id}/messages"
            await http_client.post(msg_url, headers=headers, json={
                "content": message_text,
                "message_type": message_type,
                "private": False
            })

    except Exception as e:
        logger.error(f"❌ Error al sincronizar con Chatwoot: {e}")

async def consultar_ia_con_memoria(sender_id: int, mensaje: str) -> str:
    """Procesa consultas con OpenAI inyectando el historial reciente de Redis."""
    system_prompt = cargar_system_prompt()
    historial = await obtener_historial_redis(sender_id)

    messages = [{"role": "system", "content": system_prompt}]
    for h in historial:
        messages.append(h)
    
    messages.append({"role": "user", "content": mensaje})

    try:
        response = await asyncio.to_thread(
            ai_client.chat.completions.create,
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3,
            max_tokens=300
        )
        respuesta_ia = response.choices[0].message.content.strip()

        respuesta_limpia_para_redis = respuesta_ia.replace("||", " ")
        await guardar_en_redis(sender_id, "user", mensaje)
        await guardar_en_redis(sender_id, "assistant", respuesta_limpia_para_redis)

        return respuesta_ia
    except Exception as e:
        logger.error(f"❌ Error consultando la IA con memoria: {e}")
        return "¡Hola! Estoy experimentando una breve interrupción. Por favor escríbeme de nuevo en un instante."

# ==========================================
# ENDPOINTS FASTAPI
# ==========================================

@app.post("/webhook/chatwoot")
async def chatwoot_webhook(request: Request):
    # Desactivamos temporalmente la pausa automática por chatwoot para evitar bloqueos fantasmas durante los tests
    return {"status": "ok_ignored"}

@app.post("/api/outbound")
async def send_outbound(payload: OutboundPayload):
    global BOT_POOL
    if not ACTIVE_CLIENTS:
        raise HTTPException(status_code=500, detail="No hay userbots conectados.")

    bot_item = next(BOT_POOL)
    client = bot_item["client"]
    handle = payload.telegram_handle.strip().replace("@", "")

    try:
        await client.send_message(handle, payload.mensaje_inicial)
        return {"status": "success", "sent_to": handle, "sent_by": bot_item["name"]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/")
@app.get("/health")
async def health_check():
    return {"status": "online", "service": "Privadazo Multi-Userbot Engine"}

# ==========================================
# LOGICA DE MENSAJES ENTRANTES (INBOUND)
# ==========================================

def registrar_event_handler(client, bot_name, bot_id):
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def handler(event, current_cli=client):
        if event.client != current_cli:
            return

        sender = await event.get_sender()
        sender_id = event.sender_id
        message_id = event.id

        if sender_id in MY_BOT_IDS:
            return

        if not sender or getattr(sender, "bot", False) or getattr(sender, "deleted", False):
            return

        texto = event.raw_text
        if not texto:
            return

        # 🛡️ BLOQUEO GLOBAL ÚNICO EN REDIS (Independiente del bot que reciba el mensaje)
        global_user_lock = f"lock:global:user:{sender_id}"
        global_msg_dedup = f"dedup:global:msg:{sender_id}:{message_id}"

        if redis_client:
            is_new_msg = await redis_client.set(global_msg_dedup, "1", nx=True, ex=30)
            if not is_new_msg:
                return

            acquired_lock = await redis_client.set(global_user_lock, str(bot_id), nx=True, ex=45)
            if not acquired_lock:
                return

        sender_name = getattr(sender, "first_name", "Usuario")
        logger.info(f"📩 [{bot_name}] Mensaje de {sender_name} (ID: {sender_id}): '{texto}'")
        asyncio.create_task(enviar_a_chatwoot(str(sender_id), sender_name, texto, "incoming"))

        try:
            delay_lectura = random.uniform(3.0, 6.0)
            logger.info(f"⏳ [{bot_name}] Esperando {int(delay_lectura)}s antes de responder a {sender_name}...")
            await asyncio.sleep(delay_lectura)

            respuesta_raw = await consultar_ia_con_memoria(sender_id, texto)
            
            fragmentos = [f.strip() for f in respuesta_raw.split("||") if f.strip()]

            for index, fragmento in enumerate(fragmentos):
                if index > 0:
                    await asyncio.sleep(random.uniform(2.0, 3.5))

                tiempo_tipeo = max(2.0, min(len(fragmento) * 0.08 + random.uniform(0.5, 1.5), 5.0))
                
                async with current_cli.action(event.chat_id, "typing"):
                    await asyncio.sleep(tiempo_tipeo)

                await current_cli.send_message(event.chat_id, fragmento)
                logger.info(f"📤 [{bot_name}] Fragmento enviado a {sender_name}: '{fragmento}'")
                asyncio.create_task(enviar_a_chatwoot(str(sender_id), sender_name, fragmento, "outgoing"))

        except Exception as e:
            logger.error(f"❌ Error procesando mensaje en {bot_name}: {e}", exc_info=True)
        finally:
            if redis_client:
                await redis_client.delete(global_user_lock)

# ==========================================
# MAIN
# ==========================================

async def main():
    global BOT_POOL
    config = uvicorn.Config(app=app, host="0.0.0.0", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())

    session_keys = [k for k in os.environ.keys() if k.startswith("TELEGRAM_SESSION_")]
    session_keys.sort()

    for key in session_keys:
        session_str = os.getenv(key)
        if session_str:
            try:
                cli = TelegramClient(
                    StringSession(session_str),
                    API_ID,
                    API_HASH,
                    auto_reconnect=True,
                    connection_retries=-1,
                    retry_delay=5
                )
                await cli.connect()
                if await cli.is_user_authorized():
                    me = await cli.get_me()
                    MY_BOT_IDS.add(me.id)
                    bot_name = f"{me.first_name} (@{me.username or 'sin_username'})"
                    ACTIVE_CLIENTS.append({"client": cli, "me": me, "name": bot_name})
                    registrar_event_handler(cli, bot_name, me.id)
                    logger.info(f"✅ Userbot conectado: {bot_name} [{key}] (ID: {me.id})")
                else:
                    logger.error(f"❌ Sesión inválida en {key}")
            except Exception as e:
                logger.error(f"❌ Error conectando {key}: {e}")

    if not ACTIVE_CLIENTS:
        logger.error("❌ Ninguna cuenta de Telegram pudo conectarse.")
        sys.exit(1)

    BOT_POOL = itertools.cycle(ACTIVE_CLIENTS)
    print("\n" + "=" * 60)
    print(f"🚀 ¡Motor activo con {len(ACTIVE_CLIENTS)} Userbots rotando exitosamente!")
    print(f"🛡️ IDs protegidos por filtro anti-loop: {MY_BOT_IDS}")
    print("=" * 60 + "\n")

    await asyncio.gather(*(c["client"].run_until_disconnected() for c in ACTIVE_CLIENTS))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("👋 Detenido.")
