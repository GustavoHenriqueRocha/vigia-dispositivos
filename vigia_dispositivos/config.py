"""Leitura do TOML: dispositivos esperados, tolerâncias e notificadores."""
import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import notificadores as notif
from . import silencio

def ler_toml(caminho):
    """TOML de um arquivo; aceita o UTF-16 que o `>` do PowerShell 5 grava e UTF-8 com BOM."""
    bruto = Path(caminho).read_bytes()
    if bruto.startswith((b"\xff\xfe", b"\xfe\xff")):
        texto = bruto.decode("utf-16")
    else:
        texto = bruto.decode("utf-8-sig")
    return tomllib.loads(texto)


PADROES = {"intervalo": 5.0, "tolerancia_queda": 15.0, "tolerancia_volta": 3.0}


@dataclass
class Esperado:
    nome: str
    vid: str = ""
    pid: str = ""
    serial: str = ""
    produto: str = ""        # trecho do nome do produto, sem diferenciar maiúsculas
    porta: str = ""          # padrão glob sobre as portas (ex.: "*FT232*", "COM7")
    tolerancia_queda: float = PADROES["tolerancia_queda"]
    tolerancia_volta: float = PADROES["tolerancia_volta"]
    silencio_max: float | None = None
    silencio: object = None
    extra: dict = field(default_factory=dict)

    @property
    def tem_criterio_usb(self):
        return any((self.vid, self.pid, self.serial, self.produto, self.porta))

    def casa(self, d):
        if not self.tem_criterio_usb:
            return False
        if self.vid and d.vid != self.vid:
            return False
        if self.pid and d.pid != self.pid:
            return False
        if self.serial and d.serial != self.serial:
            return False
        if self.produto and self.produto.lower() not in d.produto.lower():
            return False
        if self.porta and not any(fnmatch.fnmatch(p, self.porta) for p in d.portas):
            return False
        return True

    def criterio(self):
        partes = []
        if self.vid or self.pid:
            partes.append(f"{self.vid or '*'}:{self.pid or '*'}")
        if self.serial:
            partes.append(f"serial {self.serial}")
        if self.produto:
            partes.append(f"produto ~ {self.produto}")
        if self.porta:
            partes.append(f"porta {self.porta}")
        if self.silencio is not None:
            partes.append(self.silencio.descrever())
        return ", ".join(partes)


def _hex4(valor, campo, nome):
    v = str(valor).lower().removeprefix("0x")
    if len(v) != 4 or any(c not in "0123456789abcdef" for c in v):
        raise ValueError(f"{nome}: {campo} deve ter 4 dígitos hexadecimais, veio {valor!r}")
    return v


def esperado_de(d, geral):
    nome = d.get("nome")
    if not nome:
        raise ValueError("todo [[dispositivo]] precisa de nome")
    vid, pid = d.get("vid", ""), d.get("pid", "")
    if "id" in d:
        if ":" not in d["id"]:
            raise ValueError(f"{nome}: id deve ser vid:pid, como 0403:6001")
        vid, pid = d["id"].split(":", 1)
    e = Esperado(
        nome=nome,
        vid=_hex4(vid, "vid", nome) if vid else "",
        pid=_hex4(pid, "pid", nome) if pid else "",
        serial=str(d.get("serial", "")), produto=d.get("produto", ""), porta=d.get("porta", ""),
        tolerancia_queda=float(d.get("tolerancia_queda", geral.get("tolerancia_queda", PADROES["tolerancia_queda"]))),
        tolerancia_volta=float(d.get("tolerancia_volta", geral.get("tolerancia_volta", PADROES["tolerancia_volta"]))))
    if "silencio" in d:
        cfg = d["silencio"]
        e.silencio = silencio.criar(cfg)
        e.silencio_max = float(cfg.get("max", 60))
    if not e.tem_criterio_usb and e.silencio is None:
        raise ValueError(f"{nome}: diga como reconhecer (id, vid, pid, serial, produto, porta) "
                         "ou dê uma checagem de silêncio")
    return e


def carregar(caminho, ambiente=None):
    """→ (geral, esperados, notificadores)."""
    dados = ler_toml(caminho)
    geral = {**PADROES, **dados.get("geral", {})}
    esperados = [esperado_de(d, geral) for d in dados.get("dispositivo", [])]
    if not esperados:
        raise ValueError("nenhum [[dispositivo]] na configuração")
    nomes = [e.nome for e in esperados]
    repetidos = {n for n in nomes if nomes.count(n) > 1}
    if repetidos:
        raise ValueError(f"nomes repetidos: {', '.join(sorted(repetidos))}")
    avisos = []
    try:
        for cfg in dados.get("notificar", []):
            n = notif.criar(cfg, ambiente)
            if n is not None:
                avisos.append(n)
    except Exception:
        for n in avisos:
            n.fechar()
        raise
    return geral, esperados, avisos
