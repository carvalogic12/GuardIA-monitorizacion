#!/usr/bin/env python3
"""Lista los chats visibles para un bot de Telegram sin mostrar su token."""
import argparse
import getpass
import json
import os
import sys

from seleccionar_chat import get_chats


def clean_name(chat):
    name = chat.get("title") or chat.get("first_name") or chat.get("username") or "(sin nombre)"
    return "".join(character for character in str(name) if character.isprintable())


def printable_chats(chats):
    return [{"chat_id": chat["id"], "type": chat["type"], "name": clean_name(chat)}
            for chat in chats]


def print_table(chats, output=sys.stdout):
    records = printable_chats(chats)
    if not records:
        print("No hay chats disponibles.", file=output)
        print("Envía /start al bot en privado o /start@NombreDeTuBot en el grupo y repite.",
              file=output)
        return
    id_width = max(len("CHAT_ID"), *(len(str(record["chat_id"])) for record in records))
    type_width = max(len("TIPO"), *(len(record["type"]) for record in records))
    print(f'{"CHAT_ID":<{id_width}}  {"TIPO":<{type_width}}  NOMBRE', file=output)
    print(f'{"-" * id_width}  {"-" * type_width}  ------', file=output)
    for record in records:
        print(f'{record["chat_id"]:<{id_width}}  {record["type"]:<{type_width}}  {record["name"]}',
              file=output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Devuelve la lista como JSON")
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN", metavar="VARIABLE",
                        help="Variable que contiene el token (predeterminada: TELEGRAM_BOT_TOKEN)")
    args = parser.parse_args()

    token = os.environ.get(args.token_env, "").strip()
    if not token:
        token = getpass.getpass("Token del bot de Telegram: ").strip()
    if not token:
        raise RuntimeError("Falta el token del bot de Telegram.")

    chats = get_chats(token)
    if args.json:
        json.dump(printable_chats(chats), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print_table(chats)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Consulta de chats: {exc}", file=sys.stderr)
        sys.exit(1)
