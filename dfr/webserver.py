"""Flask HTTP server entry point, loopback by default."""
import argparse
import os

from .config import Config


def main():
    parser=argparse.ArgumentParser(prog="dfr-web"); parser.add_argument("--config",default="/etc/dfr/dfr.conf")
    args=parser.parse_args(); config=Config.load(args.config)
    host, port=config.bind.rsplit(":",1)
    host=host.strip("[]")
    if host not in {"127.0.0.1","::1","localhost"}:
        raise SystemExit("dfr-web refuse une écoute réseau sans proxy HTTPS et authentification")
    os.environ["DFR_CONFIG"] = args.config
    from gunicorn.app.wsgiapp import run
    address=f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    run(["--workers", "2", "--threads", "4", "--bind", address, "dfr.web:create_app()"])


if __name__=="__main__": main()
