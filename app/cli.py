"""einvoice command line.

einvoice process FILE...     detect, validate, parse and store structured invoices now
einvoice watch DIR           queue every new file that appears in a folder
einvoice requeue             queue documents that were stored but never processed
einvoice texts-backfill [--apply]
                             codes for texts stored before they had any (app/texts_backfill.py),
                             so the UI shows them in German; counts only without --apply
einvoice user-add EMAIL --name NAME --role ROLE [--password]
                             add a user; --password asks for one (the built-in admin),
                             without it they sign in through the identity provider;
                             for a user who exists, give them ROLE in the tenant
einvoice token-create EMAIL --name NAME
                             make an API token for scripts; printed once
einvoice token-revoke TOKEN  stop a token from working
einvoice tenant-add SLUG --name NAME
                             add a company, with no ERP and no connectors yet
einvoice tenant-set SLUG KEY=VALUE...
                             change a company's settings (app/tenants.py); KEY= clears
einvoice tenant-show SLUG    print a company's settings
einvoice key-generate        print a new MASTER_KEY for .env (encryption at rest, app/crypto.py)
einvoice seal [--apply] [--open]
                             seal the originals and invoice data stored before encryption
                             (app/sealing.py);
                             --open stores them plain again; counts only without --apply

process, watch, user-add and token-create act for one tenant: --tenant SLUG,
"default" if not given.
"""

import argparse
import getpass
import json
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app import auth, crypto, db, tenants
from app.db.engine import database_engine
from app.db.rls import tenant_transaction
from app.ingest import requeue_received, scan_folder
from app.pipeline import DocumentStatus, Outcome, Validator, process_structured
from app.queue import JobQueue, RqQueue
from app.sealing import seal_documents
from app.settings import Settings
from app.texts_backfill import backfill
from app.validate import KositValidator


def main(
    argv: Sequence[str] | None = None,
    *,
    validator: Validator | None = None,
    session_factory: sessionmaker[Session] | None = None,
    queue: JobQueue | None = None,
    password_prompt: Callable[[], str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="einvoice")
    commands = parser.add_subparsers(dest="command", required=True)
    process = commands.add_parser("process", help="process structured e-invoices")
    process.add_argument("paths", nargs="+", type=Path)
    process.add_argument("--no-store", action="store_true", help="do not write to the database")
    watch = commands.add_parser("watch", help="queue new files from a folder")
    for command in (process, watch):
        _tenant_option(command)
    watch.add_argument("folder", type=Path)
    watch.add_argument("--interval", type=float, default=5.0, help="seconds between scans")
    watch.add_argument("--once", action="store_true", help="scan once and exit")
    commands.add_parser("requeue", help="queue stored documents that were never processed")
    backfill = commands.add_parser(
        "texts-backfill",
        help="find the codes of texts stored before they had any, so the UI shows them in German",
    )
    backfill.add_argument("--apply", action="store_true", help="write them (without: only count)")
    commands.add_parser("key-generate", help="print a new MASTER_KEY for .env")
    seal = commands.add_parser("seal", help="seal the originals stored before encryption")
    seal.add_argument("--apply", action="store_true", help="write (without: only count)")
    seal.add_argument("--open", action="store_true", help="store them plain again instead")
    user_add = commands.add_parser("user-add", help="add a user")
    user_add.add_argument("email")
    user_add.add_argument("--name", required=True)
    user_add.add_argument("--role", required=True, choices=[role.value for role in auth.Role])
    user_add.add_argument(
        "--password", action="store_true", help="ask for a password (the built-in admin account)"
    )
    token_create = commands.add_parser("token-create", help="make an API token for a user")
    token_create.add_argument("email")
    token_create.add_argument("--name", required=True, help="what the token is for")
    for command in (user_add, token_create):
        _tenant_option(command)
    token_revoke = commands.add_parser("token-revoke", help="stop a token from working")
    token_revoke.add_argument("token")
    tenant_add = commands.add_parser("tenant-add", help="add a company")
    tenant_add.add_argument("slug")
    tenant_add.add_argument("--name", required=True)
    tenant_set = commands.add_parser("tenant-set", help="change a company's settings")
    tenant_set.add_argument("slug")
    tenant_set.add_argument("assignments", nargs="+", metavar="KEY=VALUE")
    tenant_show = commands.add_parser("tenant-show", help="print a company's settings")
    tenant_show.add_argument("slug")
    args = parser.parse_args(argv)

    if args.command == "key-generate":
        # Printed for the person who asked, once; never stored by us.
        print(crypto.new_master_key())
        return 0
    settings = Settings.from_env()
    if args.command == "seal":
        session_factory = session_factory or _database(settings)
        return _seal(session_factory, apply=args.apply, open_all=args.open)
    if args.command == "texts-backfill":
        session_factory = session_factory or _database(settings)
        return _texts_backfill(session_factory, apply=args.apply)
    if args.command in ("tenant-add", "tenant-set", "tenant-show"):
        session_factory = session_factory or _database(settings)
        return _tenants(args, session_factory, settings)
    if args.command in ("user-add", "token-create", "token-revoke"):
        session_factory = session_factory or _database(settings)
        prompt = password_prompt or (lambda: getpass.getpass("Password (12+ characters): "))
        return _accounts(args, session_factory, prompt)
    if args.command == "watch" or (args.command == "process" and not args.no_store):
        session_factory = session_factory or _database(settings)
        tenant_id = _tenant_id(session_factory, args.tenant)
        if tenant_id is None:
            return 2
    if args.command in ("watch", "requeue"):
        session_factory = session_factory or _database(settings)
        queue = queue or RqQueue(settings.redis_url)
        if args.command == "requeue":
            print(f"queued {requeue_received(session_factory, queue)} documents")
            return 0
        return _watch(args, session_factory, queue, settings, tenant_id)

    if validator is None:
        validator = KositValidator(
            httpx.Client(
                base_url=settings.validator_url, timeout=settings.validator_timeout_seconds
            )
        )
    all_valid = True
    for path in args.paths:
        report = (
            _process_file(path, validator, None, None)
            if args.no_store
            else _process_file(path, validator, session_factory, tenant_id)
        )
        all_valid = all_valid and report["status"] == DocumentStatus.VALID.value
        print(json.dumps(report, ensure_ascii=False))
    return 0 if all_valid else 1


def _database(settings: Settings) -> sessionmaker[Session]:
    """The application's database, with the keys its documents are sealed with."""
    engine = database_engine(settings.database_url)
    crypto.configure_from_env(engine)
    return sessionmaker(engine)


def _seal(session_factory: sessionmaker[Session], *, apply: bool, open_all: bool) -> int:
    report = seal_documents(session_factory, apply=apply, open_all=open_all)
    verb = ("opened" if open_all else "sealed") if apply else ("to open" if open_all else "to seal")
    state = "plain" if open_all else "sealed"
    for slug in report.changed:
        print(f"{slug}: {report.changed[slug]} {verb}, {report.already[slug]} {state} already")
    for place, count in sorted(report.by_place.items()):
        if count:
            print(f"    {place}: {count}")
    if not apply and any(report.changed.values()):
        print("nothing written; run again with --apply to write")
    return 0


def _texts_backfill(session_factory: sessionmaker[Session], *, apply: bool) -> int:
    report = backfill(session_factory, apply=apply)
    places = sorted(set(report.coded) | set(report.english))
    verb = "filled in" if apply else "would fill in"
    for place in places:
        print(f"{place}: {verb} {report.coded[place]}, left in English {report.english[place]}")
        for sentence in report.examples.get(place, []):
            print(f"    left: {sentence}")
    if not places:
        print("nothing to fill in: every stored text has its code")
    elif not apply:
        print("nothing written; run again with --apply to write")
    return 0


def _tenant_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--tenant", default="default", metavar="SLUG", help='the company (default: "default")'
    )


def _tenant_id(session_factory: sessionmaker[Session], slug: str) -> str | None:
    """The tenant's id, or None (and a message) if there is no such tenant."""
    with session_factory() as session:
        try:
            return db.tenant_by_slug(session, slug).id
        except LookupError as exc:
            print(f"einvoice: {exc}", file=sys.stderr)
            return None


def _tenants(args, session_factory: sessionmaker[Session], settings: Settings) -> int:
    try:
        with session_factory.begin() as session:
            if args.command == "tenant-add":
                tenant = tenants.create_tenant(session, args.slug, args.name)
                print(f"added tenant {tenant.slug} ({tenant.name})")
                return 0
            tenant = db.tenant_by_slug(session, args.slug)
            if args.command == "tenant-set":
                changes = tenants.parse_assignments(args.assignments)
                config = tenants.update_config(session, tenant.id, changes, settings)
            else:
                config = tenants.config_for(session, tenant.id, settings)
            print(json.dumps(config.model_dump(mode="json"), indent=1))
    except (ValueError, LookupError) as exc:
        print(f"einvoice: {exc}", file=sys.stderr)
        return 1
    return 0


def _accounts(args, session_factory: sessionmaker[Session], prompt: Callable[[], str]) -> int:
    try:
        with session_factory.begin() as session:
            if args.command == "user-add":
                tenant = db.tenant_by_slug(session, args.tenant)
                user = auth.find_user(session, args.email)
                if user is None:
                    password = prompt() if args.password else None
                    user = auth.create_user(
                        session,
                        args.email,
                        args.name,
                        auth.Role(args.role),
                        password,
                        tenant_id=tenant.id,
                    )
                    print(f"added {user.email} as {args.role} in {tenant.slug}")
                else:
                    # Someone who works for another company too.
                    auth.add_membership(session, user.id, tenant.id, auth.Role(args.role))
                    print(f"{user.email} is now {args.role} in {tenant.slug}")
            elif args.command == "token-create":
                tenant = db.tenant_by_slug(session, args.tenant)
                user = auth.find_user(session, args.email)
                if user is None:
                    raise ValueError(f"no user with the email {args.email}")
                token = auth.create_token(session, user.id, args.name, tenant.id)
                print(f"API token for {user.email} in {tenant.slug} ({args.name}), shown only now:")
                print(token)
            elif not auth.revoke_token(session, args.token):
                raise ValueError("no such token")
            else:
                print("token revoked")
    except (ValueError, LookupError) as exc:
        print(f"einvoice: {exc}", file=sys.stderr)
        return 1
    return 0


def _watch(
    args: argparse.Namespace,
    session_factory: sessionmaker[Session],
    queue: JobQueue,
    settings: Settings,
    tenant_id: str,
) -> int:
    if not args.folder.is_dir():
        print(f"not a folder: {args.folder}", file=sys.stderr)
        return 2
    while True:
        found = scan_folder(
            args.folder, session_factory, queue, settings.max_upload_bytes, tenant_id=tenant_id
        )
        for item in found:
            print(json.dumps({"document_id": item.document_id, "status": item.status}))
        if args.once:
            return 0
        time.sleep(args.interval)


def _process_file(
    path: Path,
    validator: Validator,
    session_factory: sessionmaker[Session] | None,
    tenant_id: str | None,
) -> dict:
    data = path.read_bytes()
    if session_factory is None or tenant_id is None:
        return _report(path, process_structured(data, validator), document_id=None)

    with tenant_transaction(session_factory, tenant_id) as session:
        existing = db.find_document(session, data, tenant_id)
        if existing is not None:
            db.record_duplicate(session, existing, path.name)
            return {
                "file": str(path),
                "document_id": existing.id,
                "status": existing.status,
                "duplicate": True,
            }
        outcome = process_structured(data, validator)
        document = db.store_outcome(session, data, path.name, outcome, tenant_id)
        return _report(path, outcome, document_id=document.id)


def _report(path: Path, outcome: Outcome, document_id: str | None) -> dict:
    validation = outcome.validation
    return {
        "file": str(path),
        "document_id": document_id,
        "format": outcome.format.value,
        "status": outcome.status.value,
        "error": outcome.error,
        "validation": None if validation is None else validation.model_dump(mode="json"),
        "invoice": None if outcome.invoice is None else outcome.invoice.model_dump(mode="json"),
    }


if __name__ == "__main__":
    sys.exit(main())
