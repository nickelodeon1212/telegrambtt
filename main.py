import os
import sys
import asyncio
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import RPCError

# Configuración de logs formateados para Railway y consola local
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno desde .env
load_dotenv()

API_ID_RAW = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("STRING_SESSION")

# Validación de variables de entorno requeridas para el Userbot
if not API_ID_RAW or not API_HASH or not STRING_SESSION:
    logger.error("❌ Faltan variables de entorno requeridas.")
    logger.error("Asegúrate de configurar API_ID, API_HASH y STRING_SESSION en tu .env o en Railway.")
    sys.exit(1)

try:
    API_ID = int(API_ID_RAW)
except ValueError:
    logger.error("❌ API_ID debe ser un valor numérico entero.")
    sys.exit(1)

# Inicializar cliente Userbot con StringSession
client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH,
    auto_reconnect=True,
    connection_retries=-1,
    retry_delay=5
)


async def consultar_ia(mensaje: str) -> str:
    """
    Función placeholder para procesar consultas con un modelo de IA / base de conocimientos.
    Lista para ser conectada en el siguiente paso.
    """
    # Simulación de latencia de consulta
    await asyncio.sleep(1.0)

    # Respuesta de prueba temporal
    return (
        "Hola! 👋 Recibí tu mensaje: "
        f"\"{mensaje}\"\n\n"
        "Esta es una respuesta automática del asistente inteligente. "
        "(Base de conocimientos en proceso de integración 🚀)"
    )


@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def incoming_private_message_handler(event):
    """
    Maneja exclusivamente mensajes privados entrantes de personas reales (ignora bots).
    Simula escritura humana (typing) antes de enviar la respuesta.
    """
    sender = await event.get_sender()

    # Ignorar si no se puede identificar el remitente, si es un bot, o cuenta eliminada
    if not sender or getattr(sender, "bot", False) or getattr(sender, "deleted", False):
        return

    sender_name = getattr(sender, "first_name", "Usuario")
    sender_id = event.sender_id
    texto = event.raw_text

    logger.info(f"📩 Mensaje recibido de {sender_name} (ID: {sender_id}): '{texto}'")

    try:
        # Simular presencia humana con acción de 'escribiendo...' (typing)
        async with client.action(event.chat_id, "typing"):
            # Consultar IA / Base de conocimientos
            respuesta = await consultar_ia(texto)

            # Simulación de tiempo de escritura humana (2.5 segundos adicionales)
            await asyncio.sleep(2.5)

        # Enviar la respuesta en el chat privado
        await event.reply(respuesta)
        logger.info(f"📤 Respuesta enviada exitosamente a {sender_name} (ID: {sender_id})")

    except Exception as e:
        logger.error(f"❌ Error al procesar el mensaje de {sender_id}: {e}", exc_info=True)


async def main():
    """Bucle principal del Userbot con control de sesión y reconexión automática."""
    print("=" * 60)
    print("🤖 Iniciando Userbot de Telegram (Cuenta Personal)...")
    print("=" * 60)

    retry_delay = 5

    while True:
        try:
            # Conectar e iniciar sesión usando el StringSession
            await client.connect()

            if not await client.is_user_authorized():
                logger.error("❌ STRING_SESSION no válida o sesión cerrada. Genera una nueva con generar_session.py.")
                sys.exit(1)

            me = await client.get_me()

            print("\n" + "=" * 60)
            print("✅ ¡Userbot autenticado y activo exitosamente!")
            print(f"👉 Nombre de cuenta: {me.first_name} {getattr(me, 'last_name', '') or ''}".strip())
            print(f"👉 Username: @{me.username or 'sin_username'}")
            print(f"👉 ID de usuario: {me.id}")
            print(f"👉 Teléfono vinculado: +{me.phone}")
            print("🚀 Escuchando mensajes privados entrantes...")
            print("=" * 60 + "\n")
            logger.info("Userbot escuchando mensajes.")

            # Mantener conexión activa
            await client.run_until_disconnected()

            logger.warning("⚠️ Desconexión detectada. Reintentando...")

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("🛑 Detención solicitada por el usuario. Cerrando sesión...")
            break
        except RPCError as e:
            logger.error(f"❌ Error RPC de Telegram: {e}")
            logger.info(f"🔄 Reintentando conexión en {retry_delay} segundos...")
            await asyncio.sleep(retry_delay)
        except Exception as e:
            logger.error(f"❌ Error inesperado: {e}", exc_info=True)
            logger.info(f"🔄 Reintentando conexión en {retry_delay} segundos...")
            await asyncio.sleep(retry_delay)
        finally:
            if client.is_connected():
                await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Userbot detenido.")
