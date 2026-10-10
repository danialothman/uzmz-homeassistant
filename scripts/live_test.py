"""Exercise custom_components/uzmz/api.py against the live USMS site, without HA.

Credentials come from .env (see .env.example). Session state (cookies and the
pending login form) is kept in .live_test/ so each step can run as its own
command, letting a human (or Claude) read the captcha image in between:

    python scripts/live_test.py captcha        # stage 1, saves .live_test/captcha.png
    python scripts/live_test.py login <code>   # logs in, then fetches meter data
    python scripts/live_test.py fetch          # reuses saved session (like an HA restart)
    python scripts/live_test.py keepalive      # touches the session only
    python scripts/live_test.py usage          # live consumption from the usage reports

If USMS rejects the IC number/password, further logins are refused until
.live_test/STOP is deleted, so a typo can't walk the account into lockout.
"""

import datetime as dt
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".live_test"
STATE_FILE = STATE_DIR / "state.json"
STOP_FILE = STATE_DIR / "STOP"
CAPTCHA_FILE = STATE_DIR / "captcha.png"

# Import uzmz.api without running the package __init__ (which needs HA).
_pkg = types.ModuleType("uzmz")
_pkg.__path__ = [str(ROOT / "custom_components" / "uzmz")]
sys.modules["uzmz"] = _pkg

from uzmz.api import InvalidCredentials, USMSClient, USMSError  # noqa: E402
from uzmz.const import DEFAULT_BASE_URL  # noqa: E402


def load_env() -> dict[str, str]:
    env_file = ROOT / ".env"
    if not env_file.exists():
        sys.exit("Missing .env - copy .env.example to .env and fill it in.")
    env = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("USMS_IC_NUMBER", "USMS_PASSWORD"):
        if not env.get(key):
            sys.exit(f"{key} is not set in .env")
    return env


def make_client(env: dict[str, str]) -> USMSClient:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    client = USMSClient(
        env.get("USMS_BASE_URL") or DEFAULT_BASE_URL,
        cookies=state.get("cookies"),
    )
    client._login_form = state.get("login_form")
    client._login_action = state.get("login_action")
    _dump_responses(client)
    return client


def _dump_responses(client: USMSClient) -> None:
    """Save every response to .live_test/<command>_NN_<method>.html for debugging."""
    original = client.session.request
    counter = iter(range(len(list(STATE_DIR.glob("*.html"))) + 1, 1000))

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        STATE_DIR.mkdir(exist_ok=True)
        name = STATE_DIR / f"{sys.argv[1]}_{next(counter):02d}_{method.lower()}.html"
        redirects = " -> ".join(f"{r.status_code} {r.url}" for r in response.history)
        name.write_text(
            f"<!-- {method.upper()} {url} -> {response.status_code} {response.url}"
            f" (redirects: {redirects or 'none'}) -->\n{response.text}",
            encoding="utf-8",
        )
        return response

    client.session.request = request


def save_state(client: USMSClient) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "cookies": client.get_cookies(),
                "login_form": client._login_form,
                "login_action": client._login_action,
                "jar": [
                    {"name": c.name, "value": c.value[:6] + "...", "domain": c.domain,
                     "path": c.path, "domain_specified": c.domain_specified}
                    for c in client.session.cookies
                ],
            }
        )
    )


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("captcha", "login", "fetch", "keepalive", "usage"):
        sys.exit(__doc__)
    command = sys.argv[1]
    env = load_env()
    client = make_client(env)

    try:
        if command == "captcha":
            # Start from a clean session and submit stage 1, like the config flow.
            if STOP_FILE.exists():
                sys.exit(f"Refusing to log in: {STOP_FILE} exists (credentials were rejected).")
            client = USMSClient(env.get("USMS_BASE_URL") or DEFAULT_BASE_URL)
            for old in STATE_DIR.glob("*.html"):
                old.unlink()
            _dump_responses(client)
            challenge = client.start_login()
            if challenge is None:
                print("Login page captcha hidden; submitting stage 1 (IC + password)")
                challenge = client.submit_login(env["USMS_IC_NUMBER"], env["USMS_PASSWORD"])
                if challenge is None:
                    print("Logged in without a captcha")
                    print(json.dumps(client.fetch_meter_data(), indent=2))
                    return
            STATE_DIR.mkdir(exist_ok=True)
            CAPTCHA_FILE.write_bytes(challenge.image)
            print(f"Captcha saved to {CAPTCHA_FILE} ({challenge.content_type})")

        elif command == "login":
            if len(sys.argv) < 3:
                sys.exit("usage: live_test.py login <captcha-code>")
            if STOP_FILE.exists():
                sys.exit(f"Refusing to log in: {STOP_FILE} exists (credentials were rejected).")
            if client._login_form is None:
                sys.exit("No pending captcha - run `live_test.py captcha` first.")
            challenge = client.submit_login(env["USMS_IC_NUMBER"], env["USMS_PASSWORD"], sys.argv[2])
            if challenge is not None:
                CAPTCHA_FILE.write_bytes(challenge.image)
                print(f"Stage accepted; USMS wants another captcha, saved to {CAPTCHA_FILE}.")
                print("Run `live_test.py login <code>` again with the new code.")
                return
            print("Login OK")
            print(json.dumps(client.fetch_meter_data(), indent=2))

        elif command == "fetch":
            print(json.dumps(client.fetch_meter_data(), indent=2))

        elif command == "keepalive":
            client.keep_alive()
            print("Session alive")

        elif command == "usage":
            # Live consumption from the usage reports (the Home cards only
            # show the readings as of login), last 3 days plus today.
            today = dt.date.today()
            for meter, readings in client.fetch_meter_data().items():
                if not readings.get("usage_path"):
                    continue
                usage = client.fetch_usage(
                    readings["usage_path"], today - dt.timedelta(days=3), today, meter == "electric"
                )
                print(f"{meter}: Home shows {readings['remaining_unit']}; reports: {json.dumps(usage)}")

    except InvalidCredentials as err:
        STATE_DIR.mkdir(exist_ok=True)
        STOP_FILE.write_text(str(err))
        print(f"InvalidCredentials: {err}\nFurther logins blocked until {STOP_FILE} is deleted.")
        sys.exit(2)
    except USMSError as err:
        print(f"{type(err).__name__}: {err}")
        sys.exit(1)
    finally:
        save_state(client)


if __name__ == "__main__":
    main()
