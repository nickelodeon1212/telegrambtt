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

# Credenciales de Chatwoot (Opcionales para evitar que crashee si no están cargadas)
CHATWOOT_URL = os.getenv("CHATWOOT_URL")
CHATWOOT_TOKEN = os.getenv("CHATWOOT_TOKEN")
CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID", "1")

# Validación exclusiva de las variables críticas de Telegram y OpenAI
if not API_ID_RAW or not API_HASH or not STRING_SESSION or not OPENAI_API_KEY:
    logger.error("❌ Faltan variables esenciales (API_ID, API_HASH, STRING_SESSION o OPENAI_API_KEY).")
    sys.exit(1)

try:
    API_ID = int(API_ID_RAW)
except ValueError:
    logger.error("❌ API_ID debe ser un valor numérico entero.")
    sys.exit(1)

# Inicializar cliente de OpenAI
ai_client = OpenAI(api_key=OPENAI_API_KEY)

# SYSTEM PROMPT COMERCIAL DE PRIVADAZO
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

# Inicializar cliente Userbot con StringSession
client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH,
    auto_reconnect=True,
    connection_retries=-1,
    retry_delay=5
)

async def enviar_a_chatwoot(sender_id: str, sender_name: str, message_text: str, message_type: str = "incoming"):
    """Sincroniza los mensajes de Telegram con el Shared Inbox de Chatwoot de forma segura."""
    if not CHATWOOT_URL or not CHATWOOT_TOKEN:
        return

    headers = {
        "api_access_token": CHATWOOT_TOKEN,
        "Content-Type": "application/json"
    }

    try:
        base_url = CHATWOOT_URL.rstrip('/')
        async with httpx.AsyncClient(timeout=5.0) as http_client:
            # 1. Buscar si el contacto ya existe en Chatwoot
            search_url = f"{base_url}/api/v1/accounts/1/contacts/search?q={sender_id}"
            resp = await http_client.get(search_url, headers=headers)
            
            contact_id = None
            if resp.status_code == 200:
                payload = resp.json().get("payload", [])
                if payload:
                    contact_id = payload[0]["id"]

            # 2. Si no existe, crearlo
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

            logger.info(f"🔄 Sincronizado con Chatwoot - Contacto ID: {contact_id}")

    except Exception as e:
        logger.error(f"❌ Error menor al sincronizar con Chatwoot: {e}")

async def consultar_ia(mensaje: str) -> str:
    """Procesa consultas privadas usando OpenAI y la Base de Conocimientos de Privadazo."""
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

@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def incoming_private_message_handler(event):
    """Maneja mensajes privados entrantes con IA, simulación de typing y Chatwoot."""
    sender = await event.get_sender()

    if not sender or getattr(sender, "bot", False) or getattr(sender, "deleted", False):
        return

    sender_name = getattr(sender, "first_name", "Usuario")
    sender_id = event.sender_id
    texto = event.raw_text

    if not texto:
        return

    logger.info(f"📩 Mensaje recibido de {sender_name} (ID: {sender_id}): '{texto}'")

    # Sincronizar mensaje entrante en Chatwoot (en segundo plano)
    asyncio.create_task(enviar_a_chatwoot(str(sender_id), sender_name, texto, "incoming"))

    try:
        # Simular presencia humana con animación de escritura 'typing'
        async with client.action(event.chat_id, "typing"):
            respuesta = await consultar_ia(texto)
            
            # Pausa natural que emula tiempo de tipeo humano
            delay_tipeo = min(len(respuesta) * 0.03, 4.0)
            await asyncio.sleep(max(1.5, delay_tipeo))

        await event.reply(respuesta)
        logger.info(f"📤 Respuesta enviada a {sender_name} (ID: {sender_id})")

        # Sincronizar respuesta saliente de la IA en Chatwoot (en segundo plano)
        asyncio.create_task(enviar_a_chatwoot(str(sender_id), sender_name, respuesta, "outgoing"))

    except Exception as e:
        logger.error(f"❌ Error al procesar mensaje de {sender_id}: {e}", exc_info=True)

async def main():
    print("=" * 60)
    print("🤖 Iniciando Userbot Asistente de Privadazo...")
    print("=" * 60)

    retry_delay = 5

    while True:
        try:
            await client.connect()

            if not await client.is_user_authorized():
                logger.error("❌ STRING_SESSION inválida. Genera una nueva con generar_session.py.")
                sys.exit(1)

            me = await client.get_me()

            print("\n" + "=" * 60)
            print("✅ ¡Userbot de Privadazo autenticado y escuchando!")
            print(f"👉 Cuenta: {me.first_name} {getattr(me, 'last_name', '') or ''}".strip())
            print(f"👉 Username: @{me.username or 'sin_username'}")
            print(f"👉 ID: {me.id}")
            print("=" * 60 + "\n")
            logger.info("Userbot escuchando mensajes activos.")

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
        print("\n👋 Userbot detenido.")
