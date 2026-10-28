"""Checagens de "silêncio": o dispositivo está lá, mas está mandando dados?

Cada checagem devolve, por rodada:
- True: chegou dado;
- False: alcançável, mas nada chegou;
- None: nem deu para olhar (porta não abre, conexão recusada, arquivo sumiu).
"""
import os
import select
import socket
import sys
from pathlib import Path


class ChecagemTCP:
    """Conecta, espera até `janela` segundos por algum byte e desconecta.

    Para conversores serial/Ethernet e balanças que transmitem sozinhos. Atenção: muitos
    conversores aceitam um cliente só; aponte para uma porta de monitoramento ou para um
    repetidor, nunca para a conexão que o seu leitor usa.
    """

    def __init__(self, host, porta, janela=2.0, enviar=None):
        self.host, self.porta, self.janela = host, int(porta), float(janela)
        self.enviar = bytes.fromhex(enviar) if isinstance(enviar, str) else enviar

    def checar(self, porta_local=None):
        try:
            conexao = socket.create_connection((self.host, self.porta), timeout=self.janela)
        except OSError:
            return None
        with conexao:
            try:
                if self.enviar:
                    conexao.sendall(self.enviar)
                return bool(conexao.recv(1024))
            except OSError:            # inclui o timeout de leitura
                return False

    def descrever(self):
        return f"tcp {self.host}:{self.porta}"


class ChecagemArquivo:
    """O arquivo cresceu ou foi modificado desde a última rodada?

    Útil quando outro programa já lê a porta e grava log ou captura: se o arquivo para de
    crescer, a porta ficou muda. Não disputa a porta com ninguém.
    """

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        self._anterior = None

    def checar(self, porta_local=None):
        try:
            st = self.caminho.stat()
        except OSError:
            self._anterior = None
            return None
        atual = (st.st_size, st.st_mtime_ns)
        mudou = self._anterior is not None and atual != self._anterior
        self._anterior = atual
        return mudou

    def descrever(self):
        return f"arquivo {self.caminho}"


class ChecagemSerial:
    """Abre a porta serial só para leitura e espera até `janela` segundos por algum byte (Linux).

    Sem `porta`, usa a porta do próprio dispositivo encontrado. Só use quando o vigia puder
    ler a porta sem tirar dados de outro programa (porta ociosa, espelho ou bancada).
    """

    def __init__(self, porta=None, baud=None, janela=1.0):
        self.porta, self.baud, self.janela = porta, baud, float(janela)

    def checar(self, porta_local=None):
        porta = self.porta or porta_local
        if not porta or sys.platform.startswith("win"):
            return None
        try:
            fd = os.open(porta, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
        except OSError:
            return None
        try:
            if self.baud:
                _ajustar_baud(fd, int(self.baud))
            prontos, _, _ = select.select([fd], [], [], self.janela)
            if not prontos:
                return False
            try:
                return bool(os.read(fd, 1024))
            except BlockingIOError:
                return False
        except OSError:
            return None
        finally:
            os.close(fd)

    def descrever(self):
        return f"serial {self.porta or '(porta do dispositivo)'}"


def _ajustar_baud(fd, baud):
    import termios
    velocidade = getattr(termios, f"B{baud}")
    attrs = termios.tcgetattr(fd)
    attrs[4] = attrs[5] = velocidade
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def criar(cfg):
    tipo = cfg.get("tipo")
    if tipo == "tcp":
        return ChecagemTCP(cfg["host"], cfg["porta"], cfg.get("janela", 2.0), cfg.get("enviar_hex"))
    if tipo == "arquivo":
        return ChecagemArquivo(cfg["caminho"])
    if tipo == "serial":
        return ChecagemSerial(cfg.get("porta"), cfg.get("baud"), cfg.get("janela", 1.0))
    raise ValueError(f"checagem de silêncio desconhecida: {tipo!r} (use tcp, arquivo ou serial)")
