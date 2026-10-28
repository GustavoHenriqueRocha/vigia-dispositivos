"""Histórico em SQLite: todos os eventos e as quedas (início, fim, duração, motivo)."""
import sqlite3
from contextlib import closing

from .estado import iso

ESQUEMA = """
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY, dispositivo TEXT NOT NULL, tipo TEXT NOT NULL,
    em REAL NOT NULL, desde REAL NOT NULL, duracao REAL, mensagem TEXT);
CREATE TABLE IF NOT EXISTS quedas (
    id INTEGER PRIMARY KEY, dispositivo TEXT NOT NULL, motivo TEXT NOT NULL,
    inicio REAL NOT NULL, fim REAL, duracao REAL);
CREATE INDEX IF NOT EXISTS quedas_dispositivo ON quedas(dispositivo, inicio);
"""


class Historico:
    def __init__(self, caminho):
        self.caminho = str(caminho)
        with self._conexao() as c, c:
            c.executescript(ESQUEMA)

    def _conexao(self):
        return closing(sqlite3.connect(self.caminho, timeout=10))

    def registrar(self, ev):
        with self._conexao() as c, c:
            c.execute("INSERT INTO eventos (dispositivo, tipo, em, desde, duracao, mensagem) VALUES (?,?,?,?,?,?)",
                      (ev.dispositivo, ev.tipo, ev.em, ev.desde, ev.duracao, ev.mensagem()))
            aberta = c.execute("SELECT id, motivo FROM quedas WHERE dispositivo=? AND fim IS NULL "
                               "ORDER BY id DESC LIMIT 1", (ev.dispositivo,)).fetchone()
            if ev.tipo == "voltou":
                if aberta:
                    c.execute("UPDATE quedas SET fim=?, duracao=? - inicio WHERE id=?",
                              (ev.desde, ev.desde, aberta[0]))
            elif aberta:                    # mudo virou ausente (ou o contrário): mesma queda
                motivo = ev.situacao.value
                if motivo not in aberta[1].split("+"):
                    c.execute("UPDATE quedas SET motivo=? WHERE id=?", (f"{aberta[1]}+{motivo}", aberta[0]))
            else:
                c.execute("INSERT INTO quedas (dispositivo, motivo, inicio) VALUES (?,?,?)",
                          (ev.dispositivo, ev.situacao.value, ev.desde))

    def quedas(self, limite=20, dispositivo=None):
        sql = "SELECT dispositivo, motivo, inicio, fim, duracao FROM quedas"
        args = []
        if dispositivo:
            sql += " WHERE dispositivo=?"
            args.append(dispositivo)
        sql += " ORDER BY inicio DESC, id DESC LIMIT ?"
        args.append(int(limite))
        with self._conexao() as c:
            linhas = c.execute(sql, args).fetchall()
        return [{"dispositivo": d, "motivo": m, "inicio": iso(i), "fim": iso(f), "duracao": dur,
                 "inicio_ts": i, "fim_ts": f} for d, m, i, f, dur in linhas]

    def eventos(self, limite=50):
        with self._conexao() as c:
            linhas = c.execute("SELECT dispositivo, tipo, em, mensagem FROM eventos "
                               "ORDER BY em DESC, id DESC LIMIT ?", (int(limite),)).fetchall()
        return [{"dispositivo": d, "tipo": t, "em": iso(e), "mensagem": m} for d, t, e, m in linhas]
