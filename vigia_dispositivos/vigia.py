"""Laço principal: lista o que está conectado, alimenta as máquinas de estado e avisa."""
import logging
import threading
import time

from .estado import Maquina, iso
from .plataforma import listar as listar_sistema

log = logging.getLogger("vigia-dispositivos")


class Vigia:
    def __init__(self, esperados, listar=None, historico=None, notificadores=(), relogio=time.time):
        self.esperados = list(esperados)
        self.listar = listar or listar_sistema
        self.historico = historico
        self.notificadores = list(notificadores)
        self.relogio = relogio
        inicio = relogio()
        self.maquinas = {e.nome: Maquina(e.nome, inicio, e.tolerancia_queda, e.tolerancia_volta, e.silencio_max)
                         for e in self.esperados}
        self.ultimo = {e.nome: {"encontrado": None, "portas": [], "dados": None} for e in self.esperados}
        self.ultima_rodada = None
        self.erro_listagem = None
        self._trava = threading.Lock()

    def observar(self):
        """Uma leitura do sistema, sem mexer em estado: [(esperado, encontrados, presente, portas, dados)]."""
        conectados = self.listar()
        saida = []
        for e in self.esperados:
            achados = [d for d in conectados if e.casa(d)]
            portas = [p for d in achados for p in d.portas]
            dados = None
            if e.silencio is not None and (achados or not e.tem_criterio_usb):
                dados = e.silencio.checar(portas[0] if portas else None)
            presente = bool(achados) if e.tem_criterio_usb else dados is not None
            if e.silencio is not None and presente and dados is None:
                dados = False      # aparelho está lá, mas a checagem nem conseguiu olhar
            saida.append((e, achados, presente, portas, dados))
        return saida

    def rodada(self):
        agora = self.relogio()
        try:
            leitura = self.observar()
        except Exception as erro:          # falha ao listar não pode virar "tudo sumiu"
            self.erro_listagem = f"{type(erro).__name__}: {erro}"
            log.warning("não consegui listar os dispositivos: %s", self.erro_listagem)
            return []
        self.erro_listagem = None
        eventos = []
        with self._trava:
            self.ultima_rodada = agora
            for e, achados, presente, portas, dados in leitura:
                self.ultimo[e.nome] = {"encontrado": presente, "portas": portas, "dados": dados}
                ev = self.maquinas[e.nome].observar(agora, presente, dados)
                if ev:
                    eventos.append(ev)
        for ev in eventos:
            self._publicar(ev)
        return eventos

    def _publicar(self, ev):
        log.info("%s", ev.mensagem())
        if self.historico is not None:
            try:
                self.historico.registrar(ev)
            except Exception as erro:
                log.warning("histórico: %s", erro)
        for n in self.notificadores:
            try:
                n.enviar(ev)
            except Exception as erro:
                log.warning("aviso por %s falhou: %s", n.nome, erro)

    def status(self):
        agora = self.relogio()
        with self._trava:
            dispositivos = []
            for e in self.esperados:
                m = self.maquinas[e.nome]
                u = self.ultimo[e.nome]
                dispositivos.append({
                    "nome": e.nome, "criterio": e.criterio(), "situacao": m.situacao.value,
                    "desde": iso(m.desde), "ha_segundos": round(agora - m.desde, 1),
                    "encontrado_agora": u["encontrado"], "portas": u["portas"], "dados_agora": u["dados"],
                    "ultimo_dado": iso(m.ultimo_dado) if e.silencio is not None else None,
                    "piscadas": m.piscadas})
            resp = {"agora": iso(agora), "ultima_rodada": iso(self.ultima_rodada),
                    "erro_listagem": self.erro_listagem, "dispositivos": dispositivos}
        resp["ok"] = all(d["situacao"] == "presente" for d in dispositivos)
        resp["ultimas_quedas"] = self.historico.quedas(10) if self.historico is not None else []
        return resp

    def rodar(self, intervalo, parar):
        while not parar.is_set():
            self.rodada()
            parar.wait(intervalo)

    def fechar(self):
        for n in self.notificadores:
            n.fechar()
