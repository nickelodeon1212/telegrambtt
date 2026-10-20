import os
import sys
import random
import asyncio
import logging
import itertools
import httpx
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import RPCError
from openai import OpenAI
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn

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

# Inicializar cliente OpenAI y FastAPI
ai_client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI(title="Privadazo Multi-Userbot Engine")

# Estructuras globales para Multi-Userbots y Pausas Humanas
ACTIVE_CLIENTS = []       # Lista de dicts: {"client": TelegramClient, "me": User, "name": str}
BOT_POOL = None            # Generador rotativo
HUMAN_PAUSED_USERS = {}   # Registro de usuarios pausados por agente humano

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

async def consultar_ia(mensaje: str) -> str:
    """Procesa consultas privadas usando OpenAI y el prompt.md dinámico."""
    system_prompt = cargar_system_prompt()
    try:
        response = await asyncio.to_thread(
            ai_client.chat.completions.create,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": mensaje}
            ],
            temperature=0.3,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"❌ Error consultando la IA: {e}")
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
        if not sender or getattr(sender, "bot", False) or getattr(sender, "deleted", False):
            return

        sender_name = getattr(sender, "first_name", "Usuario")
        sender_id = event.sender_id
        texto = event.raw_text
        if not texto:
            return

        logger.info(f"📩 [{bot_name}] Mensaje de {sender_name} (ID: {sender_id}): '{texto}'")

        asyncio.create_task(enviar_a_chatwoot(str(sender_id), sender_name, texto, "incoming"))

        if HUMAN_PAUSED_USERS.get(sender_id, False):
            logger.info(f"🤫 IA omitida para {sender_id} (Modo Agente Humano activo).")
            return

        try:
            await asyncio.sleep(random.uniform(3.0, 5.5))
            respuesta_raw = await consultar_ia(texto)
            fragmentos = [f.strip() for f in respuesta_raw.split("||") if f.strip()]

            for index, fragmento in enumerate(fragmentos):
                if index > 0:
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                tiempo_tipeo = max(3.5, min(len(fragmento) * 0.09, 9.0))
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
                    bot_name = f"{me.first_name} (@{me.username or 'sin_username'})"
                    ACTIVE_CLIENTS.append({"client": cli, "me": me, "name": bot_name})
                    registrar_event_handler(cli, bot_name)
                    logger.info(f"✅ Userbot conectado: {bot_name} [{key}]")
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
    print("=" * 60 + "\n")

    # Mantener todas las conexiones activas
    await asyncio.gather(*(c["client"].run_until_disconnected() for c in ACTIVE_CLIENTS))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Servicio detenido.")
