"""Máquina de estados pura de um dispositivo esperado: presente, ausente ou mudo.

Não lê USB, não abre porta, não consulta relógio: recebe o instante e o que foi
observado na rodada e devolve no máximo um evento. Por isso dá para testar qualquer
sequência de quedas com um relógio de mentira.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Situacao(str, Enum):
    PRESENTE = "presente"
    AUSENTE = "ausente"
    MUDO = "mudo"


@dataclass(frozen=True)
class Evento:
    dispositivo: str
    tipo: str               # "sumiu", "mudo" ou "voltou"
    em: float               # quando o evento foi confirmado
    desde: float            # quando a situação começou de fato
    anterior: Situacao
    duracao: float | None = None   # só em "voltou": quanto tempo ficou fora

    @property
    def situacao(self):
        return {"sumiu": Situacao.AUSENTE, "mudo": Situacao.MUDO}.get(self.tipo, Situacao.PRESENTE)

    def mensagem(self):
        if self.tipo == "sumiu":
            return f"{self.dispositivo}: SUMIU (desconectado desde {hora(self.desde)})"
        if self.tipo == "mudo":
            return f"{self.dispositivo}: MUDO (conectado, mas sem dados há {duracao(self.em - self.desde)})"
        if self.anterior is Situacao.MUDO:
            return f"{self.dispositivo}: VOLTOU a enviar dados após {duracao(self.duracao)}"
        return f"{self.dispositivo}: VOLTOU após {duracao(self.duracao)} fora"

    def como_dict(self):
        return {"dispositivo": self.dispositivo, "tipo": self.tipo, "situacao": self.situacao.value,
                "anterior": self.anterior.value, "em": iso(self.em), "desde": iso(self.desde),
                "duracao": self.duracao, "mensagem": self.mensagem()}


class Maquina:
    """Estado de um dispositivo com tolerância (debounce) para quedas e voltas rápidas.

    - Ausente só é declarado depois de `tolerancia_queda` segundos seguidos sem o dispositivo.
      Uma reconexão mais rápida que isso não alarma: conta como "piscada".
    - Presente de novo só depois de `tolerancia_volta` segundos seguidos com ele.
    - Mudo: conectado, com checagem de silêncio ligada, e nenhum dado há `silencio_max` segundos.
      Ao reconectar, o prazo de silêncio recomeça.
    """

    def __init__(self, nome, inicio, tolerancia_queda=10.0, tolerancia_volta=0.0, silencio_max=None):
        self.nome = nome
        self.tolerancia_queda = tolerancia_queda
        self.tolerancia_volta = tolerancia_volta
        self.silencio_max = silencio_max
        self.situacao = Situacao.PRESENTE   # esperado começa como presente: ausente no início vira "sumiu"
        self.desde = inicio
        self.candidato = Situacao.PRESENTE
        self.candidato_desde = inicio
        self.ultimo_dado = inicio
        self.presente_antes = True
        self.inicio_queda = None
        self.piscadas = 0

    def observar(self, agora, presente, dados=None):
        """`dados`: None = sem checagem de silêncio; True/False = chegou ou não dado nesta rodada."""
        if presente and not self.presente_antes:
            self.ultimo_dado = max(self.ultimo_dado, agora)   # reconectou: prazo de silêncio recomeça
        self.presente_antes = presente
        if dados:
            self.ultimo_dado = agora

        if not presente:
            cand = Situacao.AUSENTE
        elif (dados is not None and self.silencio_max is not None
              and agora - self.ultimo_dado >= self.silencio_max):
            cand = Situacao.MUDO
        else:
            cand = Situacao.PRESENTE

        if cand is not self.candidato:
            if self.candidato is Situacao.AUSENTE and self.situacao is not Situacao.AUSENTE:
                self.piscadas += 1   # sumiu e voltou dentro da tolerância
            self.candidato, self.candidato_desde = cand, agora

        if cand is self.situacao:
            return None
        espera = {Situacao.AUSENTE: self.tolerancia_queda,
                  Situacao.PRESENTE: self.tolerancia_volta,
                  Situacao.MUDO: 0.0}[cand]
        if agora - self.candidato_desde < espera:
            return None

        anterior = self.situacao
        desde = self.ultimo_dado if cand is Situacao.MUDO else self.candidato_desde
        self.situacao, self.desde = cand, desde
        if cand is Situacao.PRESENTE:
            fora = desde - self.inicio_queda if self.inicio_queda is not None else None
            self.inicio_queda = None
            return Evento(self.nome, "voltou", agora, desde, anterior, fora)
        if self.inicio_queda is None:
            self.inicio_queda = desde
        return Evento(self.nome, "sumiu" if cand is Situacao.AUSENTE else "mudo", agora, desde, anterior)


def iso(t):
    return None if t is None else datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def hora(t):
    return datetime.fromtimestamp(t).strftime("%d/%m/%Y %H:%M:%S")


def duracao(s):
    if s is None:
        return "?"
    s = int(round(s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"
