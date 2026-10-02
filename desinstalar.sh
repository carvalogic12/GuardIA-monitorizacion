#!/usr/bin/env bash
# Retira del crontab del usuario actual la tarea creada por instalar.sh.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'HELP'
Uso: bash desinstalar.sh

Ejecuta desde la misma carpeta y con el mismo usuario que instaló el monitor.
Elimina su tarea de cron y conserva las demás tareas, configuración y registros.
No detiene Docker ni el servicio cron. Una revisión ya iniciada puede terminar.
Las entradas añadidas manualmente sin el marcador del instalador deben retirarse
con crontab -e.
HELP
    exit 0
fi
[[ $# -eq 0 ]] || { echo "Argumento desconocido. Usa --help." >&2; exit 1; }

for dependency in python3 crontab; do
    command -v "$dependency" >/dev/null || {
        echo "Falta $dependency; no se ha modificado la instalación." >&2
        exit 1
    }
done

python3 - "$SCRIPT_DIR" <<'PY'
import os
from pathlib import Path
import subprocess
import sys
import tempfile

directory = sys.argv[1]
env = dict(os.environ, LC_ALL="C")
current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, env=env)
if current.returncode:
    if current.returncode == 1 and "no crontab for" in current.stderr.lower():
        print("El usuario actual no tiene crontab. No hay tarea que retirar.")
        sys.exit(0)
    sys.exit("No se pudo leer el crontab; no se ha modificado ninguna tarea.")

marker = "# guardia-monitor: " + directory
lines = current.stdout.splitlines(keepends=True)
remaining = [line for line in lines if not line.rstrip("\r\n").endswith(marker)]
removed = len(lines) - len(remaining)
if not removed:
    print("No se encontró la tarea de esta carpeta. Comprueba el usuario y la ruta de instalación.")
    print("Si la añadiste manualmente, retírala con crontab -e.")
    sys.exit(0)

# Copia única y privada: una segunda ejecución no sobrescribe la copia anterior.
with tempfile.NamedTemporaryFile(mode="w", prefix="crontab-desinstalacion-",
                                 suffix=".backup", dir=directory, delete=False) as backup:
    backup.write(current.stdout)
    backup_path = Path(backup.name)
result = subprocess.run(["crontab", "-"], input="".join(remaining), text=True,
                        capture_output=True, env=env)
if result.returncode:
    sys.exit(f"No se pudo actualizar el crontab. Copia anterior: {backup_path}")
print(f"Monitor desinstalado del crontab del usuario actual ({removed} entrada(s) retirada(s)).")
print(f"Copia del crontab anterior: {backup_path}")
print("Se conservan la configuración, el estado, los registros y los scripts.")
print("Una revisión que ya esté en ejecución puede terminar y enviar su aviso.")
PY
