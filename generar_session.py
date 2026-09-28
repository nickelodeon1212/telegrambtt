"""
Script auxiliar para generar STRING_SESSION de Telethon.
Ejecuta este script en tu máquina local para autenticar tu cuenta personal de Telegram
e imprimir el string de sesión que luego configurarás en .env o en Railway.
"""

import os
import sys
from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# Cargar .env por si ya tiene API_ID y API_HASH configurados
load_dotenv()


def main():
    print("=" * 65)
    print("🔑 GENERADOR DE STRING_SESSION TELETHON (USERBOT)")
    print("=" * 65)
    print("Este script te pedirá tus credenciales para generar la sesión en texto.")
    print("No se guardará ningún archivo .session en tu disco.\n")

    env_api_id = os.getenv("API_ID", "")
    env_api_hash = os.getenv("API_HASH", "")

    # Solicitar API_ID
    prompt_api_id = f"Introduce tu API_ID [{env_api_id}]: " if env_api_id else "Introduce tu API_ID: "
    api_id_input = input(prompt_api_id).strip() or env_api_id
    if not api_id_input:
        print("❌ El API_ID es obligatorio.")
        sys.exit(1)

    try:
        api_id = int(api_id_input)
    except ValueError:
        print("❌ El API_ID debe ser un número entero.")
        sys.exit(1)

    # Solicitar API_HASH
    prompt_api_hash = f"Introduce tu API_HASH [{env_api_hash}]: " if env_api_hash else "Introduce tu API_HASH: "
    api_hash = input(prompt_api_hash).strip() or env_api_hash
    if not api_hash:
        print("❌ El API_HASH es obligatorio.")
        sys.exit(1)

    # Solicitar Teléfono
    phone = input("\nIntroduce tu número de teléfono (con código de país, ej. +54911xxxxxxxx): ").strip()
    if not phone:
        print("❌ El número de teléfono es obligatorio.")
        sys.exit(1)

    print("\n⏳ Conectando con los servidores de Telegram...")

    try:
        with TelegramClient(StringSession(), api_id, api_hash) as client:
            client.start(phone=phone)
            me = client.get_me()
            session_string = client.session.save()

            print("\n" + "=" * 65)
            print("✅ ¡AUTENTICACIÓN EXITOSA!")
            print(f"👤 Conectado como: {me.first_name} (@{me.username or 'sin_username'}) [ID: {me.id}]")
            print("=" * 65)
            print("\n📋 COPIA TU STRING_SESSION (guárdalo en tu .env o en Railway):\n")
            print(session_string)
            print("\n" + "=" * 65)
            print("⚠️  IMPORTANTE: Trata este string como una contraseña. Nunca lo compartas ni lo subas a GitHub.")
            print("=" * 65)

    except Exception as e:
        print(f"\n❌ Ocurrió un error al generar la sesión: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
