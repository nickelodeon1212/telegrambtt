import os
import sys
import asyncio
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import RPCError

# Configuración de logging para monitoreo en consola y Railway
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno desde el archivo .env si existe
load_dotenv()

API_ID_RAW = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Validación estricta de variables de entorno
if not API_ID_RAW or not API_HASH or not BOT_TOKEN:
    logger.error("❌ Faltan variables de entorno requeridas.")
    logger.error("Asegúrate de definir API_ID, API_HASH y BOT_TOKEN en tu archivo .env o en las variables de Railway.")
    sys.exit(1)

try:
    API_ID = int(API_ID_RAW)
except ValueError:
    logger.error("❌ API_ID debe ser un valor numérico entero.")
    sys.exit(1)

# Inicializar cliente de Telethon para el bot
# auto_reconnect=True y connection_retries=-1 habilitan reconexión persistente a nivel de MTProto
client = TelegramClient(
    "bot_session",
    API_ID,
    API_HASH,
    auto_reconnect=True,
    connection_retries=-1,
    retry_delay=5
)

# Handler para el comando /start
@client.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    """Responde al comando /start confirmando que el bot está activo."""
    sender = await event.get_sender()
    sender_name = getattr(sender, "first_name", "Usuario")
    user_id = event.sender_id

    logger.info(f"📥 Mensaje /start recibido de {sender_name} (ID: {user_id})")

    mensaje_respuesta = (
        f"👋 ¡Hola, {sender_name}!\n\n"
        "✅ El bot está en línea y operando correctamente en Railway 🚀."
    )
    await event.respond(mensaje_respuesta)
    logger.info(f"📤 Respuesta enviada a {sender_name} (ID: {user_id})")


async def main():
    """Bucle principal con reconexión automática en caso de caídas inesperadas."""
    print("=" * 60)
    print("🤖 Iniciando Bot de Telegram con Telethon...")
    print("=" * 60)

    retry_delay = 5  # Segundos antes de reintentar tras una caída

    while True:
        try:
            # Iniciar sesión con el token del bot
            await client.start(bot_token=BOT_TOKEN)
            me = await client.get_me()

            print("\n" + "=" * 60)
            print(f"✅ ¡Bot iniciado exitosamente!")
            print(f"👉 Nombre: {me.first_name}")
            print(f"👉 Username: @{me.username}")
            print(f"👉 ID: {me.id}")
            print(f"🚀 Listo para recibir mensajes. Escuchando...")
            print("=" * 60 + "\n")
            logger.info("Bot conectado y en ejecución.")

            # Mantener la ejecución hasta que se desconecte
            await client.run_until_disconnected()

            logger.warning("⚠️ El cliente se ha desconectado. Intentando reconectar...")

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("🛑 Detención solicitada por el usuario o el sistema. Saliendo...")
            break
        except RPCError as e:
            logger.error(f"❌ Error de Telegram RPC: {e}")
            logger.info(f"🔄 Reintentando conexión en {retry_delay} segundos...")
            await asyncio.sleep(retry_delay)
        except Exception as e:
            logger.error(f"❌ Error inesperado en el loop principal: {e}", exc_info=True)
            logger.info(f"🔄 Reintentando conexión en {retry_delay} segundos...")
            await asyncio.sleep(retry_delay)
        finally:
            if client.is_connected():
                await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Proceso finalizado.")
