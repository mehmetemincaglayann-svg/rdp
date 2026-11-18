"""İzleyici (kontrol eden bilgisayar).  Çalıştırma:  python -m pyremote.viewer"""
import argparse

from ..config import Config
from .gui import ViewerApp


def main():
    ap = argparse.ArgumentParser(description="PyRemote izleyici")
    ap.add_argument("--relay", help="aktarma sunucusu adresi (ör. wss://ornek.com); kaydedilir")
    args = ap.parse_args()
    config = Config()
    if args.relay:
        config.relay = args.relay
    ViewerApp(config).run()


if __name__ == "__main__":
    main()
