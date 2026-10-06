"""Create a database-scoped Memoria login; admin credentials stay local."""

import argparse
import json
import os
import secrets
import shlex
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import pymysql
import yaml


class BootstrapError(Exception):
    """An actionable, credential-free initialization error."""


def load_runtime(path: Path) -> dict:
    try:
        runtime = {}
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition("=")
            if not separator:
                raise ValueError("Invalid credential file")
            runtime[key] = json.loads(value)
        required = ["AML_DATABASE_PASSWORD", "AML_MEMORIA_MASTER_KEY", "AML_ADAPTER_API_KEY"]
        if not all(isinstance(runtime.get(key), str) and runtime[key] for key in required):
            raise ValueError("Missing credentials")
        return runtime
    except (OSError, ValueError, TypeError):
        raise BootstrapError(
            "Runtime credential file is unreadable or incomplete. Restore the original "
            "deploy/.runtime.env from a controlled backup; do not delete it and retry."
        ) from None


def write_runtime(path: Path, runtime: dict, *, create: bool = False) -> None:
    content = "".join(f"{key}={json.dumps(value)}\n" for key, value in runtime.items())
    if create:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        return
    # Preserve the previous complete credential file if the process is interrupted.
    descriptor, temporary = tempfile.mkstemp(prefix=".aml-runtime-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_admin(path: Path) -> dict:
    text = path.expanduser().read_text().strip()
    if text.startswith("mysql://"):
        source = text
    else:
        source = ""
        for line in text.splitlines():
            key, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and key in {"DATABASE_URL", "MO_ADMIN_DATABASE_URL"}:
                source = value.strip().strip("\"'")
                break
    if source:
        parsed = urlsplit(source)
        if parsed.scheme != "mysql" or not parsed.hostname or not parsed.username:
            raise ValueError("Admin configuration must be a MySQL connection URL")
        return {
            "host": parsed.hostname, "port": parsed.port or 6001,
            "user": unquote(parsed.username), "password": unquote(parsed.password or ""),
        }
    tokens = shlex.split(text)
    if not tokens or tokens[0] not in {"mysql", "mariadb"}:
        raise ValueError("Provide DATABASE_URL=mysql://... or a mysql CLI connection line")
    values = {}
    flags = {
        "-h": "host", "--host": "host", "-P": "port", "--port": "port",
        "-u": "user", "--user": "user", "-p": "password", "--password": "password",
    }
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in flags and index + 1 < len(tokens):
            values[flags[token]] = tokens[index + 1]
            index += 2
            continue
        for flag, key in flags.items():
            if token.startswith(flag + "="):
                values[key] = token.split("=", 1)[1]
                break
            if len(flag) == 2 and token.startswith(flag) and len(token) > 2:
                values[key] = token[2:]
                break
        index += 1
    if not all(values.get(key) for key in ["host", "user", "password"]):
        raise ValueError("The mysql line must contain host, username and password")
    values["port"] = int(values.get("port", 6001))
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("deploy/.runtime.env"))
    parser.add_argument("--apply", action="store_true", help="Create the login and database")
    args = parser.parse_args()
    config_path = Path(__file__).resolve().parents[1] / "pulumi/Pulumi.new-dev.yaml"
    config = yaml.safe_load(config_path.read_text())["config"]

    def setting(name: str) -> str:
        return str(config[f"memory-benchmarks:{name}"])

    admin = read_admin(args.admin_file)
    if admin["user"].split(":", 1)[0] != setting("databaseAccount"):
        raise ValueError("Admin credentials do not belong to the configured dev tenant")
    if admin["host"] != "freetier-01.cn-hangzhou.cluster.cn-dev.matrixone.tech":
        raise ValueError("Admin connection must use the expected Aliyun dev MatrixOne endpoint")
    if not args.apply:
        print("Configuration parsed. --apply creates aml_memoria and its database-scoped role")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    runtime = load_runtime(args.output) if args.output.exists() else None
    user, role, database = (
        setting("databaseUser"), setting("databaseRole"), setting("databaseName")
    )
    # Names come from the fixed reviewed deployment config, never request input.
    if (user, role, database) != ("aml_memoria", "aml_memoria_role", "aml_memoria"):
        raise ValueError("Database bootstrap only supports the dedicated AML names")
    connection = pymysql.connect(
        **admin, connect_timeout=15, read_timeout=30, write_timeout=30, autocommit=True
    )
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM mo_catalog.mo_user WHERE user_name = %s", (user,)
            )
            user_exists = cursor.fetchone()[0] > 0
            if user_exists and runtime is None:
                raise BootstrapError(
                    "The dedicated SQL user already exists but its runtime credentials are "
                    "missing. No credentials were generated and no database changes were made. "
                    "Restore deploy/.runtime.env from a controlled backup, or explicitly rotate "
                    "the SQL password and synchronize AML_DATABASE_PASSWORD in GitHub and the "
                    "running service. Recover the master key and adapter key separately; "
                    "do not recreate or delete the SQL user/database."
                )
            if runtime is None:
                runtime = {
                    "AML_DATABASE_PASSWORD": secrets.token_urlsafe(32),
                    "AML_MEMORIA_MASTER_KEY": secrets.token_urlsafe(48),
                    "AML_ADAPTER_API_KEY": secrets.token_urlsafe(48),
                }
                write_runtime(args.output, runtime, create=True)
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
            cursor.execute(f"CREATE ROLE IF NOT EXISTS `{role}`")
            if not user_exists:
                # Fail on a concurrent user creation instead of accepting a different password.
                cursor.execute(
                    f"CREATE USER `{user}` IDENTIFIED BY %s DEFAULT ROLE `{role}`",
                    (runtime["AML_DATABASE_PASSWORD"],),
                )
            cursor.execute(f"GRANT ALL ON DATABASE `{database}` TO `{role}`")
            cursor.execute(f"GRANT ALL ON TABLE `{database}`.* TO `{role}`")
            cursor.execute(f"GRANT `{role}` TO `{user}`")
    username = f"{setting('databaseAccount')}:{user}:{role}"
    try:
        app = pymysql.connect(
            host=admin["host"], port=admin["port"], user=username,
            password=runtime["AML_DATABASE_PASSWORD"], database=database,
            connect_timeout=15, read_timeout=30, write_timeout=30, autocommit=True,
        )
    except pymysql.err.MySQLError:
        raise BootstrapError(
            "Dedicated SQL login failed. Keep deploy/.runtime.env, verify its password and "
            "role permissions, and restore known credentials or perform an explicit password "
            "rotation. Synchronize GitHub and the service after rotation; do not delete the "
            "credential file and rerun initialization."
        ) from None
    with app:
        with app.cursor() as cursor:
            cursor.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), DATABASE()")
            row = cursor.fetchone()
            if row[2] != database:
                raise ValueError("The new login did not select the dedicated database")
    url = (
        f"mysql://{quote(username, safe='')}:{quote(runtime['AML_DATABASE_PASSWORD'], safe='')}"
        f"@{admin['host']}:{admin['port']}/{database}"
    )
    runtime["AML_DATABASE_URL"] = url
    write_runtime(args.output, runtime)
    print(f"Dedicated user, role and database created. Credentials saved in {args.output}")


if __name__ == "__main__":
    try:
        main()
    except BootstrapError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        # SQL/driver errors can contain SQL literals or connection credentials.
        code = error.args[0] if error.args and isinstance(error.args[0], int) else "unknown"
        raise SystemExit(
            f"Database bootstrap failed ({type(error).__name__}, code={code}); "
            "credentials were not logged. Preserve deploy/.runtime.env and check connectivity "
            "and permissions; do not delete credentials or reset the existing user automatically."
        ) from None
