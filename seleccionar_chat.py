#!/usr/bin/env python3
"""Selecciona un chat observado por el bot; no envía mensajes ni consume updates."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


def get_chats(token):
    request = urllib.request.Request("https://api.telegram.org/bot" + token + "/getUpdates")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise RuntimeError(f"Telegram HTTP {code}: revisa el token. Si hay un webhook "
                           "o un consumidor getUpdates activo, usa un bot dedicado o introduce el ID manualmente.") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise RuntimeError("No se pudo consultar Telegram. Revisa la conexión HTTPS y vuelve a intentar.") from None
    if not data.get("ok"):
        raise RuntimeError("Telegram rechazó la consulta de chats.")
    chats = {}
    for update in data.get("result", []):
        for field in ("message", "channel_post", "my_chat_member"):
            chat = update.get(field, {}).get("chat")
            if chat and chat.get("type") in ("private", "group", "supergroup", "channel"):
                chats[chat["id"]] = chat
    return list(chats.values())


def select_chat(token, terminal, output=None):
    output = terminal if output is None else output

    def say(text):
        print(text, file=output, flush=True)

    def ask(text):
        say(text)
        answer = terminal.readline()
        if not answer:
            raise RuntimeError("Selección cancelada: se cerró la terminal.")
        return answer.strip()

    say("Añade el bot a la sala y envía allí /start@NombreDeTuBot (en privado, /start).")
    say("Sólo se muestran chats con actualizaciones disponibles, no todos los grupos del bot.")
    ask("Pulsa Intro después de enviar el comando.")
    while True:
        try:
            chats = get_chats(token)
        except RuntimeError as exc:
            say(str(exc))
            chats = []
        for index, chat in enumerate(chats, 1):
            name = chat.get("title") or chat.get("first_name") or chat.get("username", "")
            # Evitar secuencias de control procedentes del nombre de una sala.
            name = "".join(char for char in name if char.isprintable())
            say(f"{index}. {name} ({chat['type']}) — ID: {chat['id']}")
        if not chats:
            say("No hay chats disponibles. Envía un comando nuevo al bot y reintenta.")
        answer = ask("Selecciona un número, r para consultar de nuevo, m para introducir el ID o q para cancelar:")
        if answer.lower() == "q":
            raise RuntimeError("Selección cancelada.")
        if answer.lower() == "m":
            chat_id = ask("ID del chat (conserva el signo negativo):")
            if chat_id.lstrip("-").isdigit() and int(chat_id) != 0:
                return chat_id
            say("El ID debe ser un número distinto de cero.")
        elif answer.isdigit() and 1 <= int(answer) <= len(chats):
            return str(chats[int(answer) - 1]["id"])


def select_from_terminal(token, path="/dev/tty"):
    # Una terminal no admite seek. Abrir lectura y escritura por separado evita
    # que el búfer de un archivo r+ intente reposicionarse al cambiar de operación.
    with open(path, "r") as reader, open(path, "w") as writer:
        return select_chat(token, reader, writer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Actualiza sólo telegram_chat_id de esta configuración")
    args = parser.parse_args()
    config = json.loads(args.config.read_text()) if args.config else None
    token = config["telegram_bot_token"] if config is not None else sys.stdin.read().strip()
    if not token:
        raise RuntimeError("Falta el token de Telegram.")
    chat_id = select_from_terminal(token)
    if config is None:
        print(chat_id)
    else:
        import tempfile
        config["telegram_chat_id"] = chat_id
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=args.config.resolve().parent,
                                             suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                json.dump(config, output, indent=2)
                output.write("\n")
            os.replace(temporary, args.config)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(f"Sala configurada: {chat_id}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print(f"Selección de sala: {exc}", file=sys.stderr)
        sys.exit(1)
