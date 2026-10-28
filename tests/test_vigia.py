import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from vigia_dispositivos import __main__ as cli
from vigia_dispositivos.config import Esperado, carregar, esperado_de
from vigia_dispositivos.historico import Historico
from vigia_dispositivos.plataforma import Dispositivo
from vigia_dispositivos.servidor import criar_servidor
from vigia_dispositivos.vigia import Vigia

RAIZ = Path(__file__).resolve().parent.parent
FTDI = Dispositivo("0403", "6001", "A10K3XYZ", "FT232R USB UART", "FTDI", ["/dev/ttyUSB0"])
OUTRO_FTDI = Dispositivo("0403", "6001", "B99", "FT232R USB UART", "FTDI", ["/dev/ttyUSB1"])
CLP = Dispositivo("2341", "8036", "SN0042", "PLC USB", "Fabricante", ["/dev/ttyACM0"])


class Relogio:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class Guardador:
    nome = "teste"

    def __init__(self):
        self.eventos = []
        self.fechado = False

    def enviar(self, ev):
        self.eventos.append(ev)

    def fechar(self):
        self.fechado = True


class Quebrado(Guardador):
    nome = "quebrado"

    def enviar(self, ev):
        raise RuntimeError("fora do ar")


class Silencio:
    def __init__(self):
        self.resposta = True
        self.portas = []

    def checar(self, porta=None):
        self.portas.append(porta)
        return self.resposta

    def descrever(self):
        return "falsa"


class TestConfig(unittest.TestCase):
    def test_exemplo_carrega(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(tmp.name)             # o notificador de log cria o arquivo na pasta atual
        with self.assertLogs("vigia-dispositivos", "WARNING"):
            geral, esperados, avisos = carregar(RAIZ / "exemplos" / "vigia.toml", ambiente={})
        try:
            self.assertEqual(geral["status_porta"], 8791)
            nomes = [e.nome for e in esperados]
            self.assertEqual(nomes, ["balanca-expedicao", "clp-linha-2", "leitor-codigo", "conversor-serial-ethernet"])
            balanca, clp, leitor, conversor = esperados
            self.assertEqual((balanca.vid, balanca.pid, balanca.serial), ("0403", "6001", "A10K3XYZ"))
            self.assertEqual((balanca.tolerancia_queda, balanca.tolerancia_volta), (15, 3))
            self.assertEqual((clp.tolerancia_queda, clp.silencio_max), (30, 120))
            self.assertFalse(conversor.tem_criterio_usb)
            self.assertEqual(sorted(a.nome for a in avisos), ["comando", "log", "webhook"],
                             "telegram fica desligado sem as variáveis")
        finally:
            for a in avisos:
                a.fechar()

    def test_casamento(self):
        e = esperado_de({"nome": "x", "id": "0403:6001", "serial": "A10K3XYZ"}, {})
        self.assertTrue(e.casa(FTDI))
        self.assertFalse(e.casa(OUTRO_FTDI))
        self.assertTrue(esperado_de({"nome": "x", "produto": "plc"}, {}).casa(CLP))
        self.assertTrue(esperado_de({"nome": "x", "porta": "/dev/ttyACM*"}, {}).casa(CLP))
        self.assertFalse(esperado_de({"nome": "x", "porta": "/dev/ttyACM*"}, {}).casa(FTDI))
        self.assertEqual(esperado_de({"nome": "x", "vid": "0x0403"}, {}).vid, "0403")

    def test_erros_de_configuracao(self):
        with self.assertRaisesRegex(ValueError, "diga como reconhecer"):
            esperado_de({"nome": "x"}, {})
        with self.assertRaisesRegex(ValueError, "4 dígitos"):
            esperado_de({"nome": "x", "id": "403:6001"}, {})
        with tempfile.TemporaryDirectory() as tmp:
            arq = Path(tmp) / "c.toml"
            arq.write_text('[[dispositivo]]\nnome="a"\nid="0403:6001"\n[[dispositivo]]\nnome="a"\nserial="1"\n')
            with self.assertRaisesRegex(ValueError, "repetidos"):
                carregar(arq)


class TestVigia(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.relogio = Relogio()
        self.conectados = [FTDI, CLP]
        self.silencio = Silencio()
        self.esperados = [
            Esperado("balanca", vid="0403", pid="6001", serial="A10K3XYZ", tolerancia_queda=10, tolerancia_volta=0),
            Esperado("clp", produto="PLC", tolerancia_queda=10, tolerancia_volta=0,
                     silencio=self.silencio, silencio_max=30),
        ]
        self.historico = Historico(Path(self.tmp.name) / "vigia.db")
        self.guardador = Guardador()
        self.vigia = Vigia(self.esperados, listar=lambda: list(self.conectados), historico=self.historico,
                           notificadores=[Quebrado(), self.guardador], relogio=self.relogio)

    def rodadas(self, n, passo=5):
        evs = []
        for _ in range(n):
            evs += self.vigia.rodada()
            self.relogio.t += passo
        return evs

    def test_queda_e_volta_viram_historico_e_aviso(self):
        self.assertEqual(self.rodadas(3), [])
        self.conectados = [CLP]
        with self.assertLogs("vigia-dispositivos", "WARNING") as cm:
            evs = self.rodadas(4)
        self.assertTrue(any("quebrado falhou" in l for l in cm.output), "um aviso quebrado não derruba os outros")
        self.assertEqual([(e.dispositivo, e.tipo) for e in evs], [("balanca", "sumiu")])
        (q,) = self.historico.quedas()
        self.assertEqual((q["dispositivo"], q["motivo"], q["fim"]), ("balanca", "ausente", None))

        self.conectados = [CLP, FTDI]
        with self.assertLogs("vigia-dispositivos", "WARNING"):
            evs = self.rodadas(1)
        self.assertEqual([e.tipo for e in evs], ["voltou"])
        (q,) = self.historico.quedas()
        self.assertEqual(q["duracao"], 20.0)
        self.assertEqual([e.tipo for e in self.guardador.eventos], ["sumiu", "voltou"])
        self.assertEqual([e["tipo"] for e in self.historico.eventos()], ["voltou", "sumiu"])

    def test_silencio_usa_porta_do_dispositivo_e_marca_mudo(self):
        self.rodadas(2)
        self.assertEqual(self.silencio.portas[-1], "/dev/ttyACM0")
        self.silencio.resposta = False
        with self.assertLogs("vigia-dispositivos", "INFO"):
            evs = self.rodadas(8)
        self.assertEqual([(e.dispositivo, e.tipo) for e in evs], [("clp", "mudo")])
        self.assertEqual(self.historico.quedas()[0]["motivo"], "mudo")

    def test_falha_na_listagem_nao_marca_ninguem_ausente(self):
        def quebra():
            raise OSError("sem permissão")
        self.vigia.listar = quebra
        with self.assertLogs("vigia-dispositivos", "WARNING"):
            self.assertEqual(self.rodadas(10), [])
        st = self.vigia.status()
        self.assertTrue(st["ok"])
        self.assertIn("sem permissão", st["erro_listagem"])

    def test_dispositivo_so_de_rede(self):
        rede = Silencio()
        v = Vigia([Esperado("conversor", silencio=rede, silencio_max=60, tolerancia_queda=10)],
                  listar=list, relogio=self.relogio)
        rede.resposta = None                 # conexão recusada
        evs = []
        with self.assertLogs("vigia-dispositivos", "INFO"):
            for _ in range(4):
                evs += v.rodada()
                self.relogio.t += 5
        self.assertEqual([e.tipo for e in evs], ["sumiu"])

    def test_status_http_e_pagina(self):
        self.rodadas(1)
        self.conectados = [CLP]
        with self.assertLogs("vigia-dispositivos", "WARNING"):
            self.rodadas(4)
        srv = criar_servidor(self.vigia, "127.0.0.1", 0)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            with urllib.request.urlopen(base + "/status", timeout=5) as r:
                self.assertIn("application/json", r.headers["Content-Type"])
                st = json.load(r)
            with urllib.request.urlopen(base + "/", timeout=5) as r:
                pagina = r.read().decode()
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/nada", timeout=5)
            cm.exception.close()
        finally:
            srv.shutdown()
            srv.server_close()
            t.join()
        self.assertFalse(st["ok"])
        situacoes = {d["nome"]: d["situacao"] for d in st["dispositivos"]}
        self.assertEqual(situacoes, {"balanca": "ausente", "clp": "presente"})
        self.assertEqual(st["ultimas_quedas"][0]["dispositivo"], "balanca")
        self.assertIn("AUSENTE", pagina)
        self.assertIn("Últimas quedas", pagina)

    def test_fechar_fecha_notificadores(self):
        self.vigia.fechar()
        self.assertTrue(self.guardador.fechado)


class TestCLI(unittest.TestCase):
    def rodar(self, *args):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            codigo = cli.main(list(args))
        return codigo, saida.getvalue()

    def test_listar_tabela_json_e_toml(self):
        with mock.patch("vigia_dispositivos.plataforma.listar", return_value=[FTDI, CLP]):
            codigo, tabela = self.rodar("listar")
            _, js = self.rodar("listar", "--json")
            _, toml = self.rodar("listar", "--toml")
        self.assertEqual(codigo, 0)
        self.assertIn("0403:6001", tabela)
        self.assertIn("/dev/ttyACM0", tabela)
        self.assertEqual(json.loads(js)[0]["serial"], "A10K3XYZ")
        self.assertIn('id = "2341:8036"', toml)
        import tomllib
        self.assertEqual(len(tomllib.loads(toml)["dispositivo"]), 2, "saída --toml é TOML válido")

    def test_historico(self):
        with tempfile.TemporaryDirectory() as tmp:
            banco = Path(tmp) / "h.db"
            h = Historico(banco)
            relogio = Relogio()
            conectados = [FTDI]
            v = Vigia([Esperado("balanca", serial="A10K3XYZ", tolerancia_queda=5, tolerancia_volta=0)],
                      listar=lambda: list(conectados), historico=h, relogio=relogio)
            with self.assertLogs("vigia-dispositivos", "INFO"):
                conectados.clear()
                for _ in range(3):
                    v.rodada()
                    relogio.t += 5
                conectados.append(FTDI)
                v.rodada()
            codigo, texto = self.rodar("historico", "--banco", str(banco))
            self.assertEqual(codigo, 0)
            self.assertIn("balanca", texto)
            self.assertIn("ausente", texto)
            _, js = self.rodar("historico", "--banco", str(banco), "--json")
            self.assertEqual(json.loads(js)[0]["duracao"], 15.0)

    def test_vigiar_uma_vez(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "c.toml"
            cfg.write_text(f'[geral]\nbanco = "{(Path(tmp) / "v.db").as_posix()}"\n'
                           '[[dispositivo]]\nnome = "balanca"\nid = "0403:6001"\n'
                           '[[dispositivo]]\nnome = "sumido"\nserial = "NAO-EXISTE"\n')
            with mock.patch("vigia_dispositivos.vigia.listar_sistema", return_value=[FTDI]):
                codigo, texto = self.rodar("vigiar", cfg.as_posix(), "--uma-vez")
        self.assertEqual(codigo, 1)
        self.assertRegex(texto, r"balanca\s+ENCONTRADO")
        self.assertRegex(texto, r"sumido\s+NÃO ENCONTRADO")


if __name__ == "__main__":
    unittest.main()
