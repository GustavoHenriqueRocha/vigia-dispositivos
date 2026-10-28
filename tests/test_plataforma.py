import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from vigia_dispositivos import plataforma

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def montar_sys(raiz):
    """Árvore /sys e /dev falsa: hub raiz, conversor FTDI com by-id, CDC ACM sem by-id,
    teclado sem serial e uma interface (1-1:1.0) que não pode virar aparelho."""
    raiz = Path(raiz)
    sysd, dev = raiz / "sys", raiz / "dev"
    usb = sysd / "devices" / "pci0000:00" / "usb1"
    aparelhos = {
        "usb1": (usb, {"idVendor": "1d6b", "idProduct": "0002", "bDeviceClass": "09", "product": "xHCI Host Controller"}),
        "1-1": (usb / "1-1", {"idVendor": "0403", "idProduct": "6001", "bDeviceClass": "00", "serial": "A10K3XYZ",
                              "product": "FT232R USB UART", "manufacturer": "FTDI"}),
        "1-2": (usb / "1-2", {"idVendor": "2341", "idProduct": "8036", "bDeviceClass": "ef", "serial": "SN0042",
                              "product": "PLC USB", "manufacturer": "Fabricante"}),
        "1-3": (usb / "1-3", {"idVendor": "046D", "idProduct": "C31C", "bDeviceClass": "00", "product": "Keyboard"}),
    }
    links = sysd / "bus" / "usb" / "devices"
    links.mkdir(parents=True)
    for nome, (pasta, arquivos) in aparelhos.items():
        pasta.mkdir(parents=True, exist_ok=True)
        for arq, valor in arquivos.items():
            (pasta / arq).write_text(valor + "\n")
        (links / nome).symlink_to(pasta)
    interface = usb / "1-1" / "1-1:1.0"
    interface.mkdir()
    (links / "1-1:1.0").symlink_to(interface)

    tty = sysd / "class" / "tty"
    tty.mkdir(parents=True)
    for nome, pai in (("ttyUSB0", interface), ("ttyACM0", usb / "1-2" / "1-2:1.0")):
        alvo = pai / nome
        alvo.mkdir(parents=True)
        (tty / nome).mkdir()
        (tty / nome / "device").symlink_to(alvo)
    (tty / "ttyS0").mkdir()      # serial da placa-mãe: ignorada
    (tty / "ttyS0" / "device").symlink_to(sysd / "devices")

    by_id = dev / "serial" / "by-id"
    by_id.mkdir(parents=True)
    (by_id / "usb-FTDI_FT232R_USB_UART_A10K3XYZ-if00-port0").symlink_to("../../ttyUSB0")
    return sysd, dev


class TestLinux(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys, self.dev = montar_sys(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_le_aparelhos_e_liga_portas(self):
        ds = {d.local: d for d in plataforma.listar_linux(self.sys, self.dev)}
        self.assertEqual(sorted(ds), ["1-1", "1-2", "1-3"], "hub e interface ficam de fora")
        ftdi = ds["1-1"]
        self.assertEqual((ftdi.id, ftdi.serial, ftdi.produto, ftdi.fabricante),
                         ("0403:6001", "A10K3XYZ", "FT232R USB UART", "FTDI"))
        self.assertEqual(ftdi.portas, [str(self.dev / "serial/by-id/usb-FTDI_FT232R_USB_UART_A10K3XYZ-if00-port0")])
        self.assertEqual(ds["1-2"].portas, [str(self.dev / "ttyACM0")], "sem by-id, usa /dev/ttyACM0")
        teclado = ds["1-3"]
        self.assertEqual((teclado.id, teclado.serial, teclado.portas), ("046d:c31c", "", []))

    def test_inclui_hubs_quando_pedido(self):
        locais = [d.local for d in plataforma.listar_linux(self.sys, self.dev, incluir_hubs=True)]
        self.assertIn("usb1", locais)

    def test_sem_sys_devolve_lista_vazia(self):
        self.assertEqual(plataforma.listar_linux(Path(self.tmp.name) / "nada", self.dev), [])


class TestWindows(unittest.TestCase):
    def test_powershell(self):
        registros = plataforma.parse_powershell((FIXTURES / "pnp_powershell.json").read_text())
        ds = {d.id: d for d in plataforma.montar_windows(registros)}
        self.assertEqual(sorted(ds), ["0403:6001", "046d:c52b", "1a86:7523", "2341:8036"])
        self.assertEqual(ds["0403:6001"].serial, "A10K3XYZ")
        self.assertEqual(ds["0403:6001"].portas, ["COM3"], "porta do nó FTDIBUS vai para o aparelho")
        self.assertEqual(ds["1a86:7523"].serial, "", "serial inventado pelo Windows é descartado")
        self.assertEqual(ds["1a86:7523"].portas, ["COM4"])
        self.assertEqual(ds["1a86:7523"].produto, "USB-SERIAL CH340")
        self.assertEqual(ds["2341:8036"].portas, ["COM7"], "porta da interface &MI_00")
        self.assertEqual(ds["046d:c52b"].portas, [])

    def test_powershell_com_um_so_dispositivo(self):
        texto = '{"InstanceId":"USB\\\\VID_0403&PID_6015\\\\DQ00ABCD","FriendlyName":"USB Serial Port (COM9)",' \
                '"Manufacturer":"FTDI","Class":"Ports","Status":"OK"}'
        (d,) = plataforma.montar_windows(plataforma.parse_powershell(texto))
        self.assertEqual((d.id, d.serial, d.portas), ("0403:6015", "DQ00ABCD", ["COM9"]))
        self.assertEqual(plataforma.parse_powershell("  "), [])

    def test_wmic(self):
        registros = plataforma.parse_wmic((FIXTURES / "wmic.csv").read_text(newline=""))
        self.assertTrue(all(r["id"].upper().startswith(("USB", "FTDIBUS")) for r in registros))
        ds = {d.id: d for d in plataforma.montar_windows(registros)}
        self.assertEqual(sorted(ds), ["0403:6001", "1a86:7523"])
        self.assertEqual(ds["0403:6001"].portas, ["COM3"])

    def test_cai_para_wmic_quando_powershell_falha(self):
        chamados = []

        def executar(cmd):
            chamados.append(cmd[0])
            if cmd[0] == "powershell":
                raise subprocess.CalledProcessError(1, cmd)
            return (FIXTURES / "wmic.csv").read_text(newline="")

        ds = plataforma.listar_windows(executar)
        self.assertEqual(chamados, ["powershell", "wmic"])
        self.assertEqual(len(ds), 2)

    def test_usa_powershell_quando_funciona(self):
        ds = plataforma.listar_windows(lambda cmd: (FIXTURES / "pnp_powershell.json").read_text())
        self.assertEqual(len(ds), 4)


if __name__ == "__main__":
    unittest.main()
