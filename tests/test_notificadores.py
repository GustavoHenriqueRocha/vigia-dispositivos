import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from vigia_dispositivos import notificadores as notif
from vigia_dispositivos.estado import Evento, Situacao

EVENTO = Evento("balanca-expedicao", "sumiu", 1_800_000_015.0, 1_800_000_000.0, Situacao.PRESENTE)
VOLTA = Evento("balanca-expedicao", "voltou", 1_800_000_100.0, 1_800_000_090.0, Situacao.AUSENTE, 90.0)


class ServidorLocal:
    """Servidor HTTP de verdade em 127.0.0.1 que guarda o que recebe."""

    def __init__(self, codigo=200):
        self.recebidos = []
        dono = self

        class Atendente(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                corpo = self.rfile.read(int(self.headers["Content-Length"]))
                dono.recebidos.append((self.path, dict(self.headers), json.loads(corpo)))
                self.send_response(codigo)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Atendente)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def fechar(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()


class TestWebhook(unittest.TestCase):
    def setUp(self):
        self.srv = ServidorLocal()
        self.addCleanup(self.srv.fechar)

    def test_envia_evento_em_json_com_cabecalho_do_ambiente(self):
        n = notif.criar({"tipo": "webhook", "url": self.srv.url + "/eventos",
                         "cabecalhos": {"X-Origem": "vigia"},
                         "cabecalhos_env": {"Authorization": "TESTE_AUTH"}},
                        ambiente={"TESTE_AUTH": "Bearer abc"})
        n.enviar(EVENTO)
        n.enviar(VOLTA)
        (caminho, cab, corpo), (_, _, corpo2) = self.srv.recebidos
        self.assertEqual(caminho, "/eventos")
        self.assertEqual(cab["Authorization"], "Bearer abc")
        self.assertEqual(cab["X-Origem"], "vigia")
        self.assertEqual(corpo["dispositivo"], "balanca-expedicao")
        self.assertEqual((corpo["tipo"], corpo["situacao"], corpo["anterior"]), ("sumiu", "ausente", "presente"))
        self.assertIn("SUMIU", corpo["mensagem"])
        self.assertEqual((corpo2["tipo"], corpo2["duracao"]), ("voltou", 90.0))

    def test_erro_http_vira_excecao_sem_vazar_conexao(self):
        ruim = ServidorLocal(codigo=500)
        self.addCleanup(ruim.fechar)
        n = notif.NotificadorWebhook(ruim.url + "/x")
        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            n.enviar(EVENTO)

    def test_servidor_fora_do_ar_vira_excecao(self):
        n = notif.NotificadorWebhook("http://127.0.0.1:9/nada", timeout=1)
        with self.assertRaises(OSError):
            n.enviar(EVENTO)


class TestTelegram(unittest.TestCase):
    def test_usa_token_e_chat_do_ambiente(self):
        srv = ServidorLocal()
        self.addCleanup(srv.fechar)
        n = notif.criar({"tipo": "telegram", "api": srv.url, "token_env": "TK", "chat_env": "CH"},
                        ambiente={"TK": "123:abc", "CH": "-1001"})
        n.enviar(EVENTO)
        ((caminho, _, corpo),) = srv.recebidos
        self.assertEqual(caminho, "/bot123:abc/sendMessage")
        self.assertEqual(corpo["chat_id"], "-1001")
        self.assertIn("balanca-expedicao: SUMIU", corpo["text"])

    def test_sem_variaveis_fica_desligado_com_aviso(self):
        with self.assertLogs("vigia-dispositivos", "WARNING") as cm:
            n = notif.criar({"tipo": "telegram"}, ambiente={"VIGIA_TELEGRAM_TOKEN": "x"})
        self.assertIsNone(n)
        self.assertIn("VIGIA_TELEGRAM_CHAT", cm.output[0])
        self.assertNotIn("VIGIA_TELEGRAM_TOKEN", cm.output[0])


class TestLogEComando(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)

    def test_log_com_rotacao(self):
        arq = self.pasta / "eventos.log"
        n = notif.criar({"tipo": "log", "arquivo": str(arq), "max_bytes": 300, "copias": 2})
        try:
            for _ in range(12):
                n.enviar(EVENTO)
        finally:
            n.fechar()
        self.assertIn("balanca-expedicao: SUMIU", arq.read_text(encoding="utf-8"))
        self.assertTrue((self.pasta / "eventos.log.1").exists())
        self.assertFalse((self.pasta / "eventos.log.3").exists(), "guarda só `copias` arquivos antigos")

    def test_comando_recebe_argumentos_e_variaveis(self):
        saida = self.pasta / "saida.txt"
        script = ("import os,sys; open(sys.argv[1],'w',encoding='utf-8').write("
                  "sys.argv[2] + '|' + os.environ['VIGIA_TIPO'] + '|' + os.environ['VIGIA_DISPOSITIVO'])")
        n = notif.criar({"tipo": "comando", "comando": [sys.executable, "-c", script, str(saida), "{mensagem}"]})
        n.enviar(EVENTO)
        mensagem, tipo, disp = saida.read_text(encoding="utf-8").split("|")
        self.assertIn("SUMIU", mensagem)
        self.assertEqual((tipo, disp), ("sumiu", "balanca-expedicao"))

    def test_comando_que_falha_levanta_erro(self):
        n = notif.NotificadorComando([sys.executable, "-c", "raise SystemExit(3)"])
        with self.assertRaises(Exception):
            n.enviar(EVENTO)

    def test_tipo_desconhecido(self):
        with self.assertRaisesRegex(ValueError, "notificador desconhecido"):
            notif.criar({"tipo": "pombo"})


if __name__ == "__main__":
    unittest.main()
