#!/usr/bin/env bash
# Configura el monitor y lo añade al crontab del usuario actual.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/config.json"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'HELP'
Uso: bash instalar.sh [--seleccionar-chat]

Requiere Python 3, Docker Compose v2 y cron instalados, y acceso a Docker.
Solicita los datos de configuración si no existe config.json; si existe,
lo conserva. Instala una revisión cada minuto en el crontab del usuario actual.
Ejecuta el instalador con el usuario que gestiona el despliegue Docker.
Consulta los chats del bot para seleccionar la sala. --seleccionar-chat permite
cambiar la sala de una configuración existente conservando las demás opciones.
HELP
    exit 0
fi
[[ $# -eq 0 || ( $# -eq 1 && "$1" == "--seleccionar-chat" ) ]] || { echo "Argumento desconocido. Usa --help." >&2; exit 1; }

for dependency in python3 docker crontab; do
    command -v "$dependency" >/dev/null || {
        echo "Falta $dependency. Instala Python 3, Docker Compose v2 y cron antes de continuar." >&2
        exit 1
    }
done
[[ -f "$SCRIPT_DIR/monitor.py" ]] || { echo "No se encuentra monitor.py." >&2; exit 1; }
[[ -f "$SCRIPT_DIR/seleccionar_chat.py" ]] || { echo "No se encuentra seleccionar_chat.py." >&2; exit 1; }
docker compose version >/dev/null
docker info >/dev/null 2>&1 || {
    echo "El usuario actual no puede acceder al daemon Docker. Comprueba que está iniciado y los permisos." >&2
    exit 1
}

if [[ ! -f "$CONFIG" ]]; then
    [[ -t 0 ]] || { echo "Se necesita una terminal para crear config.json. Puedes prepararlo desde config.example.json." >&2; exit 1; }
    read -r -p "Ruta del docker-compose.yml: " compose_file
    [[ -f "$compose_file" ]] || { echo "No existe el archivo Compose indicado." >&2; exit 1; }
    read -r -p "Nombre del proyecto Compose (-p, vacío para el predeterminado): " project_name
    read -r -p "Nombre para identificar el servidor en Telegram [$(hostname)]: " monitor_name
    read -r -p "URL /health del embedder [http://127.0.0.1:8081/health]: " health_url
    health_url="${health_url:-http://127.0.0.1:8081/health}"
    read -r -s -p "Token del bot de Telegram: " telegram_token
    printf '\n'
    telegram_chat="$(printf '%s' "$telegram_token" | python3 "$SCRIPT_DIR/seleccionar_chat.py")"
    [[ -n "$telegram_token" && -n "$telegram_chat" ]] || { echo "El token y el chat son obligatorios." >&2; exit 1; }
    # El token viaja por stdin, nunca como argumento visible en la lista de procesos.
    printf '%s\0' "$compose_file" "$project_name" "${monitor_name:-$(hostname)}" "$telegram_token" "$telegram_chat" "$health_url" |
        python3 -c '
import json, pathlib, sys
compose, project, name, token, chat, health_url, _ = sys.stdin.buffer.read().decode().split("\0")
config = dict(compose_files=[str(pathlib.Path(compose).resolve())], project_name=project,
              name=name, telegram_bot_token=token, telegram_chat_id=chat,
              profiles=[], excluded_services=[], embedder_service="embedder-server",
              embedder_health_url=health_url, embedder_health_timeout=15)
with open(sys.argv[1], "x") as destination:
    json.dump(config, destination, indent=2)
    destination.write("\n")
' "$CONFIG"
    unset telegram_token
else
    echo "Se conserva la configuración existente: $CONFIG"
    if [[ "${1:-}" == "--seleccionar-chat" ]]; then
        python3 "$SCRIPT_DIR/seleccionar_chat.py" --config "$CONFIG"
    fi
fi
chmod 600 "$CONFIG"

# Validar sin ejecutar el monitor ni enviar mensajes de prueba.
python3 - "$CONFIG" "$SCRIPT_DIR" <<'PY'
import json, pathlib, subprocess, sys
sys.path.insert(0, sys.argv[2])
from monitor import Monitor
try:
    config = json.loads(pathlib.Path(sys.argv[1]).read_text())
    for key in ("compose_files", "telegram_bot_token", "telegram_chat_id"):
        if not config.get(key):
            raise ValueError("Falta " + key)
    if not isinstance(config["compose_files"], list):
        raise ValueError("compose_files debe ser una lista")
    for filename in config["compose_files"]:
        if not pathlib.Path(filename).is_absolute() or not pathlib.Path(filename).is_file():
            raise ValueError("Las rutas Compose deben ser absolutas y existir")
    for key in ("project_directory", "state_file"):
        if config.get(key) and not pathlib.Path(config[key]).is_absolute():
            raise ValueError(key + " debe ser una ruta absoluta")
    monitor = Monitor(config, {})
    result = subprocess.run(monitor.compose + ["config", "--services"], cwd=monitor.cwd,
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise ValueError("Compose no válido. Revisa los archivos y su entorno (.env)")
    if config.get("embedder_service", "embedder-server") not in result.stdout.split():
        raise ValueError("El servicio embedder no figura en el Compose seleccionado")
except Exception as exc:
    print("Configuración inválida: " + str(exc), file=sys.stderr)
    sys.exit(1)
PY

# Preservar las tareas existentes y reemplazar sólo la entrada de este monitor.
python3 - "$SCRIPT_DIR" "$(command -v python3)" "$PATH" <<'PY'
import os, pathlib, shlex, subprocess, sys
directory, python, search_path = sys.argv[1:]
if any(c in directory + python + search_path for c in "\n\r%"):
    sys.exit("Las rutas y PATH no pueden contener saltos de línea ni % para instalar cron")
env = dict(os.environ, LC_ALL="C")
current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, env=env)
if current.returncode and not (current.returncode == 1 and "no crontab for" in current.stderr.lower()):
    sys.exit("No se pudo leer el crontab; no se modificó ninguna tarea")
marker = "# guardia-monitor: " + directory
lines = [line for line in current.stdout.splitlines() if not line.endswith(marker)]
quote = shlex.quote
entry = ("* * * * * PATH=" + quote(search_path) + " " + quote(python) + " "
         + quote(directory + "/monitor.py") + " --config " + quote(directory + "/config.json")
         + " >> " + quote(directory + "/monitor.log") + " 2>&1 " + marker)
if current.returncode == 0:
    backup = pathlib.Path(directory) / "crontab.backup"
    backup.write_text(current.stdout)
    backup.chmod(0o600)
subprocess.run(["crontab", "-"], input="\n".join(lines + [entry]) + "\n", text=True, check=True)
PY

printf 'Monitor instalado para el usuario %s. Se ejecutará cada minuto si el servicio cron está activo.\n' "$(id -un)"
printf 'Configuración: %s\nRegistro: %s/monitor.log\n' "$CONFIG" "$SCRIPT_DIR"
echo "Comprueba el servicio con: systemctl status cron (o crond, según la distribución)."
