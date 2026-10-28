import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from vigia_dispositivos import silencio


class ServidorTCP:
    """Aceita conexões e, se `fala`, manda bytes como um conversor serial/Ethernet faria."""

    def __init__(self, fala):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.porta = self.sock.getsockname()[1]
        self.fala = fala
        self.parar = threading.Event()
        self.conexoes = []
        self.thread = threading.Thread(target=self._laco, daemon=True)
        self.thread.start()

    def _laco(self):
        self.sock.settimeout(0.1)
        while not self.parar.is_set():
            try:
                c, _ = self.sock.accept()
            except OSError:
                continue
            self.conexoes.append(c)
            if self.fala:
                c.sendall(b"ST,GS,+0012.50kg\r\n")

    def fechar(self):
        self.parar.set()
        self.thread.join()
        for c in self.conexoes:
            c.close()
        self.sock.close()


class TestTCP(unittest.TestCase):
    def test_conversor_falando(self):
        srv = ServidorTCP(fala=True)
        self.addCleanup(srv.fechar)
        self.assertTrue(silencio.ChecagemTCP("127.0.0.1", srv.porta, janela=1).checar())

    def test_conversor_calado(self):
        srv = ServidorTCP(fala=False)
        self.addCleanup(srv.fechar)
        self.assertFalse(silencio.ChecagemTCP("127.0.0.1", srv.porta, janela=0.3).checar())

    def test_conexao_recusada_e_none(self):
        s = socket.create_server(("127.0.0.1", 0))
        porta = s.getsockname()[1]
        s.close()
        self.assertIsNone(silencio.ChecagemTCP("127.0.0.1", porta, janela=0.5).checar())


class TestArquivo(unittest.TestCase):
    def test_cresceu_ou_nao(self):
        with tempfile.TemporaryDirectory() as tmp:
            arq = Path(tmp) / "captura.log"
            c = silencio.ChecagemArquivo(arq)
            self.assertIsNone(c.checar(), "arquivo inexistente")
            arq.write_text("a")
            self.assertFalse(c.checar(), "primeira olhada só marca a referência")
            self.assertFalse(c.checar())
            with arq.open("a") as f:
                f.write("mais dados")
            self.assertTrue(c.checar())
            self.assertFalse(c.checar())


@unittest.skipIf(sys.platform.startswith("win"), "porta serial via pty só no Linux/macOS")
class TestSerial(unittest.TestCase):
    def setUp(self):
        self.mestre, self.escravo = os.openpty()
        self.caminho = os.ttyname(self.escravo)

    def tearDown(self):
        os.close(self.mestre)
        os.close(self.escravo)

    def test_porta_com_dados(self):
        os.write(self.mestre, b"leitura 42\n")
        self.assertTrue(silencio.ChecagemSerial(janela=0.5).checar(self.caminho))

    def test_porta_calada(self):
        self.assertFalse(silencio.ChecagemSerial(self.caminho, janela=0.2).checar())

    def test_porta_inexistente_ou_nao_informada(self):
        self.assertIsNone(silencio.ChecagemSerial("/dev/nao-existe-tty", janela=0.1).checar())
        self.assertIsNone(silencio.ChecagemSerial(janela=0.1).checar(None))


class TestCriar(unittest.TestCase):
    def test_tipos(self):
        self.assertIsInstance(silencio.criar({"tipo": "tcp", "host": "10.0.0.20", "porta": 4001}), silencio.ChecagemTCP)
        self.assertIsInstance(silencio.criar({"tipo": "arquivo", "caminho": "x"}), silencio.ChecagemArquivo)
        self.assertIsInstance(silencio.criar({"tipo": "serial"}), silencio.ChecagemSerial)
        with self.assertRaises(ValueError):
            silencio.criar({"tipo": "radio"})


if __name__ == "__main__":
    unittest.main()
