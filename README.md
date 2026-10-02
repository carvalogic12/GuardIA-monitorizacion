# Monitorización de GuardIA

`monitor.py` comprueba todos los servicios del Compose seleccionado y busca errores
en los logs de `embedder-server`. No necesita paquetes Python adicionales.

Puede ejecutarse de dos formas:

- como contenedor con `--loop-seconds`, que es el modo utilizado por IONOS;
- directamente en el servidor mediante cron, mantenido para instalaciones existentes.

## Contenedor Docker

La imagen incluye Python, Docker CLI y el plugin Compose. El servicio necesita:

- acceso a `/var/run/docker.sock` para consultar contenedores y logs;
- el directorio del despliegue montado en `/deployment` en solo lectura;
- `/data` persistente para conservar el estado entre reinicios;
- acceso por red al endpoint `/health` del embedder.

La configuración `config.docker.json` utiliza el Compose montado en
`/deployment/docker-compose.yml` y consulta directamente
`http://embedder-server:8080/health`. Las credenciales no forman parte de la
imagen. Se proporcionan con:

```text
MONITOR_TELEGRAM_BOT_TOKEN
MONITOR_TELEGRAM_CHAT_ID
```

También admite `MONITOR_NAME`, `MONITOR_PROJECT_NAME` y, cuando el despliegue se
levantó con un archivo diferente de `.env`, `MONITOR_COMPOSE_ENV_FILE` con su ruta
dentro del contenedor, por ejemplo `/deployment/.env.nodo1`.

Para ejecutarlo periódicamente sin cron:

```bash
python3 monitor.py --config config.docker.json --loop-seconds 60
```

Un intervalo `0` conserva el comportamiento de una única revisión. Si una revisión
falla, el proceso periódico registra el error y vuelve a intentarlo en el intervalo
siguiente. Montar el socket Docker concede al contenedor control equivalente al del
daemon aunque el bind se marque como solo lectura; la imagen debe tratarse como un
componente privilegiado y no ejecutar código ajeno.

## Despliegue automático en IONOS

`.github/workflows/build-and-deploy.yml` se ejecuta al actualizar la rama
`produccion` o manualmente mediante `workflow_dispatch`. El flujo:

1. ejecuta las pruebas unitarias;
2. construye la imagen para `linux/amd64`;
3. publica `carvalogic.ddns.net:15000/monitor:<commit>` y `:latest`;
4. despliega y verifica primero `srv-guardia-01`;
5. solo si el primer nodo termina correctamente, despliega `srv-guardia-02`.

El entorno de GitHub `Compilacion_Self_hosted` debe proporcionar estos secretos:

```text
REGISTRY_USERNAME
REGISTRY_PASSWORD
SSH_PRIVATE_KEY
MONITOR_TELEGRAM_BOT_TOKEN
```

Y estas variables de entorno no sensibles:

```text
SSH_KNOWN_HOSTS
MONITOR_TELEGRAM_CHAT_ID
```

`SSH_KNOWN_HOSTS` debe contener las claves de ambos nombres de servidor. La clave
privada debe permitir al usuario `guardia` acceder a los dos nodos. En cada servidor,
el despliegue principal debe encontrarse en `/home/guardia`. El workflow instala allí
`docker-compose.monitor.yml` y un `.env.monitor` con permisos `600`, crea
`datos/monitor` y levanta el servicio con la imagen exacta del commit. El Compose
principal no necesita contener previamente el servicio `monitor`, pero sí debe definir
la red `guardia-node` y los servicios que se van a supervisar.

## Instalación

### Instalación automática

Ejecuta con el usuario que gestiona Docker:

```bash
bash monitorizacion/instalar.sh
```

El instalador comprueba las dependencias y el acceso a Docker, solicita la ruta
del Compose y el token de Telegram, consulta los chats del bot y permite seleccionar
la sala por su nombre e ID. Añade antes el bot al grupo y envía allí
`/start@NombreDeTuBot` cuando lo indique el instalador. Puedes volver a consultar
con `r` o introducir el ID manualmente con `m` si no aparece. El instalador
protege `config.json` e instala la tarea cada
minuto. Si ya existe configuración, la conserva. Puedes ejecutarlo otra vez sin
duplicar su tarea; conserva las demás entradas y guarda `crontab.backup` antes de
modificar un crontab existente. No instala paquetes del sistema: requiere Python 3,
Docker Compose v2 y cron, cuyo servicio debe estar activo.

Para varios archivos Compose, perfiles u otras opciones, prepara `config.json`
a partir del ejemplo antes de ejecutar el instalador. Las variables necesarias
para Compose deben estar en el `.env` del despliegue o disponibles también en cron.
La primera revisión programada ya puede enviar avisos reales.

Para seleccionar otra sala de una instalación existente:

```bash
bash monitorizacion/instalar.sh --seleccionar-chat
```

También puedes cambiar sólo la sala, sin reinstalar cron ni necesitar Docker:

```bash
cd monitorizacion
python3 seleccionar_chat.py --config config.json
```

Se conserva el resto de la configuración. La consulta usa `getUpdates` sin confirmar
ni borrar actualizaciones; sólo puede descubrir chats presentes en las actualizaciones
disponibles. Si hay un webhook u otra aplicación consumiéndolas, utiliza la entrada
manual o un bot dedicado. No se envían mensajes de prueba durante la selección.

### Instalación manual

1. En el servidor con Docker, instala Python 3 y Docker Compose v2. El usuario del
   cron debe poder ejecutar `docker compose ps` sobre el despliegue.
2. Copia `config.example.json` a `config.json` y limita sus permisos con
   `chmod 600 config.json`.
3. Configura las rutas **absolutas** de `compose_files`, el token del bot Telegram
   y el identificador del chat destino. El destinatario debe iniciar conversación
   con el bot, o añadirlo al grupo y permitirle enviar mensajes.
   Usa el mismo `project_name` (si se arrancó con `-p`), archivos, orden de overrides,
   perfiles y entorno que al desplegar. Por defecto se usa como directorio de proyecto
   el del primer Compose; puede cambiarse con `project_directory`.
4. Añade mediante `crontab -e` esta línea, sustituyendo la ruta de ejemplo:

   ```cron
   * * * * * PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin /usr/bin/python3 /ruta/GuardIA/monitorizacion/monitor.py --config /ruta/GuardIA/monitorizacion/config.json >> /ruta/GuardIA/monitorizacion/monitor.log 2>&1
   ```

Para ejecutar una revisión manual (puede enviar alertas reales):

```bash
python3 monitor.py --config /ruta/GuardIA/monitorizacion/config.json
```

## Desinstalación

Ejecuta con el mismo usuario que instaló el monitor y desde su carpeta original:

```bash
cd ~/GuardIA/monitorizacion
bash desinstalar.sh
```

Retira la tarea creada por `instalar.sh`, conserva las demás entradas y guarda una
copia privada del crontab anterior en `crontab-desinstalacion-*.backup`. Conserva
también la configuración, el estado y los registros para una posible reinstalación.
No requiere acceso a Docker ni detiene servicios. Una revisión ya iniciada puede
terminar y enviar un último aviso. Ejecutarlo otra vez no modifica nada si la tarea
ya no existe.

Comprueba el resultado con `crontab -l`. Si añadiste la tarea manualmente siguiendo
la instalación manual, elimínala con `crontab -e`: el desinstalador sólo reconoce
el marcador `# guardia-monitor:` de `instalar.sh` para esta carpeta.

## Obtener el identificador del chat de Telegram

El instalador pide el **ID del chat destino** (`telegram_chat_id`), distinto del
token del bot (`telegram_bot_token`).

El subproyecto incluye un comando que lista los chats sin mostrar el token:

```bash
cd monitorizacion
python3 obtener_chat_ids.py
```

También puede obtener el token de una variable de entorno y producir JSON:

```bash
TELEGRAM_BOT_TOKEN='<token>' python3 obtener_chat_ids.py --json
```

No se acepta el token como argumento para evitar que aparezca en la lista de
procesos. Si no se define la variable, el script lo solicita mediante entrada oculta.

1. Si aún no tienes bot, créalo con `/newbot` en
   [@BotFather](https://t.me/BotFather) y guarda el token que te entrega.
2. Para recibir avisos en **privado**, abre una conversación con tu bot y envía
   `/start`. Para recibirlos en un **grupo**, añade el bot al grupo y envía allí
   `/start@NombreDeTuBot`, sustituyendo el nombre por su usuario real. Este comando
   dirigido al bot funciona con el modo de privacidad activado.
3. Ejecuta este bloque en una terminal. Solicita el token sin mostrarlo y consulta
   `getUpdates` para listar los chats de los mensajes recibidos:

   ```bash
   python3 - <<'PY'
   import getpass
   import json
   import urllib.error
   import urllib.request

   token = getpass.getpass("Token del bot: ").strip()
   url = "https://api.telegram.org/bot" + token + "/getUpdates"
   try:
       with urllib.request.urlopen(url, timeout=20) as response:
           data = json.load(response)
   except urllib.error.HTTPError as exc:
       raise SystemExit(f"Telegram respondió HTTP {exc.code}. Revisa el token o el webhook.")
   except urllib.error.URLError:
       raise SystemExit("No se pudo conectar con Telegram.")
   chats = {}
   for update in data.get("result", []):
       chat = update.get("message", {}).get("chat")
       if chat:
           chats[chat["id"]] = chat
   for chat_id, chat in chats.items():
       name = chat.get("title") or chat.get("first_name") or chat.get("username", "")
       print(f"ID: {chat_id} | Tipo: {chat['type']} | Nombre: {name}")
   if not chats:
       print("Sin mensajes: envía de nuevo el comando al bot y repite la consulta.")
   PY
   ```

4. Copia el ID del chat elegido en el instalador o en `config.json`, por ejemplo:

   ```json
   "telegram_chat_id": "-1001234567890"
   ```

   Conserva el signo negativo cuando aparezca. El número del ejemplo es ficticio.

Si no aparecen chats, envía un mensaje nuevo y comprueba que otra aplicación no
esté consumiendo las actualizaciones del mismo bot. `getUpdates` no funciona con
un webhook activo; para este monitor puedes utilizar un bot dedicado.

Referencias: [Telegram getUpdates](https://core.telegram.org/bots/api#getupdates)
y [modo de privacidad](https://core.telegram.org/bots/features#privacy-mode).

## Comprobar el envío a Telegram

Desde la carpeta de monitorización, ejecuta:

```bash
python3 monitor.py --config config.json --test-telegram
```

Envía un mensaje real al chat configurado con el estado de todos los servicios,
sin detenerlos ni modificar el estado persistido del monitor. Si Docker no está
disponible, lo indica en el mensaje de prueba. Si el envío falla, muestra el código y la descripción de Telegram sin
mostrar el token. Revisa el token si indica `Unauthorized`, el ID y la pertenencia
del bot al grupo si indica `chat not found`, y los permisos o bloqueos del bot si
indica `Forbidden`. Los errores de conexión requieren comprobar DNS, salida HTTPS
y certificados del servidor. El monitor programado también registra estos detalles
en `monitor.log`.

## Comportamiento

- Cada minuto consulta también `/health` del embedder. Por defecto usa
  `http://127.0.0.1:8081/health`, correspondiente al puerto publicado en el Compose
  de DigitalOcean, con 15 segundos de espera. Funciona también con configuraciones
  existentes que no incluyan las nuevas opciones. Para otra dirección o tiempo:

  ```json
  "embedder_health_url": "http://127.0.0.1:8081/health",
  "embedder_health_timeout": 15
  ```

  La URL debe ser accesible desde el host que ejecuta cron. El instalador la solicita
  al crear una configuración nueva. Comprueba HTTP 200, `estado: "OK"` y los estados
  de `sistemas`; alerta ante HTTP 503 u otros códigos, errores de las dependencias,
  JSON inválido, fallo de conexión o tiempo de espera agotado. Incluye el sistema,
  el identificador de configuración y el error devueltos por el endpoint.
  Avisa al fallar y al recuperarse, incluso si el contenedor sigue activo.
  Todos los avisos y `--test-telegram` incluyen el resultado de `/health`.
- Cada aviso muestra primero el estado de **todos** los servicios del Compose,
  incluidos los excluidos de alertas, y después el error o la recuperación detectada.
  Las recuperaciones incluyen la incidencia anterior y el resultado de la nueva
  comprobación; el monitor no atribuye una solución concreta ni realiza reparaciones.
  Si falla Docker, los servicios conocidos aparecen como `DESCONOCIDO`; si nunca
  pudo obtener la lista, lo indica. Los avisos largos se dividen en mensajes
  consecutivos para conservar el listado y el detalle final.
- Cada minuto alerta por servicios sin contenedor, detenidos, reiniciándose,
  pausados o con healthcheck `unhealthy`/`starting`. Comprueba todas las instancias
  existentes. `excluded_services` permite excluir tareas que terminan normalmente.
- Envía un aviso por incidencia y otro al recuperarse. También avisa cuando no
  puede consultar Docker o leer los logs. No reinicia servicios.
- Revisa los logs Docker del embedder (stdout/stderr), donde su configuración
  Logback actual escribe también las trazas de `/datos/logs/guardia.log`.
  Detecta ERROR, FATAL, excepciones Java, `Caused by:` y tracebacks Python;
  `error_pattern` permite ajustar la expresión regular.
- Agrupa las líneas de error nuevas de cada ventana en un mensaje con un extracto
  limitado al tamaño de Telegram. Los logs pueden contener datos de la aplicación:
  usa un chat adecuado. La primera revisión lee el último minuto; las siguientes
  retoman la ventana pendiente desde el estado persistido.
- Si Telegram falla, no marca ese aviso como entregado y reintenta en la siguiente
  revisión. Un bloqueo evita revisiones simultáneas. Cada configuración debe tener
  su propio estado (por defecto `config.state.json`, configurable con `state_file`).
- Es monitorización por sondeo: una caída y recuperación entre revisiones puede
  pasar inadvertida. Los logs eliminados por rotación o eliminación de contenedores
  antes de leerlos no se recuperan. Un contenedor activo sin healthcheck no garantiza
  que su aplicación responda. Si cae el servidor o cron, este monitor no puede avisar.

Pruebas locales sin Docker ni envíos a Telegram:

```bash
python3 -m unittest discover -s monitorizacion -p 'test_*.py'
```

Referencias: [Docker Compose ps](https://docs.docker.com/reference/cli/docker/compose/ps/)
y [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage).
