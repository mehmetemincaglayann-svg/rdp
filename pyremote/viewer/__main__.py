"""İzleyici (kontrol eden bilgisayar).  Çalıştırma:  python -m pyremote.viewer"""
import sys

from .. import fix_console, selftest
from ..config import Config


def main():
    fix_console()
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        raise SystemExit(selftest(sys.argv[2]))
    from .gui import ViewerApp
    ViewerApp(Config()).run()


if __name__ == "__main__":
    main()
