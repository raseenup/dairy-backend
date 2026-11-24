"""Exercise the three staged portals over local HTTPS with an ephemeral PostgreSQL service."""
import hashlib
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import uvicorn
import yaml
from playwright.sync_api import sync_playwright
from starlette.staticfiles import StaticFiles

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
HOSTS = {role: f"{name}.firstmilkventure.com"
         for role, name in (("admin", "admin"), ("customer", "customer"), ("driver", "delivery"))}
API_HOST = "backend.firstmilkventure.com"
EMAILS = {role: f"qa.{role}@example.com" for role in HOSTS}
PHONES = {"admin": "9876511001", "customer": "9876511002", "driver": "9876511003"}


def prepare_database(directory):
    # The integration workflow supplies only a port on loopback. All other connection settings are fixed.
    port = int(os.environ["FMV_QA_POSTGRES_PORT"])
    dsn = f"postgresql://postgres:fmv-local-test@127.0.0.1:{port}/fmv_portal_qa"
    os.environ.update(APP_ENV="test", DATABASE_URL=dsn, FMV_DB_PATH=str(directory / "state.sqlite3"),
                      JWT_SECRET=secrets.token_urlsafe(48), PYTHON_DOTENV_DISABLED="1")
    with psycopg.connect(dsn, autocommit=True) as db:
        # Refuse to use an existing installation or any real application data.
        assert db.execute("SELECT to_regnamespace('fmv_secure')").fetchone() == (None,)
        db.execute("CREATE ROLE anon NOLOGIN")
        db.execute("CREATE ROLE authenticated NOLOGIN")
        db.execute((BACKEND / "migration_v8_security.sql").read_text())
    return dsn


@contextmanager
def serve_locally(api, directory):
    policy = yaml.safe_load((BACKEND / "infra/portal-hosting.yaml").read_text())
    sites = {}
    for service in policy["services"]:
        host = service["domains"][0]
        repo = service["repo"].rsplit("/", 1)[1]
        files = StaticFiles(directory=BACKEND.parent / repo / "public-portal", html=True)
        headers = [(h["name"].lower().encode(), h["value"].encode()) for h in service["headers"]]
        sites[host] = files, headers

    async def serve(scope, receive, send):
        host = dict(scope["headers"])[b"host"].decode().split(":")[0]
        if host == API_HOST:
            return await api(scope, receive, send)
        files, headers = sites[host]

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message["headers"]) + headers
            await send(message)

        await files(scope, receive, send_with_headers)

    key, cert = directory / "local.key", directory / "local.crt"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=*.firstmilkventure.com", "-addext", "subjectAltName=DNS:*.firstmilkventure.com",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        config = uvicorn.Config(serve, interface="asgi3", ssl_keyfile=str(key), ssl_certfile=str(cert),
                                lifespan="off", log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started, "Local portal server failed to start"
            yield sock.getsockname()[1]
        finally:
            server.should_exit = True
            thread.join(timeout=8)


def portal_request(page, path, method="GET", body=None, headers=None):
    options = {"method": method, "headers": headers or {}}
    if body is not None:
        options["body"] = body
    return page.evaluate("([path, options]) => FMV.request(path, options)", [path, options])


def sign(page, role, mode, password, invitation, mailbox):
    page.locator("#fmv-tab-" + mode).click()
    page.locator("#fmv-auth-identifier").fill(EMAILS[role])
    page.locator("#fmv-auth-password").fill(password)
    if mode == "signup":
        page.locator("#fmv-auth-name").fill("QA " + role)
        page.locator("#fmv-auth-phone").fill(PHONES[role])
        page.locator("#fmv-auth-address").fill("QA test address, Kerala 682001")
        if role == "admin":
            page.locator("#fmv-auth-invitation").fill(invitation)

    def submit(path):
        with page.expect_response(lambda r: urlsplit(r.url).path == path and r.request.method == "POST") as event:
            page.locator("#fmv-auth-submit").click()
        assert event.value.status == 200, (role, path, event.value.status)
        return event.value.json()

    submit("/auth/" + mode)
    page.locator("#fmv-auth-otp").fill(mailbox[(role, mode, EMAILS[role])])
    result = submit("/auth/verify-otp")
    assert "access_token" not in result
    if role == "driver" and mode == "signup":
        assert result["pending_approval"]
        print("PASS driver signup requires approval", flush=True)
        return
    page.wait_for_function("role => window.FMV?.user?.portal === role", arg=role)
    assert result["session_id"]
    assert portal_request(page, "/auth/session")["user"]["email"] == EMAILS[role]
    print(f"PASS {role} {mode} and OTP session", flush=True)


def exercise_portals(browser, password, invitation, mailbox):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 428, "height": 850},
                                  service_workers="block")
    context.set_default_timeout(15000)
    allowed = {*HOSTS.values(), API_HOST}

    def allow_local_only(route):
        url = urlsplit(route.request.url)
        if url.scheme == "https" and url.hostname in allowed:
            return route.continue_()
        return route.abort()

    context.route("**/*", allow_local_only)
    pages, errors = {}, []
    for role, host in HOSTS.items():
        page = pages[role] = context.new_page()
        page.on("pageerror", lambda err, r=role: errors.append((r, str(err).splitlines()[0])))
        response = page.goto("https://" + host, wait_until="domcontentloaded")
        assert response.ok and response.headers["strict-transport-security"] == "max-age=31536000"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        sign(page, role, "signup", password, invitation, mailbox)

    admin, customer, driver = (pages[role] for role in ("admin", "customer", "driver"))
    driver_id = next(d["id"] for d in portal_request(admin, "/admin/drivers") if d["email"] == EMAILS["driver"])
    portal_request(admin, f"/admin/drivers/{driver_id}/approve", "POST")
    sign(driver, "driver", "signin", password, invitation, mailbox)
    def session_cookies():
        return [cookie for cookie in context.cookies() if cookie["name"].startswith("__Host-fmv-")]

    assert len(session_cookies()) == 3
    assert all(c["domain"] == API_HOST and c["path"] == "/" and c["secure"] and c["httpOnly"]
               and c["sameSite"] == "Strict" for c in session_cookies())
    for role, page in pages.items():
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("role => window.FMV?.user?.portal === role", arg=role)
        assert portal_request(page, "/sync")["portal"] == role
    print("PASS three simultaneous role sessions survive reload", flush=True)

    media = admin.evaluate("""async () => {
        const response = await fetch('/assets/brand/favicon-32.png');
        const file = new File([await response.blob()], 'qa.png', {type: 'image/png'});
        return FMVUpload.upload(file);
    }""")
    assert media["mime_type"] == "image/png" and media["size"] > 0
    print("PASS authorized campaign upload with row-level security", flush=True)

    def stock():
        return next(p["stock_quantity"] for p in portal_request(customer, "/products") if p["id"] == "prod-4")

    initial_stock = stock()
    order = portal_request(customer, "/orders/checkout", "POST",
                           {"items": [{"product_id": "prod-4", "quantity": 1}], "payment_method": "COD"},
                           {"Idempotency-Key": "qa-order-" + secrets.token_hex(10)})["order"]
    assert stock() == initial_stock - 1
    oid = order["id"]
    portal_request(admin, f"/admin/deliveries/{oid}/assign", "PATCH", {"driver_key": driver_id})
    assert any(o["id"] == oid for o in portal_request(driver, "/driver/deliveries"))
    shift = portal_request(driver, "/driver/shifts/clock-in", "POST",
                           {"vehicle_plate": "KL07QA1234", "odometer_start": 100, "checklist_json":
                            {"tires": True, "brakes": True, "fuel": True, "cold_chain": True}})["shift"]
    portal_request(driver, f"/driver/deliveries/{oid}", "PATCH", {"status": "In Transit"})
    assert next(o["status"] for o in portal_request(customer, "/orders") if o["id"] == oid) == "In Transit"
    portal_request(driver, f"/driver/shifts/{shift['id']}", "PATCH", {"status": "completed", "odometer_end": 104})
    with admin.expect_download() as download:
        admin.evaluate("FMV.download('/admin/attendance/export', 'attendance.csv')")
    assert download.value.failure() is None and download.value.suggested_filename.endswith(".csv")
    print("PASS checkout, stock, dispatch, driver shift and attendance download", flush=True)

    customer.evaluate("FMV.logout()")
    assert len(session_cookies()) == 2
    for role in ("admin", "driver"):
        assert portal_request(pages[role], "/auth/session")["user"]["portal"] == role
    assert not errors, errors
    print("PASS isolated logout and portal JavaScript", flush=True)
    context.close()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        dsn = prepare_database(directory)
        from main import app
        import secure_backend as sec

        mailbox = {}
        def capture_code(email, code, purpose, portal, *args):
            mailbox[(portal, purpose, email)] = code

        sec.send_code = capture_code
        invitation = secrets.token_urlsafe(32)
        password = "Qa!6" + secrets.token_hex(12)
        with sec.transaction() as state:
            state["invites"][hashlib.sha256(invitation.encode()).hexdigest()] = {
                "email": EMAILS["admin"], "role": "super_admin", "expires": time.time() + 900, "used": False}
        with psycopg.connect(dsn) as db:
            rows = db.execute("""
                SELECT c.relname, c.relrowsecurity, p.polpermissive,
                       pg_get_expr(p.polqual, c.oid), pg_get_expr(p.polwithcheck, c.oid),
                       p.polroles @> ARRAY[to_regrole('anon')::oid, to_regrole('authenticated')::oid]
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                LEFT JOIN pg_policy p ON p.polrelid=c.oid AND p.polname='fmv_block_direct_clients'
                WHERE n.nspname='fmv_secure' AND c.relkind IN ('r','p')
            """).fetchall()
            assert {row[0] for row in rows} == {
                "application_state", "campaign_files", "campaign_upload_parts", "driver_private_files",
                "order_chat_files", "product_media", "rate_limits"}
            assert all(row[1:] == (True, False, "false", "false", True) for row in rows)
            print("PASS all seven private tables protected on a fresh install", flush=True)
        with serve_locally(app, directory) as port, sync_playwright() as playwright:
            chrome = shutil.which("google-chrome") or shutil.which("chromium")
            assert chrome, "A local Chrome or Chromium installation is required"
            resolver = f"MAP *.firstmilkventure.com 127.0.0.1:{port}, MAP * ~NOTFOUND, EXCLUDE localhost"
            browser = playwright.chromium.launch(executable_path=chrome, headless=True,
                                                 args=["--no-proxy-server", "--host-resolver-rules=" + resolver])
            exercise_portals(browser, password, invitation, mailbox)
            browser.close()


if __name__ == "__main__":
    main()
