import os
import sys
import asyncio
import logging
import httpx
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import RPCError
from openai import OpenAI
from fastapi import FastAPI, Request, BackgroundTasks
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
STRING_SESSION = os.getenv("STRING_SESSION")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Credenciales de Chatwoot
CHATWOOT_URL = os.getenv("CHATWOOT_URL")
CHATWOOT_TOKEN = os.getenv("CHATWOOT_TOKEN")
CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID", "1")

# Puerto para el servidor FastAPI (Railway asigna PORT automáticamente)
PORT = int(os.getenv("PORT", 8080))

if not API_ID_RAW or not API_HASH or not STRING_SESSION or not OPENAI_API_KEY:
    logger.error("❌ Faltan variables esenciales (API_ID, API_HASH, STRING_SESSION o OPENAI_API_KEY).")
    sys.exit(1)

try:
    API_ID = int(API_ID_RAW)
except ValueError:
    logger.error("❌ API_ID debe ser un valor numérico entero.")
    sys.exit(1)

# Inicializar clientes
ai_client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI()

# Registro en memoria para pausar la IA cuando un humano interviene desde Chatwoot
# Diccionario: { sender_id (int): True }
HUMAN_PAUSED_USERS = {}

SYSTEM_PROMPT = """
Eres el Asistente Comercial Oficial de Privadazo (privadazo.com), la plataforma líder en Argentina y Brasil para creadoras de contenido.

Tu objetivo principal es responder dudas con un tono cercano, seguro, profesional e invitar a registrarse o unirse a la Waitlist.

REGLAS DE ORO (INVIOLABLES):
1. RESPONDE ÚNICAMENTE sobre la plataforma Privadazo. Si preguntan sobre otros temas ajenos, responde amablemente: "Lo siento, como asistente de Privadazo solo puedo responder consultas sobre nuestra plataforma y servicios de cobro."
2. NO DAR CONSEJOS LEGALES O FISCALES. Indicar que Privadazo procesa los cobros de forma discreta y anónima.
3. CONCISIÓN: Respuestas breves, directas y al grano.

KNOWLEDGE BASE:
- ¿Qué es Privadazo?: Solución que permite cobrar Shows 1-a-1, Shows Grupales y Canales VIP de Telegram usando Mercado Pago (Argentina) y PIX (Brasil).
- Anonimato: En el resumen bancario del comprador NO figura contenido adulto ni el nombre real de la creadora.
- Comisiones: Solo un 11% por transacción cobrada. Sin costos fijos ni mensualidades.
- Automatización VIP de Telegram: Bot automático que cobra, entrega el link de acceso único y expulsa automáticamente al cliente al vencer el tiempo o suscripción.
- Registro: Acceso anticipado vía Waitlist en privadazo.com.
"""

client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH,
    auto_reconnect=True,
    connection_retries=-1,
    retry_delay=5
)

# ==========================================
# FUNCIONES DE HELPER PARA CHATWOOT
# ==========================================

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
                if resp_create.status_code == 200:
                    contact_id = resp_create.json().get("payload", {}).get("contact", {}).get("id")

            if not contact_id:
                return

            # 2. Buscar si ya existe una conversación abierta para este contacto
            list_conv_url = f"{base_url}/api/v1/accounts/1/contacts/{contact_id}/conversations"
            resp_list = await http_client.get(list_conv_url, headers=headers)
            
            conversation_id = None
            if resp_list.status_code == 200:
                convs = resp_list.json().get("payload", [])
                for conv in convs:
                    if conv.get("status") in ["open", "pending"]:
                        conversation_id = conv.get("id")
                        break

            # 3. Si no existe conversación abierta, se crea una sola vez
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

            # 4. Enviar el mensaje a la conversación unificada
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
    """Procesa consultas privadas usando OpenAI."""
    try:
        response = await asyncio.to_thread(
            ai_client.chat.completions.create,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
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
# ENDPOINT WEBHOOK (CHATWOOT -> TELEGRAM)
# ==========================================

@app.post("/webhook/chatwoot")
async def chatwoot_webhook(request: Request):
    """Escucha eventos de Chatwoot. Si un agente humano escribe, manda el mensaje a Telegram."""
    try:
        data = await request.json()
        event_type = data.get("event")

        if event_type == "message_created":
            message_type = data.get("message_type")
            is_private = data.get("private", False)

            # Nos interesan respuestas humanas salientes que no sean notas privadas
            if message_type == "outgoing" and not is_private:
                # Verificar si el mensaje fue enviado por un agente (no por la API/bot)
                sender_type = data.get("sender", {}).get("type")
                
                if sender_type == "user":  # 'user' en Chatwoot es el agente humano
                    content = data.get("content", "")
                    meta = data.get("conversation", {}).get("meta", {})
                    sender_identifier = meta.get("sender", {}).get("identifier")

                    if sender_identifier and content:
                        telegram_user_id = int(sender_identifier)
                        
                        # Pausar la IA automáticamente para dar control total al humano
                        HUMAN_PAUSED_USERS[telegram_user_id] = True
                        logger.info(f"⏸️ IA pausada para el usuario {telegram_user_id} por intervención humana.")

                        # Enviar el mensaje a Telegram usando Telethon
                        await client.send_message(telegram_user_id, content)
                        logger.info(f"💬 Mensaje manual enviado a Telegram ID {telegram_user_id}: '{content}'")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"❌ Error procesando Webhook de Chatwoot: {e}")
        return {"status": "error", "detail": str(e)}

@app.get("/")
async def health_check():
    return {"status": "online", "service": "Privadazo Userbot"}

# ==========================================
# EVENT HANDLER DE TELEGRAM
# ==========================================

@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def incoming_private_message_handler(event):
    """Maneja mensajes de Telegram, consulta IA si aplica y sincroniza con Chatwoot."""
    sender = await event.get_sender()

    if not sender or getattr(sender, "bot", False) or getattr(sender, "deleted", False):
        return

    sender_name = getattr(sender, "first_name", "Usuario")
    sender_id = event.sender_id
    texto = event.raw_text

    if not texto:
        return

    logger.info(f"📩 Mensaje recibido de {sender_name} (ID: {sender_id}): '{texto}'")

    # Sincronizar mensaje entrante en Chatwoot
    asyncio.create_task(enviar_a_chatwoot(str(sender_id), sender_name, texto, "incoming"))

    # Si la IA está pausada para este usuario por intervención humana, no responde OpenAI
    if HUMAN_PAUSED_USERS.get(sender_id, False):
        logger.info(f"🤫 IA omitida para {sender_id} (Modo Agente Humano activo).")
        return

    try:
        async with client.action(event.chat_id, "typing"):
            respuesta = await consultar_ia(texto)
            delay_tipeo = min(len(respuesta) * 0.03, 4.0)
            await asyncio.sleep(max(1.5, delay_tipeo))

        await event.reply(respuesta)
        logger.info(f"📤 Respuesta IA enviada a {sender_name} (ID: {sender_id})")

        # Sincronizar respuesta saliente en Chatwoot
        asyncio.create_task(enviar_a_chatwoot(str(sender_id), sender_name, respuesta, "outgoing"))

    except Exception as e:
        logger.error(f"❌ Error procesando mensaje de {sender_id}: {e}", exc_info=True)

# ==========================================
# INICIO CONJUNTO (FASTAPI + TELETHON)
# ==========================================

async def main():
    print("=" * 60)
    print("🤖 Iniciando Userbot + Webhook Server de Privadazo...")
    print("=" * 60)

    # Iniciar servidor web FastAPI en segundo plano
    config = uvicorn.Config(app=app, host="0.0.0.0", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())

    retry_delay = 5

    while True:
        try:
            await client.connect()

            if not await client.is_user_authorized():
                logger.error("❌ STRING_SESSION inválida. Genera una nueva con generar_session.py.")
                sys.exit(1)

            me = await client.get_me()

            print("\n" + "=" * 60)
            print("✅ ¡Userbot y Servidor Web escuchando correctamente!")
            print(f"👉 Cuenta: {me.first_name} {getattr(me, 'last_name', '') or ''}".strip())
            print(f"👉 Username: @{me.username or 'sin_username'}")
            print(f"👉 ID: {me.id}")
            print(f"👉 Webhook URL: https://<tu-bot-url>.up.railway.app/webhook/chatwoot")
            print("=" * 60 + "\n")

            await client.run_until_disconnected()

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("🛑 Detención solicitada. Cerrando sesión...")
            break
        except RPCError as e:
            logger.error(f"❌ Error RPC de Telegram: {e}")
            await asyncio.sleep(retry_delay)
        except Exception as e:
            logger.error(f"❌ Error inesperado: {e}", exc_info=True)
            await asyncio.sleep(retry_delay)
        finally:
            if client.is_connected():
                await client.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Servicio detenido.")
