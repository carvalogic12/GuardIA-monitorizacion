#!/usr/bin/env python3
"""Monitoriza un proyecto Compose una vez o periódicamente."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def command(args, cwd, logs=False):
    result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT if logs else subprocess.PIPE, text=True,
                            errors="replace", timeout=45)
    if result.returncode:
        # No mostrar stderr: Compose puede incluir valores de configuración.
        raise RuntimeError("Falló el comando " + " ".join(args[:3]))
    return result.stdout


def rows(output):
    if not output.strip():
        return []
    if output.lstrip().startswith("["):
        return json.loads(output)
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def service_status(services, containers):
    statuses = {}
    for name in services:
        instances = [c for c in containers if c["Service"] == name]
        problems = [f'{c.get("Name", name)}: {c.get("State", "desconocido")}'
                    + (f' ({c["Health"]})' if c.get("Health") else "")
                    for c in instances
                    if c.get("State") != "running"
                    or c.get("Health") in ("unhealthy", "starting")]
        statuses[name] = "; ".join(problems) if instances else "sin contenedor"
    return statuses


class Monitor:
    def __init__(self, config, state):
        self.config, self.state = config, state
        files = [str(Path(p).resolve()) for p in config["compose_files"]]
        self.cwd = config.get("project_directory") or str(Path(files[0]).parent)
        self.compose = ["docker", "compose"]
        for path in config.get("env_files", []):
            self.compose += ["--env-file", str(Path(path).resolve())]
        for path in files:
            self.compose += ["-f", path]
        if config.get("project_name"):
            self.compose += ["-p", config["project_name"]]
        self.compose += ["--project-directory", self.cwd]
        for profile in config.get("profiles", []):
            self.compose += ["--profile", profile]
        self.prefix = f'[GuardIA / {config.get("name", socket.gethostname())}] '
        self.snapshot = None
        self.service_names = list(state.get("service_names", []))
        self.health_problem = "DESCONOCIDO (pendiente de comprobación)"

    def refresh_health(self):
        url = self.config.get("embedder_health_url", "http://127.0.0.1:8081/health")
        timeout = float(self.config.get("embedder_health_timeout", 15))
        if timeout <= 0:
            raise ValueError("embedder_health_timeout debe ser mayor que cero")
        try:
            try:
                response = urllib.request.urlopen(url, timeout=timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                code = response.code
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Respuesta demasiado grande")
            data = json.loads(raw)
            if (not isinstance(data, dict) or not isinstance(data.get("sistemas"), list)
                    or data.get("estado") not in ("OK", "ERROR")
                    or any(not isinstance(item, dict) or item.get("estado") not in ("OK", "ERROR")
                           for item in data["sistemas"])):
                raise ValueError("Formato de health no válido")
            problems = []
            if code != 200:
                problems.append(f"HTTP {code}")
            if data["estado"] != "OK":
                problems.append(f'Estado global: {data["estado"]}')
            for item in data["sistemas"]:
                if item["estado"] != "OK":
                    name = str(item.get("sistema", "desconocido"))
                    if item.get("configuracionId") is not None:
                        name += f' (configuración {item["configuracionId"]})'
                    problems.append(f'{name}: {item.get("error") or item["estado"]}')
            self.health_problem = "; ".join(problems)
        except (urllib.error.URLError, OSError):
            self.health_problem = "No se pudo conectar o se agotó el tiempo de espera"
        except (ValueError, UnicodeError):
            self.health_problem = f"Respuesta /health inválida (HTTP {code})" if 'code' in locals() else "URL /health inválida"

    def refresh_services(self):
        self.snapshot = None
        self.service_names = command(self.compose + ["config", "--services"], self.cwd).split()
        self.state["service_names"] = self.service_names
        containers = rows(command(self.compose + ["ps", "--all", "--format", "json"], self.cwd))
        self.snapshot = service_status(self.service_names, containers)

    def notify(self, detail):
        lines = ["Estado de todos los servicios:"]
        for name in self.service_names:
            status = (self.snapshot[name] or "OK (running)") if self.snapshot is not None else "DESCONOCIDO (consulta Docker no disponible)"
            lines.append(f"- {name}: {status}")
        if not self.service_names:
            lines.append("No se ha podido obtener la lista de servicios.")
        lines.append("- embedder /health: " + (self.health_problem or "OK"))
        self.send("\n".join(lines) + "\n\n" + detail)

    def send(self, message):
        # Dividir sin perder estados ni el detalle final. Contar unidades UTF-16
        # también mantiene los mensajes con emojis dentro del límite de Telegram.
        chunk, size = "", 0
        for char in self.prefix + message:
            width = len(char.encode("utf-16-le")) // 2
            if size + width > 3500:
                self.send_part(chunk)
                chunk, size = "", 0
            chunk += char
            size += width
        if chunk:
            self.send_part(chunk)

    def send_part(self, message):
        payload = json.dumps({"chat_id": self.config["telegram_chat_id"],
                              "text": message}).encode()
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + self.config["telegram_bot_token"] + "/sendMessage",
            data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                result = json.load(exc)
            except (ValueError, OSError):
                result = {}
            finally:
                exc.close()
            result.setdefault("error_code", exc.code)
        except (urllib.error.URLError, OSError):
            raise RuntimeError("No se pudo conectar con Telegram: comprueba DNS, HTTPS, "
                               "certificados y tiempo de espera; se reintentará") from None
        except ValueError:
            raise RuntimeError("Telegram devolvió una respuesta no JSON; se reintentará") from None
        if not result.get("ok"):
            detail = str(result.get("description", "Sin descripción"))
            detail = detail.replace(self.config["telegram_bot_token"], "[TOKEN OCULTO]")
            detail = " ".join(detail.split())[:400]
            raise RuntimeError(f'Telegram {result.get("error_code", "error")}: {detail}; se reintentará')

    def transition(self, key, problem):
        previous = self.state.setdefault("status", {}).get(key, "")
        if bool(problem) != bool(previous):
            self.notify(f'ERROR / ALERTA: {key}: {problem}' if problem
                        else f'RECUPERADO: {key}\nIncidencia anterior: {previous}\n'
                        'Resultado: la comprobación vuelve a ser correcta. '
                        'El monitor no ha ejecutado acciones correctivas.')
        self.state["status"][key] = problem

    def check(self):
        end = int(time.time())
        docker_problem = ""
        try:
            self.refresh_services()
        except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            docker_problem = type(exc).__name__
        self.refresh_health()
        self.transition("embedder /health", self.health_problem)
        self.transition("consulta Docker Compose", docker_problem)
        if docker_problem:
            return
        excluded = self.config.get("excluded_services", [])
        for name, problem in self.snapshot.items():
            if name not in excluded:
                self.transition(name, problem)
        embedder = self.config.get("embedder_service", "embedder-server")
        start = self.state.setdefault("logs_until", end - 60)
        try:
            if embedder not in self.service_names:
                raise RuntimeError("Servicio embedder ausente del Compose")
            logs = command(self.compose + ["logs", "--no-color", "--no-log-prefix",
                           "--timestamps", "--since", str(start), "--until", str(end),
                           embedder], self.cwd, logs=True)
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            self.transition("lectura de logs del embedder", str(exc))
            return
        self.transition("lectura de logs del embedder", "")
        pattern = re.compile(self.config.get("error_pattern",
                             r"\b(?:ERROR|FATAL)\b|\b[\w.$]*(?:Exception|Error)\b|Traceback \(most recent call last\)|Caused by:"))
        # El timestamp Docker forma parte de la línea: distingue errores iguales
        # en instantes diferentes y evita duplicados en el límite de cada ventana.
        previous = set(self.state.get("last_errors", []))
        errors = [line for line in logs.splitlines() if pattern.search(line)]
        fresh = [line for line in errors if line not in previous]
        if fresh:
            self.notify(f'ERROR en {embedder}: {len(fresh)} líneas de error nuevas. '
                      'Extracto (consultar el log completo en el servidor):\n'
                      + "\n".join(fresh)[:2900])
        self.state["logs_until"] = end
        self.state["last_errors"] = errors


def load_config(path):
    config = json.loads(path.read_text())
    environment = {
        "MONITOR_NAME": "name",
        "MONITOR_PROJECT_NAME": "project_name",
        "MONITOR_TELEGRAM_BOT_TOKEN": "telegram_bot_token",
        "MONITOR_TELEGRAM_CHAT_ID": "telegram_chat_id",
    }
    for variable, key in environment.items():
        if os.environ.get(variable):
            config[key] = os.environ[variable]
    if os.environ.get("MONITOR_COMPOSE_ENV_FILE"):
        config["env_files"] = [os.environ["MONITOR_COMPOSE_ENV_FILE"]]
    return config


def run(config, config_path, test_telegram=False):
    if test_telegram:
        monitor = Monitor(config, {})
        detail = "Prueba de monitorización: conexión con Telegram correcta."
        try:
            monitor.refresh_services()
        except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            detail += f"\nERROR al consultar Docker: {type(exc).__name__}"
        monitor.refresh_health()
        if monitor.health_problem:
            detail += "\nERROR en embedder /health: " + monitor.health_problem
        monitor.notify(detail)
        print("Mensaje de prueba enviado correctamente a Telegram.")
        return
    state_path = Path(config.get("state_file", str(config_path.resolve().with_suffix(".state.json"))))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        try:
            Monitor(config, state).check()
        finally:
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state))
            temporary.replace(state_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--test-telegram", action="store_true",
                        help="Envía el estado de los servicios como prueba sin modificar el estado persistido")
    parser.add_argument("--loop-seconds", type=float, default=0,
                        help="Repite la revisión con este intervalo; 0 ejecuta una sola vez")
    args = parser.parse_args()
    os.umask(0o077)
    if args.loop_seconds < 0:
        parser.error("--loop-seconds no puede ser negativo")
    config = load_config(args.config)
    for key in ("compose_files", "telegram_bot_token", "telegram_chat_id"):
        if not config.get(key):
            parser.error(f"Falta {key} en la configuración")
    if args.test_telegram or not args.loop_seconds:
        run(config, args.config, args.test_telegram)
        return
    while True:
        started = time.monotonic()
        try:
            run(config, args.config)
        except Exception as exc:
            print(f"Monitorización: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        elapsed = time.monotonic() - started
        time.sleep(max(0, args.loop_seconds - elapsed))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Monitorización: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
