import unittest

from vigia_dispositivos.estado import Maquina, Situacao, duracao

SIM, NAO = True, False


class Relogio:
    """Roda a máquina de estados com um relógio de mentira, uma rodada a cada `passo` segundos."""

    def __init__(self, passo=5.0, **kw):
        self.t = 1000.0
        self.passo = passo
        self.m = Maquina("balanca", self.t, **kw)
        self.eventos = []

    def rodada(self, presente=True, dados=None):
        ev = self.m.observar(self.t, presente, dados)
        if ev:
            self.eventos.append((self.t, ev))
        self.t += self.passo
        return ev

    def varias(self, n, presente=True, dados=None):
        return [e for e in (self.rodada(presente, dados) for _ in range(n)) if e]


class TestPresencaEDebounce(unittest.TestCase):
    def test_presente_o_tempo_todo_nao_gera_evento(self):
        r = Relogio(tolerancia_queda=15)
        self.assertEqual(r.varias(50), [])
        self.assertIs(r.m.situacao, Situacao.PRESENTE)

    def test_reconexao_rapida_nao_alarma_e_conta_piscada(self):
        r = Relogio(tolerancia_queda=15)
        r.varias(3)
        r.varias(2, NAO)          # 10 s fora, menos que a tolerância
        r.varias(3)
        r.varias(1, NAO)
        r.varias(3)
        self.assertEqual(r.eventos, [])
        self.assertEqual(r.m.piscadas, 2)

    def test_queda_confirmada_exatamente_na_tolerancia_com_inicio_real(self):
        r = Relogio(tolerancia_queda=15)
        r.varias(2)
        inicio = r.t
        evs = r.varias(3, NAO)    # 0, 5, 10 s fora: ainda não
        self.assertEqual(evs, [])
        ev = r.rodada(NAO)        # 15 s fora
        self.assertEqual(ev.tipo, "sumiu")
        self.assertEqual(ev.desde, inicio)
        self.assertEqual(ev.em, inicio + 15)
        self.assertIs(r.m.situacao, Situacao.AUSENTE)
        self.assertEqual(r.varias(10, NAO), [], "não repete o aviso enquanto continua fora")

    def test_queda_e_volta_com_duracao(self):
        r = Relogio(tolerancia_queda=10, tolerancia_volta=0)
        r.varias(2)
        saiu = r.t
        r.varias(12, NAO)         # 60 s fora
        voltou = r.t
        ev = r.rodada(SIM)
        self.assertEqual(ev.tipo, "voltou")
        self.assertEqual(ev.anterior, Situacao.AUSENTE)
        self.assertEqual(ev.duracao, voltou - saiu)
        self.assertIn("VOLTOU após 1 min 00 s", ev.mensagem())
        self.assertEqual([e.tipo for _, e in r.eventos], ["sumiu", "voltou"])

    def test_tolerancia_de_volta_ignora_volta_relampago(self):
        r = Relogio(tolerancia_queda=10, tolerancia_volta=10)
        r.varias(6, NAO)
        self.assertIs(r.m.situacao, Situacao.AUSENTE)
        r.varias(1, SIM)          # apareceu 5 s e sumiu de novo
        r.varias(2, NAO)
        self.assertIs(r.m.situacao, Situacao.AUSENTE)
        volta = r.t
        evs = r.varias(3, SIM)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].tipo, "voltou")
        self.assertEqual(evs[0].desde, volta)
        self.assertEqual(evs[0].em, volta + 10)

    def test_ausente_desde_o_inicio_vira_sumiu(self):
        r = Relogio(tolerancia_queda=10)
        inicio = r.t
        evs = r.varias(3, NAO)
        self.assertEqual([e.tipo for e in evs], ["sumiu"])
        self.assertEqual(evs[0].desde, inicio)


class TestMudo(unittest.TestCase):
    def test_sem_checagem_de_silencio_nunca_fica_mudo(self):
        r = Relogio(silencio_max=10)
        self.assertEqual(r.varias(30, SIM, None), [])

    def test_fica_mudo_apos_silencio_e_volta_a_falar(self):
        r = Relogio(tolerancia_queda=15, silencio_max=30)
        r.varias(4, SIM, SIM)
        ultimo = r.t - r.passo
        evs = r.varias(6, SIM, NAO)       # 30 s sem dados
        self.assertEqual([e.tipo for e in evs], ["mudo"])
        self.assertEqual(evs[0].desde, ultimo)
        self.assertIn("MUDO", evs[0].mensagem())
        evs = r.varias(1, SIM, SIM)
        self.assertEqual(evs[0].tipo, "voltou")
        self.assertEqual(evs[0].anterior, Situacao.MUDO)
        self.assertIn("VOLTOU a enviar dados", evs[0].mensagem())

    def test_silencio_curto_nao_alarma(self):
        r = Relogio(silencio_max=30)
        for _ in range(10):
            r.varias(5, SIM, NAO)         # 25 s calado
            r.varias(1, SIM, SIM)
        self.assertEqual(r.eventos, [])

    def test_reconectar_reinicia_o_prazo_de_silencio(self):
        r = Relogio(tolerancia_queda=10, silencio_max=30)
        r.varias(2, SIM, SIM)
        r.varias(20, NAO, None)           # 100 s fora: sumiu
        evs = r.varias(1, SIM, NAO)       # voltou, ainda sem dados: não pode cair direto em mudo
        self.assertEqual([e.tipo for e in evs], ["voltou"])
        self.assertIs(r.m.situacao, Situacao.PRESENTE)
        evs = r.varias(6, SIM, NAO)
        self.assertEqual([e.tipo for e in evs], ["mudo"])

    def test_mudo_que_some_e_uma_queda_so(self):
        r = Relogio(tolerancia_queda=10, silencio_max=10)
        r.varias(1, SIM, SIM)
        inicio = r.t - r.passo
        r.varias(3, SIM, NAO)
        self.assertIs(r.m.situacao, Situacao.MUDO)
        r.varias(3, NAO, None)
        self.assertIs(r.m.situacao, Situacao.AUSENTE)
        ev = r.varias(2, SIM, SIM)[0]
        self.assertEqual(ev.tipo, "voltou")
        self.assertEqual(ev.duracao, ev.desde - inicio)
        self.assertEqual([e.tipo for _, e in r.eventos], ["mudo", "sumiu", "voltou"])


class TestFormatacao(unittest.TestCase):
    def test_duracao(self):
        self.assertEqual(duracao(42), "42 s")
        self.assertEqual(duracao(185), "3 min 05 s")
        self.assertEqual(duracao(7800), "2 h 10 min")
        self.assertEqual(duracao(None), "?")


if __name__ == "__main__":
    unittest.main()
