"""Descobre o que está conectado agora. Única parte que conversa com o sistema operacional.

- Linux: lê /sys/bus/usb/devices (idVendor, idProduct, serial, product) e liga as portas
  seriais (/dev/serial/by-id, /dev/ttyUSB*, /dev/ttyACM*) ao aparelho USB dono delas.
- Windows: pede ao PowerShell (`Get-PnpDevice`) ou, na falta dele, ao `wmic` a lista de
  dispositivos USB e tira VID, PID, serial e porta COM do InstanceId e do nome amigável.

As funções de leitura recebem a raiz do /sys ou o executor de comandos, então os testes
rodam sobre uma árvore falsa ou sobre uma saída gravada, sem hardware nenhum.
"""
import csv
import io
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Dispositivo:
    vid: str = ""            # 4 dígitos hexadecimais, minúsculos
    pid: str = ""
    serial: str = ""
    produto: str = ""
    fabricante: str = ""
    portas: list[str] = field(default_factory=list)
    local: str = ""          # nome no /sys ou InstanceId no Windows

    @property
    def id(self):
        return f"{self.vid}:{self.pid}"

    def como_dict(self):
        return {"id": self.id, **asdict(self)}


def listar(incluir_hubs=False):
    if sys.platform.startswith("win"):
        return listar_windows(incluir_hubs=incluir_hubs)
    return listar_linux(incluir_hubs=incluir_hubs)


# ----------------------------------------------------------------------------- Linux

def _ler(caminho):
    try:
        return caminho.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _dono_usb(caminho, donos):
    """Sobe a partir do sysfs de uma tty até achar o aparelho USB que a contém."""
    try:
        p = caminho.resolve()
    except OSError:
        return None
    while p != p.parent:
        if p in donos:
            return p
        p = p.parent
    return None


def listar_linux(raiz_sys="/sys", raiz_dev="/dev", incluir_hubs=False):
    raiz_sys, raiz_dev = Path(raiz_sys), Path(raiz_dev)
    base = raiz_sys / "bus" / "usb" / "devices"
    if not base.is_dir():
        return []
    donos = {}
    for entrada in sorted(base.iterdir()):
        if ":" in entrada.name:          # 1-1:1.0 é interface, não aparelho
            continue
        vid = _ler(entrada / "idVendor").lower()
        if not vid:
            continue
        if not incluir_hubs and _ler(entrada / "bDeviceClass") == "09":
            continue
        donos[entrada.resolve()] = Dispositivo(
            vid=vid, pid=_ler(entrada / "idProduct").lower(), serial=_ler(entrada / "serial"),
            produto=_ler(entrada / "product"), fabricante=_ler(entrada / "manufacturer"),
            local=entrada.name)

    por_id = {}
    pasta_by_id = raiz_dev / "serial" / "by-id"
    if pasta_by_id.is_dir():
        for link in sorted(pasta_by_id.iterdir()):
            por_id.setdefault(Path(_destino(link)).name, []).append(str(link))

    pasta_tty = raiz_sys / "class" / "tty"
    if pasta_tty.is_dir():
        for tty in sorted(pasta_tty.iterdir()):
            if not tty.name.startswith(("ttyUSB", "ttyACM")):
                continue
            dono = _dono_usb(tty / "device", donos)
            if dono is not None:
                donos[dono].portas.extend(por_id.get(tty.name) or [str(raiz_dev / tty.name)])
    return list(donos.values())


def _destino(link):
    try:
        return link.readlink()
    except OSError:
        return link


# ----------------------------------------------------------------------------- Windows

COMANDO_POWERSHELL = [
    "powershell", "-NoProfile", "-NonInteractive", "-Command",
    "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match '^(USB|FTDIBUS)\\\\' } | "
    "Select-Object InstanceId,FriendlyName,Manufacturer,Class,Status | ConvertTo-Json -Compress",
]
COMANDO_WMIC = ["wmic", "path", "Win32_PnPEntity", "get", "DeviceID,Name,Manufacturer,PNPClass", "/format:csv"]

_RE_VIDPID = re.compile(r"VID_([0-9A-F]{4})[&+]PID_([0-9A-F]{4})(.*)", re.IGNORECASE)
_RE_COM = re.compile(r"\((COM\d+)\)")


def parse_powershell(texto):
    """Saída de `Get-PnpDevice ... | ConvertTo-Json` → lista de registros {id, nome, fabricante, classe}."""
    texto = texto.strip()
    if not texto:
        return []
    dados = json.loads(texto)
    if isinstance(dados, dict):          # ConvertTo-Json devolve objeto solto quando há um só
        dados = [dados]
    return [{"id": d.get("InstanceId") or "", "nome": d.get("FriendlyName") or "",
             "fabricante": d.get("Manufacturer") or "", "classe": d.get("Class") or ""} for d in dados]


def parse_wmic(texto):
    """Saída de `wmic ... /format:csv` (com as linhas em branco e \\r que o wmic produz)."""
    linhas = [l for l in texto.replace("\r", "").split("\n") if l.strip()]
    if not linhas:
        return []
    registros = []
    for d in csv.DictReader(io.StringIO("\n".join(linhas))):
        ident = d.get("DeviceID") or ""
        if re.match(r"^(USB|FTDIBUS)\\", ident, re.IGNORECASE):
            registros.append({"id": ident, "nome": d.get("Name") or "",
                              "fabricante": d.get("Manufacturer") or "", "classe": d.get("PNPClass") or ""})
    return registros


def montar_windows(registros, incluir_hubs=False):
    """Agrupa os registros do PnP em aparelhos: o nó USB\\VID_x&PID_y\\serial é o aparelho;
    interfaces (&MI_xx) e nós FTDIBUS só contribuem com a porta COM."""
    aparelhos, filhos = [], []
    for r in registros:
        m = _RE_VIDPID.search(r["id"])
        if not m:
            continue
        vid, pid, resto = m.group(1).lower(), m.group(2).lower(), m.group(3)
        com = _RE_COM.findall(r["nome"])
        if r["id"].upper().startswith("USB\\") and not resto.upper().startswith("&MI_"):
            serial = resto.lstrip("\\")
            if "&" in serial:            # serial inventado pelo Windows (ex.: 5&2D3B1F0&0&3)
                serial = ""
            hub = "HUB" in r["nome"].upper() and r["classe"].upper() == "USB"
            if hub and not incluir_hubs:
                continue
            nome = _RE_COM.sub("", r["nome"]).strip()
            aparelhos.append(Dispositivo(vid=vid, pid=pid, serial=serial, produto=nome,
                                         fabricante=r["fabricante"], portas=com, local=r["id"]))
        else:
            serial = resto.lstrip("+").split("\\")[0] if r["id"].upper().startswith("FTDIBUS") else ""
            filhos.append((vid, pid, serial, com))

    for vid, pid, serial, com in filhos:
        candidatos = [a for a in aparelhos if a.vid == vid and a.pid == pid]
        dono = next((a for a in candidatos if serial and a.serial and serial.startswith(a.serial)), None)
        dono = dono or (candidatos[0] if candidatos else None)
        if dono is not None:
            dono.portas.extend(p for p in com if p not in dono.portas)
    return aparelhos


def listar_windows(executar=None, incluir_hubs=False):
    executar = executar or _executar
    try:
        registros = parse_powershell(executar(COMANDO_POWERSHELL))
    except (OSError, ValueError, subprocess.SubprocessError):
        registros = parse_wmic(executar(COMANDO_WMIC))
    return montar_windows(registros, incluir_hubs)


def _executar(comando):
    r = subprocess.run(comando, capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace", check=True)
    return r.stdout
