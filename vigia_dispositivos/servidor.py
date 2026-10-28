"""GET /status (JSON) e GET / (página HTML simples com o estado e as últimas quedas)."""
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .estado import duracao

CORES = {"presente": "#1f9d55", "ausente": "#d64545", "mudo": "#d9a21b"}


def pagina(st):
    e = html.escape
    linhas = []
    for d in st["dispositivos"]:
        cor = CORES.get(d["situacao"], "#888")
        portas = ", ".join(d["portas"]) or "—"
        extra = f" · {d['piscadas']} piscada(s)" if d["piscadas"] else ""
        linhas.append(
            f"<tr><td><b>{e(d['nome'])}</b><br><small>{e(d['criterio'])}</small></td>"
            f"<td><span class=s style='background:{cor}'>{e(d['situacao'].upper())}</span></td>"
            f"<td>há {e(duracao(d['ha_segundos']))}{e(extra)}</td><td><small>{e(portas)}</small></td></tr>")
    quedas = []
    for q in st["ultimas_quedas"]:
        fim = e(q["fim"] or "em aberto")
        dur = e(duracao(q["duracao"])) if q["duracao"] is not None else "—"
        quedas.append(f"<tr><td>{e(q['dispositivo'])}</td><td>{e(q['motivo'])}</td>"
                      f"<td>{e(q['inicio'])}</td><td>{fim}</td><td>{dur}</td></tr>")
    aviso = f"<p class=erro>Falha ao listar: {e(st['erro_listagem'])}</p>" if st.get("erro_listagem") else ""
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="10"><title>Vigia de dispositivos</title>
<style>
:root{{color-scheme:light dark;--fundo:#f6f7f9;--cartao:#fff;--texto:#1d2330;--linha:#e3e6eb}}
@media (prefers-color-scheme:dark){{:root{{--fundo:#14171c;--cartao:#1d2128;--texto:#e6e9ee;--linha:#2c313a}}}}
body{{margin:0;padding:16px;background:var(--fundo);color:var(--texto);font:15px/1.4 system-ui,sans-serif}}
main{{max-width:960px;margin:auto}} table{{width:100%;border-collapse:collapse;background:var(--cartao);margin-bottom:24px}}
td,th{{padding:8px 10px;border-bottom:1px solid var(--linha);text-align:left;vertical-align:top}}
.s{{color:#fff;border-radius:4px;padding:2px 8px;font-size:13px;font-weight:600}} small{{opacity:.7}}
.erro{{background:#d64545;color:#fff;padding:8px 10px;border-radius:4px}}
</style></head><body><main>
<h1>Vigia de dispositivos</h1><p><small>Atualizado em {e(st['agora'] or '')} · <a href="/status">/status</a></small></p>
{aviso}<table><tr><th>Dispositivo</th><th>Situação</th><th>Desde</th><th>Portas</th></tr>{''.join(linhas)}</table>
<h2>Últimas quedas</h2>
<table><tr><th>Dispositivo</th><th>Motivo</th><th>Início</th><th>Fim</th><th>Duração</th></tr>
{''.join(quedas) or '<tr><td colspan=5>Nenhuma queda registrada.</td></tr>'}</table>
</main></body></html>"""


def criar_servidor(vigia, host="127.0.0.1", porta=8791):
    class Atendente(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _responder(self, codigo, tipo, corpo):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def do_GET(self):
            caminho = self.path.split("?")[0]
            if caminho == "/status":
                corpo = json.dumps(vigia.status(), ensure_ascii=False, indent=1).encode("utf-8")
                self._responder(200, "application/json; charset=utf-8", corpo)
            elif caminho == "/":
                self._responder(200, "text/html; charset=utf-8", pagina(vigia.status()).encode("utf-8"))
            else:
                self.send_error(404)

    return ThreadingHTTPServer((host, porta), Atendente)


def servir(vigia, host, porta):
    servidor = criar_servidor(vigia, host, porta)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor
