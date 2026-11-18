"""Ana makine (kontrol edilecek bilgisayar).  Çalıştırma:  python -m pyremote.host"""
import argparse
import asyncio
import getpass
import logging

from .. import fix_console
from .. import crypto
from ..config import Config, format_id
from .backend import default_backend
from .service import HostService


def main():
    ap = argparse.ArgumentParser(description="PyRemote ana makine")
    ap.add_argument("--relay", help="aktarma sunucusu adresi (ör. wss://ornek.com); kaydedilir")
    ap.add_argument("--console", action="store_true", help="pencere açmadan konsolda çalış")
    ap.add_argument("--set-password", action="store_true",
                    help="gözetimsiz erişim için kalıcı parola ayarla ve çık")
    ap.add_argument("--clear-password", action="store_true", help="kalıcı parolayı kaldır ve çık")
    args = ap.parse_args()
    fix_console()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    config = Config()
    if args.relay:
        config.relay = args.relay
    if args.clear_password:
        config.permanent_key = None
        return print("Kalıcı parola kaldırıldı.")
    if args.set_password:
        pw = getpass.getpass("Kalıcı parola (en az 8 karakter): ")
        if len(pw) < 8 or pw != getpass.getpass("Tekrar: "):
            raise SystemExit("Parola çok kısa ya da eşleşmiyor.")
        config.permanent_key = crypto.stretch_password(pw, config.host_id)
        return print("Kalıcı parola ayarlandı. ID:", format_id(config.host_id))

    backend = default_backend()
    if args.console:
        service = HostService(config, backend)
        print("ID:", format_id(config.host_id))
        print("Tek kullanımlık parola:", service.session_password)
        print("Gözetimsiz erişim:", "açık" if config.permanent_key else "kapalı")
        try:
            asyncio.run(service.run())
        except KeyboardInterrupt:
            pass
    else:
        from .gui import HostApp
        HostApp(config, backend).run()


if __name__ == "__main__":
    main()
