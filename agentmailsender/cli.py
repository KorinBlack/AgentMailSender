"""Command line: ``agentmailsender serve | create-operator | reset-password``."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from sqlalchemy import func, select

from . import __version__
from .config import Config


def _setup_logging(level: str) -> None:
    logger = logging.getLogger("agentmailsender")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False


def _ask_password(provided: str | None) -> str:
    if provided:
        password = provided
    else:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match.")
    if len(password) < 10:
        sys.exit("The password must be at least 10 characters long.")
    return password


def cmd_serve(args: argparse.Namespace, config: Config) -> None:
    import uvicorn

    from .app import create_app

    host = args.host or config.host
    port = args.port or config.port
    app = create_app(config)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=config.log_level,
        proxy_headers=True,
        forwarded_allow_ips=config.forwarded_allow_ips or "127.0.0.1",
    )


def cmd_create_operator(args: argparse.Namespace, config: Config) -> None:
    from .context import AppContext
    from .errors import ApiError
    from .services import create_operator

    ctx = AppContext(config)
    ctx.init_db(bootstrap=False)
    password = _ask_password(args.password)
    with ctx.session() as db:
        try:
            create_operator(db, args.username, password)
        except ApiError as exc:
            sys.exit(exc.message)
    print(f"Operator {args.username!r} created.")
    ctx.close()


def cmd_reset_password(args: argparse.Namespace, config: Config) -> None:
    from .context import AppContext
    from .models import Operator, OperatorSession
    from .security import hash_password

    ctx = AppContext(config)
    ctx.init_db(bootstrap=False)
    with ctx.session() as db:
        operator = db.scalar(select(Operator).where(func.lower(Operator.username) == args.username.lower()))
        if operator is None:
            sys.exit(f"No operator named {args.username!r}.")
        operator.password_hash = hash_password(_ask_password(args.password))
        db.query(OperatorSession).filter(OperatorSession.operator_id == operator.id).delete()
        db.commit()
    print(f"Password of {args.username!r} updated; existing sessions were signed out.")
    ctx.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agentmailsender", description="Human approval gate for agent e-mail.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the web server (default).")
    serve.add_argument("--host", help="Bind address (default: AMS_HOST or 0.0.0.0).")
    serve.add_argument("--port", type=int, help="Port (default: AMS_PORT or 8080).")

    create = sub.add_parser("create-operator", help="Add an operator account.")
    create.add_argument("username")
    create.add_argument("--password", help="Read from a prompt when omitted.")

    reset = sub.add_parser("reset-password", help="Set a new password for an operator.")
    reset.add_argument("username")
    reset.add_argument("--password", help="Read from a prompt when omitted.")

    args = parser.parse_args(argv)
    config = Config.from_env()
    _setup_logging(config.log_level)
    command = args.command or "serve"
    if command == "serve":
        if not hasattr(args, "host"):
            args.host, args.port = None, None
        cmd_serve(args, config)
    elif command == "create-operator":
        cmd_create_operator(args, config)
    elif command == "reset-password":
        cmd_reset_password(args, config)


if __name__ == "__main__":  # pragma: no cover
    main()
