import unittest
import io
import json
import urllib.error
from unittest.mock import patch

from monitor import Monitor, load_config, rows, service_status


class MonitorTests(unittest.TestCase):
    def test_environment_overrides_container_configuration(self):
        config_file = unittest.mock.Mock()
        config_file.read_text.return_value = json.dumps({
            "compose_files": ["/deployment/docker-compose.yml"],
            "telegram_bot_token": "from-file",
            "telegram_chat_id": "from-file",
        })
        with patch.dict("monitor.os.environ", {
            "MONITOR_NAME": "node-2",
            "MONITOR_PROJECT_NAME": "guardia",
            "MONITOR_TELEGRAM_BOT_TOKEN": "from-env",
            "MONITOR_TELEGRAM_CHAT_ID": "-100123",
            "MONITOR_COMPOSE_ENV_FILE": "/deployment/.env.nodo2",
        }, clear=True):
            config = load_config(config_file)
        self.assertEqual(config["name"], "node-2")
        self.assertEqual(config["project_name"], "guardia")
        self.assertEqual(config["telegram_bot_token"], "from-env")
        self.assertEqual(config["telegram_chat_id"], "-100123")
        self.assertEqual(config["env_files"], ["/deployment/.env.nodo2"])

    def test_compose_env_file_is_added_before_compose_file(self):
        monitor = Monitor({
            "compose_files": ["/deployment/docker-compose.yml"],
            "env_files": ["/deployment/.env"],
            "project_name": "guardia",
        }, {})
        self.assertEqual(monitor.compose[:8], [
            "docker", "compose", "--env-file", "/deployment/.env",
            "-f", "/deployment/docker-compose.yml", "-p", "guardia",
        ])

    @patch("monitor.urllib.request.urlopen")
    def test_health_success_and_dependency_failure(self, urlopen):
        monitor = Monitor({"compose_files": ["/tmp/compose.yml"]}, {})
        healthy = io.BytesIO(b'{"estado":"OK","sistemas":[{"sistema":"BASE_DATOS","estado":"OK"}]}')
        healthy.code = 200
        urlopen.return_value = healthy
        monitor.refresh_health()
        self.assertEqual(monitor.health_problem, "")
        self.assertTrue(healthy.closed)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 15)
        failure = urllib.error.HTTPError("http://localhost/health", 503, "Unavailable", {},
            io.BytesIO(b'{"estado":"ERROR","sistemas":[{"sistema":"LLM","configuracionId":2,"estado":"ERROR","error":"sin conexion"}]}'))
        urlopen.side_effect = failure
        monitor.refresh_health()
        self.assertIn("HTTP 503", monitor.health_problem)
        self.assertIn("LLM (configuración 2): sin conexion", monitor.health_problem)
        self.assertTrue(failure.closed)

    @patch("monitor.urllib.request.urlopen")
    def test_health_invalid_response_timeout_and_http_200_error(self, urlopen):
        monitor = Monitor({"compose_files": ["/tmp/compose.yml"]}, {})
        for body in (b'<html>login</html>', b'{}', b'[]'):
            response = io.BytesIO(body)
            response.code = 200
            urlopen.return_value = response
            monitor.refresh_health()
            self.assertIn("inválida", monitor.health_problem)
        response = io.BytesIO(b'{"estado":"ERROR","sistemas":[]}')
        response.code = 200
        urlopen.return_value = response
        monitor.refresh_health()
        self.assertIn("Estado global: ERROR", monitor.health_problem)
        urlopen.side_effect = TimeoutError()
        monitor.refresh_health()
        self.assertIn("tiempo de espera", monitor.health_problem)

    @patch("monitor.command")
    def test_health_alert_and_recovery_while_container_running(self, command):
        monitor = self.make_monitor()
        responses = ["embedder-server", '[{"Service":"embedder-server","State":"running"}]', ""]
        for problem in ("HTTP 503; BASE_DATOS: sin conexión", "HTTP 503; BASE_DATOS: sin conexión", ""):
            monitor.health_problem = problem
            command.side_effect = responses
            monitor.check()
        self.assertEqual(monitor.send.call_count, 2)
        alert, recovery = [call.args[0] for call in monitor.send.call_args_list]
        self.assertIn("embedder-server: OK (running)", alert)
        self.assertIn("ERROR / ALERTA: embedder /health", alert)
        self.assertIn("embedder /health: OK", recovery)
        self.assertIn("RECUPERADO: embedder /health", recovery)
        self.assertIn("BASE_DATOS: sin conexión", recovery)

    @patch("monitor.command")
    def test_alert_and_recovery_include_entire_current_snapshot(self, command):
        monitor = self.make_monitor()
        monitor.config["excluded_services"] = ["job"]
        def responses(front):
            return ["front\nembedder-server\njob\n", json.dumps([
                {"Service": "front", "State": front},
                {"Service": "embedder-server", "State": "running"},
                {"Service": "job", "State": "exited"}]), ""]
        command.side_effect = responses("exited")
        monitor.check()
        alert = monitor.send.call_args.args[0]
        self.assertIn("front: front: exited", alert)
        self.assertIn("embedder-server: OK (running)", alert)
        self.assertIn("job: job: exited", alert)
        self.assertLess(alert.index("job:"), alert.index("ERROR / ALERTA"))
        monitor.send.reset_mock()
        command.side_effect = responses("running")
        monitor.check()
        recovered = monitor.send.call_args.args[0]
        self.assertIn("front: OK (running)", recovered)
        self.assertIn("RECUPERADO: front", recovered)
        self.assertIn("Incidencia anterior: front: exited", recovered)
        self.assertIn("no ha ejecutado acciones correctivas", recovered)

    @patch("monitor.command", side_effect=RuntimeError("unavailable"))
    def test_failed_query_does_not_report_old_status_as_current(self, command):
        monitor = self.make_monitor({"service_names": ["front"]})
        monitor.snapshot = {"front": ""}
        monitor.check()
        message = monitor.send.call_args.args[0]
        self.assertIn("front: DESCONOCIDO", message)
        self.assertNotIn("OK (running)", message)

    def test_long_message_keeps_all_content(self):
        monitor = Monitor({"compose_files": ["/tmp/compose.yml"]}, {})
        monitor.send_part = unittest.mock.Mock()
        message = "Estado: " + "😀" * 4000 + "\nERROR: final"
        monitor.send(message)
        parts = [call.args[0] for call in monitor.send_part.call_args_list]
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), monitor.prefix + message)
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in parts))

    @patch("monitor.urllib.request.urlopen")
    def test_telegram_diagnostic_hides_token(self, urlopen):
        monitor = Monitor({"compose_files": ["/tmp/compose.yml"],
                           "telegram_bot_token": "secret-token", "telegram_chat_id": "123"}, {})
        urlopen.side_effect = urllib.error.HTTPError(
            "https://example.invalid/secret-token", 400, "Bad Request", {},
            io.BytesIO(b'{"ok":false,"description":"Bad Request: chat not found secret-token"}'))
        with self.assertRaises(RuntimeError) as caught:
            monitor.send("test")
        self.assertIn("Telegram 400: Bad Request: chat not found", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertTrue(urlopen.side_effect.closed)

    @patch("monitor.urllib.request.urlopen")
    def test_telegram_closes_non_json_http_error(self, urlopen):
        monitor = Monitor({"compose_files": ["/tmp/compose.yml"],
                           "telegram_bot_token": "secret-token", "telegram_chat_id": "123"}, {})
        urlopen.side_effect = urllib.error.HTTPError(
            "https://example.invalid", 502, "Bad Gateway", {}, io.BytesIO(b"not JSON"))
        with self.assertRaisesRegex(RuntimeError, "Telegram 502"):
            monitor.send("test")
        self.assertTrue(urlopen.side_effect.closed)

    @patch("monitor.urllib.request.urlopen", side_effect=urllib.error.URLError("secret-token"))
    def test_telegram_network_diagnostic(self, urlopen):
        monitor = Monitor({"compose_files": ["/tmp/compose.yml"],
                           "telegram_bot_token": "secret-token", "telegram_chat_id": "123"}, {})
        with self.assertRaisesRegex(RuntimeError, "No se pudo conectar con Telegram") as caught:
            monitor.send("test")
        self.assertNotIn("secret-token", str(caught.exception))

    def make_monitor(self, state=None):
        monitor = Monitor({"compose_files": ["/tmp/docker-compose.yml"]},
                          {} if state is None else state)
        monitor.send = unittest.mock.Mock()
        monitor.health_problem = ""
        monitor.refresh_health = unittest.mock.Mock()
        return monitor

    def test_status_and_replicas(self):
        states = service_status(["missing", "web", "db"], [
            {"Service": "web", "State": "running"},
            {"Service": "web", "State": "exited"},
            {"Service": "db", "State": "running", "Health": "unhealthy"}])
        self.assertEqual(states["missing"], "sin contenedor")
        self.assertIn("exited", states["web"])
        self.assertIn("unhealthy", states["db"])
        self.assertEqual(service_status(["web"], [{"Service": "web", "State": "running"}]), {"web": ""})

    def test_json_formats(self):
        self.assertEqual(rows('[{"Service":"web"}]'), rows('{"Service":"web"}\n'))

    def test_transition_and_retry(self):
        monitor = self.make_monitor()
        monitor.transition("web", "exited")
        monitor.transition("web", "exited")
        monitor.transition("web", "")
        self.assertEqual(monitor.send.call_count, 2)
        monitor.send.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            monitor.transition("web", "exited")
        self.assertEqual(monitor.state["status"]["web"], "")

    @patch("monitor.time.time", return_value=120)
    @patch("monitor.command")
    def test_logs_deduplication_and_retry(self, command, clock):
        monitor = self.make_monitor({"logs_until": 60})
        responses = ["embedder-server\n",
                     '[{"Service":"embedder-server","State":"running"}]',
                     "2026-09-05T00:00:01Z ERROR fallo\nCaused by: java.io.IOException\n"]
        command.side_effect = responses
        monitor.send.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            monitor.check()
        self.assertEqual(monitor.state["logs_until"], 60)
        command.side_effect = responses
        monitor.send.side_effect = None
        monitor.check()
        self.assertEqual(monitor.state["logs_until"], 120)
        self.assertIn("embedder-server: OK (running)", monitor.send.call_args.args[0])
        self.assertIn("ERROR en embedder-server", monitor.send.call_args.args[0])
        monitor.send.reset_mock()
        command.side_effect = responses
        monitor.check()
        monitor.send.assert_not_called()

    @patch("monitor.command", side_effect=RuntimeError("Docker unavailable"))
    def test_docker_failure_alert(self, command):
        monitor = self.make_monitor()
        monitor.check()
        monitor.check()
        monitor.send.assert_called_once()
        self.assertIn("consulta Docker Compose", monitor.send.call_args.args[0])

    @patch("monitor.time.time", side_effect=[120, 180, 240])
    @patch("monitor.command")
    def test_same_error_at_later_time_is_a_new_alert(self, command, clock):
        monitor = self.make_monitor({"logs_until": 60})
        first = "2026-09-06T00:01:10Z ERROR fallo de conexión"
        later = "2026-09-06T00:03:10Z ERROR fallo de conexión"
        for logs in (first, first, later):
            command.side_effect = ["embedder-server",
                '[{"Service":"embedder-server","State":"running"}]', logs]
            monitor.check()
        self.assertEqual(monitor.send.call_count, 2)
        self.assertIn(later, monitor.send.call_args.args[0])
        self.assertIn("embedder /health: OK", monitor.send.call_args.args[0])
        self.assertIn("embedder-server: OK (running)", monitor.send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
