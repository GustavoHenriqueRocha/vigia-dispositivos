"""Para onde vão os avisos. Cada notificador tem `enviar(evento)` e `fechar()`.

Segredos (token do Telegram, cabeçalho de autenticação do webhook) vêm sempre de variável
de ambiente; o TOML só diz o *nome* da variável.
"""
import json
import logging
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler

log = logging.getLogger("vigia-dispositivos")


class Notificador:
    nome = "?"

    def enviar(self, evento):
        raise NotImplementedError

    def fechar(self):
        pass


class NotificadorLog(Notificador):
    """Uma linha por evento, com rotação por tamanho."""

    nome = "log"

    def __init__(self, arquivo, max_bytes=1_000_000, copias=5):
        self.logger = logging.getLogger(f"vigia-dispositivos.eventos.{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.handler = RotatingFileHandler(arquivo, maxBytes=max_bytes, backupCount=copias, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%d/%m/%Y %H:%M:%S"))
        self.logger.addHandler(self.handler)

    def enviar(self, evento):
        self.logger.info(evento.mensagem())

    def fechar(self):
        self.logger.removeHandler(self.handler)
        self.handler.close()


def _post_json(url, dados, cabecalhos=None, timeout=10.0):
    corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=corpo, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8",
                                          **(cabecalhos or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        with e:
            raise RuntimeError(f"HTTP {e.code} de {url.split('?')[0]}") from None


class NotificadorWebhook(Notificador):
    """POST com o evento em JSON (dispositivo, tipo, situacao, em, desde, duracao, mensagem)."""

    nome = "webhook"

    def __init__(self, url, cabecalhos=None, cabecalhos_env=None, timeout=10.0, ambiente=None):
        ambiente = os.environ if ambiente is None else ambiente
        self.url, self.timeout = url, float(timeout)
        self.cabecalhos = dict(cabecalhos or {})
        for chave, var in (cabecalhos_env or {}).items():
            if ambiente.get(var):
                self.cabecalhos[chave] = ambiente[var]

    def enviar(self, evento):
        _post_json(self.url, evento.como_dict(), self.cabecalhos, self.timeout)


class NotificadorTelegram(Notificador):
    """sendMessage do Bot API. Token e chat vêm das variáveis indicadas em token_env e chat_env."""

    nome = "telegram"

    def __init__(self, token, chat, api="https://api.telegram.org", timeout=10.0):
        self.token, self.chat, self.api, self.timeout = token, chat, api.rstrip("/"), float(timeout)

    @classmethod
    def do_ambiente(cls, token_env="VIGIA_TELEGRAM_TOKEN", chat_env="VIGIA_TELEGRAM_CHAT",
                    ambiente=None, **kw):
        ambiente = os.environ if ambiente is None else ambiente
        token, chat = ambiente.get(token_env), ambiente.get(chat_env)
        if not token or not chat:
            faltando = [v for v, x in ((token_env, token), (chat_env, chat)) if not x]
            log.warning("telegram desligado: defina %s", " e ".join(faltando))
            return None
        return cls(token, chat, **kw)

    def enviar(self, evento):
        _post_json(f"{self.api}/bot{self.token}/sendMessage",
                   {"chat_id": self.chat, "text": evento.mensagem()}, timeout=self.timeout)


class NotificadorComando(Notificador):
    """Roda um programa por evento. Aceita {dispositivo}, {tipo}, {situacao}, {mensagem}, {em}
    nos argumentos e também passa tudo em variáveis VIGIA_*."""

    nome = "comando"

    def __init__(self, comando, timeout=30.0):
        self.comando = comando if isinstance(comando, list) else shlex.split(comando)
        self.timeout = float(timeout)

    def enviar(self, evento):
        d = evento.como_dict()
        campos = {k: "" if v is None else str(v) for k, v in d.items()}
        args = [a.format(**campos) for a in self.comando]
        env = {**os.environ, **{f"VIGIA_{k.upper()}": v for k, v in campos.items()}}
        subprocess.run(args, env=env, timeout=self.timeout, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def criar(cfg, ambiente=None):
    """Um bloco [[notificar]] do TOML → notificador (ou None se faltou segredo)."""
    tipo = cfg.get("tipo")
    if tipo == "log":
        return NotificadorLog(cfg.get("arquivo", "vigia-eventos.log"),
                              cfg.get("max_bytes", 1_000_000), cfg.get("copias", 5))
    if tipo == "webhook":
        return NotificadorWebhook(cfg["url"], cfg.get("cabecalhos"), cfg.get("cabecalhos_env"),
                                  cfg.get("timeout", 10.0), ambiente)
    if tipo == "telegram":
        extra = {k: cfg[k] for k in ("api", "timeout") if k in cfg}
        return NotificadorTelegram.do_ambiente(cfg.get("token_env", "VIGIA_TELEGRAM_TOKEN"),
                                               cfg.get("chat_env", "VIGIA_TELEGRAM_CHAT"), ambiente, **extra)
    if tipo == "comando":
        return NotificadorComando(cfg["comando"], cfg.get("timeout", 30.0))
    raise ValueError(f"notificador desconhecido: {tipo!r} (use log, webhook, telegram ou comando)")
