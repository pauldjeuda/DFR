"""dfrd command entry point."""
import argparse
import sys

from .config import Config
from .daemon import DFRDaemon


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dfrd")
    parser.add_argument("--config", default="/etc/dfr/dfr.conf")
    parser.add_argument("--check", action="store_true", help="valide la configuration sans démarrer")
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        if args.check:
            print(f"Configuration valide : nœud={config.node_id} racine={config.root}")
            return 0
        DFRDaemon(config, args.config).run(); return 0
    except Exception as exc:
        print(f"dfrd: {exc}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
