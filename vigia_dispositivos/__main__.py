"""Linha de comando: `listar`, `vigiar config.toml`, `historico`."""
import argparse
import json
import logging
import signal
import sys
import threading
import tomllib
from pathlib import Path

from . import __version__, plataforma
from .config import carregar
from .estado import duracao
from .historico import Historico
from .servidor import servir
from .vigia import Vigia


def cmd_listar(a):
    dispositivos = plataforma.listar(incluir_hubs=a.todos)
    if a.json:
        print(json.dumps([d.como_dict() for d in dispositivos], ensure_ascii=False, indent=1))
    elif a.toml:
        for d in dispositivos:
            nome = (d.produto or d.id).lower().replace(" ", "-")
            print("[[dispositivo]]")
            print(f"nome = {json.dumps(nome, ensure_ascii=False)}")
            print(f'id = "{d.id}"')
            if d.serial:
                print(f"serial = {json.dumps(d.serial, ensure_ascii=False)}")
            print()
    elif not dispositivos:
        print("nenhum dispositivo USB encontrado")
    else:
        print(f"{'ID':<10} {'SERIAL':<20} {'PRODUTO':<32} PORTAS")
        for d in dispositivos:
            produto = " / ".join(x for x in (d.fabricante, d.produto) if x)
            print(f"{d.id:<10} {d.serial or '-':<20} {produto[:32]:<32} {', '.join(d.portas) or '-'}")
    return 0


def cmd_vigiar(a):
    geral, esperados, avisos = carregar(a.config)
    historico = Historico(geral.get("banco", "vigia.db"))
    vigia = Vigia(esperados, historico=historico, notificadores=avisos)
    try:
        if a.uma_vez:
            ok = True
            for e, achados, presente, portas, dados in vigia.observar():
                ok &= presente and dados is not False
                txt = "ENCONTRADO" if presente else "NÃO ENCONTRADO"
                if dados is not None:
                    txt += ", com dados" if dados else ", SEM DADOS"
                print(f"{e.nome:<24} {txt}  {', '.join(portas)}")
            return 0 if ok else 1
        servidor = None
        if "status_porta" in geral:
            servidor = servir(vigia, geral.get("status_host", "127.0.0.1"), int(geral["status_porta"]))
        parar = threading.Event()
        for sinal in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sinal, lambda *_: parar.set())
        print(f"vigiando {len(esperados)} dispositivo(s) a cada {geral['intervalo']} s (Ctrl+C para sair)",
              flush=True)
        vigia.rodar(float(geral["intervalo"]), parar)
        if servidor:
            servidor.shutdown()
            servidor.server_close()
        return 0
    finally:
        vigia.fechar()


def cmd_historico(a):
    banco = a.banco
    if not banco and a.config:
        dados = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
        banco = dados.get("geral", {}).get("banco", "vigia.db")
    banco = banco or "vigia.db"
    if not Path(banco).exists():
        print(f"sem histórico em {banco}", file=sys.stderr)
        return 1
    quedas = Historico(banco).quedas(a.n, a.dispositivo)
    if a.json:
        print(json.dumps(quedas, ensure_ascii=False, indent=1))
        return 0
    if not quedas:
        print("nenhuma queda registrada")
        return 0
    print(f"{'DISPOSITIVO':<24} {'MOTIVO':<14} {'INÍCIO':<26} DURAÇÃO")
    for q in quedas:
        dur = duracao(q["duracao"]) if q["duracao"] is not None else "em aberto"
        print(f"{q['dispositivo']:<24} {q['motivo']:<14} {q['inicio']:<26} {dur}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="vigia-dispositivos",
                                description="Avisa quando um dispositivo USB ou serial some, volta ou fica mudo.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("listar", help="mostra o que está conectado agora")
    s.add_argument("--json", action="store_true")
    s.add_argument("--toml", action="store_true", help="imprime blocos [[dispositivo]] prontos para a config")
    s.add_argument("--todos", action="store_true", help="inclui os hubs USB")
    s.set_defaults(func=cmd_listar)

    s = sub.add_parser("vigiar", help="vigia os dispositivos da configuração")
    s.add_argument("config")
    s.add_argument("--uma-vez", action="store_true", help="só confere uma vez e sai (código 1 se faltar algo)")
    s.set_defaults(func=cmd_vigiar)

    s = sub.add_parser("historico", help="últimas quedas registradas")
    s.add_argument("config", nargs="?", help="usa o banco indicado na configuração")
    s.add_argument("--banco")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--dispositivo")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_historico)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%d/%m/%Y %H:%M:%S")
    try:
        return a.func(a)
    except (ValueError, OSError, tomllib.TOMLDecodeError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
