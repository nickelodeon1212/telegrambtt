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
from telethon.errors import RPCError
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

# Cliente de Redis para la memoria de contexto cercano
redis_client = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

# Estructuras globales para Multi-Userbots, Pausas Humanas y Filtro Anti-Loop
ACTIVE_CLIENTS = []       # Lista de dicts: {"client": TelegramClient, "me": User, "name": str}
BOT_POOL = None            # Generador rotativo
HUMAN_PAUSED_USERS = {}    # Registro de usuarios pausados por agente humano
MY_BOT_IDS = set()        # 🛡️ Set dinámico para almacenar los IDs de Telegram de nuestras cuentas

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
    return "Sos parte del equipo comercial de Privadazo. Respondé breve y amigable."

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
    """Guarda un mensaje en el historial de Redis (ventana deslizante de 10 mensajes) y renueva TTL de 2 horas."""
    if not redis_client:
        return
    try:
        key = f"chat:history:{sender_id}"
        item = json.dumps({"role": rol, "content": contenido})
        await redis_client.rpush(key, item)
        # Mantener solo los últimos 10 mensajes (5 pares usuario/asistente)
        await redis_client.ltrim(key, -10, -1)
        # Expirar el historial si pasan 2 horas sin actividad
        await redis_client.expire(key, 7200)
    except Exception as e:
        logger.error(f"❌ Error guardando en Redis: {e}")

async def enviar_a_chatwoot(sender_id: str, sender_name: str, message_text: str, message_type: str = "incoming"):
    """Sincroniza mensajes agrupando todo en un único hilo de conversación activa por contacto."""
    if not CHATWOOT_URL or not CHATWOOT_TOKEN:
        return

    headers = {
        "api_access_token": CHATWOOT_TOKEN,
        "Content-Type": "application/json"
    }

    try:
        base_url = CHATWOOT_URL.rstrip('/')
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            # 1. Buscar o crear el contacto
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

            # 2. Buscar conversación abierta
            list_conv_url = f"{base_url}/api/v1/accounts/1/contacts/{contact_id}/conversations"
            resp_list = await http_client.get(list_conv_url, headers=headers)
            
            conversation_id = None
            if resp_list.status_code == 200:
                convs = resp_list.json().get("payload", [])
                for conv in convs:
                    if conv.get("status") in ["open", "pending"]:
                        conversation_id = conv.get("id")
                        break

            # 3. Crear conversación si no existe
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

            # 4. Enviar el mensaje
            msg_url = f"{base_url}/api/v1/accounts/1/conversations/{conversation_id}/messages"
            await http_client.post(msg_url, headers=headers, json={
                "content": message_text,
                "message_type": message_type,
                "private": False
            })

            logger.info(f"✅ Sincronizado en Chatwoot (Conversación ID {conversation_id})")

    except Exception as e:
        logger.error(f"❌ Error al sincronizar con Chatwoot: {e}")

async def consultar_ia_con_memoria(sender_id: int, mensaje: str) -> str:
    """Procesa consultas con OpenAI inyectando el historial reciente de Redis."""
    system_prompt = cargar_system_prompt()
    
    # 1. Obtener historial previo desde Redis
    historial = await obtener_historial_redis(sender_id)

    # 2. Armar el array de mensajes para OpenAI (System + Historial + Mensaje nuevo)
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

        # 3. Guardar la nueva interacción en Redis de forma asíncrona
        await asyncio.gather(
            guardar_en_redis(sender_id, "user", mensaje),
            guardar_en_redis(sender_id, "assistant", respuesta_ia)
        )

        return respuesta_ia
    except Exception as e:
        logger.error(f"❌ Error consultando la IA con memoria: {e}")
        return "¡Hola! Estoy experimentando una breve interrupción. Por favor escríbeme de nuevo en un instante."

# ==========================================
# ENDPOINTS FASTAPI (WEBHOOKS & OUTBOUND)
# ==========================================

@app.post("/webhook/chatwoot")
async def chatwoot_webhook(request: Request):
    """Escucha eventos de Chatwoot. Si un agente humano escribe, manda el mensaje desde el bot correspondiente."""
    try:
        data = await request.json()
        event_type = data.get("event")

        if event_type == "message_created":
            message_type = data.get("message_type")
            is_private = data.get("private", False)

            if message_type == "outgoing" and not is_private:
                sender_type = data.get("sender", {}).get("type")
                
                if sender_type == "user":  # Agente humano
                    content = data.get("content", "")
                    meta = data.get("conversation", {}).get("meta", {})
                    sender_identifier = meta.get("sender", {}).get("identifier")

                    if sender_identifier and content:
                        telegram_user_id = int(sender_identifier)
                        
                        HUMAN_PAUSED_USERS[telegram_user_id] = True
                        logger.info(f"⏸️ IA pausada para el usuario {telegram_user_id} por intervención humana.")

                        # Enviar mensaje usando el primer client activo
                        if ACTIVE_CLIENTS:
                            client = ACTIVE_CLIENTS[0]["client"]
                            await client.send_message(telegram_user_id, content)
                            logger.info(f"💬 Mensaje manual enviado a Telegram ID {telegram_user_id}: '{content}'")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"❌ Error procesando Webhook de Chatwoot: {e}")
        return {"status": "error", "detail": str(e)}

@app.post("/api/outbound")
async def send_outbound(payload: OutboundPayload):
    """Endpoint llamado por n8n para enviar el primer mensaje a un nuevo lead rotando cuentas."""
    global BOT_POOL
    if not ACTIVE_CLIENTS:
        raise HTTPException(status_code=500, detail="No hay userbots conectados en Railway.")

    bot_item = next(BOT_POOL)
    client = bot_item["client"]
    handle = payload.telegram_handle.strip().replace("@", "")

    try:
        await client.send_message(handle, payload.mensaje_inicial)
        logger.info(f"🚀 Outbound enviado a @{handle} con el bot {bot_item['name']}")
        return {
            "status": "success",
            "sent_to": handle,
            "sent_by": bot_item["name"]
        }
    except Exception as e:
        logger.error(f"❌ Error enviando outbound a {handle}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "online",
        "service": "Privadazo Multi-Userbot Engine",
        "active_bots_count": len(ACTIVE_CLIENTS),
        "bots": [b["name"] for b in ACTIVE_CLIENTS]
    }

# ==========================================
# LOGICA DE MENSAJES ENTRANTES (INBOUND)
# ==========================================

def registrar_event_handler(client, bot_name):
    """Registra el manejador de mensajes para cada cuenta de Telegram conectada."""
    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def handler(event):
        sender = await event.get_sender()
        sender_id = event.sender_id

        # 🛡️ FILTRO ANTI-LOOP: Ignorar mensajes provenientes de nuestras propias cuentas
        if sender_id in MY_BOT_IDS:
            logger.info(f"🚫 [Anti-Loop] Mensaje ignorado: proviene de una de nuestras cuentas (ID: {sender_id}).")
            return

        if not sender or getattr(sender, "bot", False) or getattr(sender, "deleted", False):
            return

        sender_name = getattr(sender, "first_name", "Usuario")
        texto = event.raw_text
        if not texto:
            return

        logger.info(f"📩 [{bot_name}] Mensaje de {sender_name} (ID: {sender_id}): '{texto}'")

        asyncio.create_task(enviar_a_chatwoot(str(sender_id), sender_name, texto, "incoming"))

        # 🛡️ PAUSA HUMANA ACTIVADA PARA PRODUCCIÓN:
        if HUMAN_PAUSED_USERS.get(sender_id, False):
            logger.info(f"🤫 IA omitida para {sender_id} (Modo Agente Humano activo).")
            return

        try:
            # ⏱️ DELAY HUMANO REALISTA: Espera aleatoria entre 1 y 5 minutos (60 a 300 segundos)
            delay_lectura = random.uniform(60.0, 300.0)
            logger.info(f"⏳ [{bot_name}] Esperando {int(delay_lectura)}s antes de responder a {sender_name}...")
            await asyncio.sleep(delay_lectura)

            # Consulta a OpenAI integrando la memoria de contexto cercano de Redis
            respuesta_raw = await consultar_ia_con_memoria(sender_id, texto)
            fragmentos = [f.strip() for f in respuesta_raw.split("||") if f.strip()]

            for index, fragmento in enumerate(fragmentos):
                if index > 0:
                    # Pausa de reflexión entre fragmentos
                    await asyncio.sleep(random.uniform(4.0, 10.0))

                # ⌨️ SIMULACIÓN DE TIPEO: Se muestra "escribiendo..." durante 5 a 14 segundos
                tiempo_tipeo = max(5.0, min(len(fragmento) * 0.12 + random.uniform(1.0, 3.0), 14.0))
                
                async with client.action(event.chat_id, "typing"):
                    await asyncio.sleep(tiempo_tipeo)

                await client.send_message(event.chat_id, fragmento)
                logger.info(f"📤 [{bot_name}] Respuesta enviada a {sender_name}: '{fragmento}'")
                asyncio.create_task(enviar_a_chatwoot(str(sender_id), sender_name, fragmento, "outgoing"))

        except Exception as e:
            logger.error(f"❌ Error procesando mensaje de {sender_id} en {bot_name}: {e}", exc_info=True)

# ==========================================
# INICIO Y CONEXIÓN MULTI-USERBOTS
# ==========================================

async def main():
    global BOT_POOL
    print("=" * 60)
    print("🤖 Iniciando Motor Multi-Userbot + Webhook Server...")
    print("=" * 60)

    # Iniciar servidor Uvicorn en segundo plano
    config = uvicorn.Config(app=app, host="0.0.0.0", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())

    # Detectar todas las variables TELEGRAM_SESSION_1, TELEGRAM_SESSION_2, etc.
    session_keys = [k for k in os.environ.keys() if k.startswith("TELEGRAM_SESSION_")]
    session_keys.sort()

    if not session_keys:
        logger.error("❌ No se encontraron variables TELEGRAM_SESSION_X en Railway.")
        sys.exit(1)

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
                    
                    # 🛡️ Registrar el ID en la lista anti-loop
                    MY_BOT_IDS.add(me.id)
                    
                    bot_name = f"{me.first_name} (@{me.username or 'sin_username'})"
                    ACTIVE_CLIENTS.append({"client": cli, "me": me, "name": bot_name})
                    registrar_event_handler(cli, bot_name)
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

    # Mantener todas las conexiones activas
    await asyncio.gather(*(c["client"].run_until_disconnected() for c in ACTIVE_CLIENTS))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Servicio detenido.")
