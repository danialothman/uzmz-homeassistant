"""Tests for the USMS client against synthetic pages shaped like the live site."""

from types import SimpleNamespace

import pytest

from uzmz import api
from uzmz.const import ELEM_CAPTCHA_ERROR, FORM_CAPTCHA, FORM_PASSWORD, FORM_USERNAME

BASE_URL = "https://usms.example/SmartMeter/"


def _card(img: str, fields: dict[str, str], extra_first: bool = False) -> str:
    """A DevExpress card: caption spans followed by their value cells."""
    rows = []
    if extra_first:
        # USMS once added a field, shifting every index by one.
        rows.append(("Meter No", "12345"))
    rows += list(fields.items())
    items = "".join(
        f'<tr><td><span id="ASPxCardView1_DXCardLayout0_{i}_Cap">{name}:</span></td>'
        f'<td class="dxflNestedControlCell">  {value}  </td></tr>'
        for i, (name, value) in enumerate(rows)
    )
    return (
        '<td class="dxcvCard"><table>'
        f'<tr><td><img id="ASPxCardView1_DXCardLayout0_img" src="{img}"></td></tr>'
        f"{items}</table></td>"
    )


METER_PAGE = (
    "<html><body><table><tr>"
    + _card(
        "Images/Bulb.png",
        {
            "Meter Status": "ACTIVE",
            "Remaining Unit": "2477.399 kWh",
            "Remaining Balance": "$224.59",
            "Last Updated": "09/10/2026",
        },
        extra_first=True,
    )
    + _card(
        "Images/WaterPipe.png",
        {
            "Meter Status": "ACTIVE",
            "Remaining Unit": "27.667 m³",
            "Remaining Balance": "$3.04",
            "Last Updated": "08/10/2026",
        },
    )
    + "</tr></table></body></html>"
)


def login_page(captcha_visible: bool, action: str = "./ResLogin", extra: str = "") -> str:
    """The ResLogin form; the captcha box is hidden on the first stage."""
    box_style = "" if captcha_visible else ' style="display: none"'
    return f"""
    <html><head>
      <script src="/SmartMeter/DXR.axd?r=1_10,1_253-abc"></script>
      <link href="/SmartMeter/DXR.axd?r=0_2,1_5-def" rel="stylesheet">
      <link href="Content/site.css" rel="stylesheet">
    </head><body>
    <form method="post" action="{action}">
      <input type="hidden" name="__VIEWSTATE" value="vs">
      <input type="hidden" name="__EVENTVALIDATION" value="ev">
      <input type="text" name="{FORM_USERNAME}" value="">
      <input type="password" name="{FORM_PASSWORD}" value="">
      <input type="submit" name="ASPxRoundPanel1$btnLogin" value="Login">
      <div id="ASPxRoundPanel1_ASPxCaptcha1"{box_style}>
        <img id="ASPxRoundPanel1_ASPxCaptcha1_IMG" src="DXB.axd?DXCache=captcha">
        <input type="text" name="{FORM_CAPTCHA}" value="">
        <table><tr><td id="{ELEM_CAPTCHA_ERROR}" style="visibility:hidden">Wrong code</td></tr></table>
      </div>
      {extra}
    </form></body></html>
    """


LOGGED_IN_PAGE = """
<html><body><span id="ASPxPanel1_lblUserName">A USER</span>
<iframe id="MyFrame" src="Default.aspx"></iframe></body></html>
"""


def response(text: str = "", url: str = BASE_URL, content: bytes = b"", headers=None):
    return SimpleNamespace(text=text, url=url, content=content, headers=headers or {})


@pytest.fixture
def client(monkeypatch):
    """A client whose HTTP calls are answered from a queue and recorded."""
    c = api.USMSClient(BASE_URL)
    c.responses = []
    c.requests = []

    def fake_request(method, url, **kwargs):
        c.requests.append((method, url, kwargs))
        return c.responses.pop(0)

    monkeypatch.setattr(c, "_request", fake_request)
    return c


# ----- parsing -----


def test_meter_cards_are_parsed_by_caption():
    data = api._format_meter_data(api._parse_meter_cards(METER_PAGE))
    assert data == {
        "electric": {
            "status": "ACTIVE",
            "remaining_unit": "2477.399 kWh",
            "remaining_balance": "$224.59",
            "last_updated": "09/10/2026",
        },
        "water": {
            "status": "ACTIVE",
            "remaining_unit": "27.667 m³",
            "remaining_balance": "$3.04",
            "last_updated": "08/10/2026",
        },
    }


def test_missing_meter_stays_empty():
    assert api._format_meter_data(api._parse_meter_cards("<html></html>")) == {
        "electric": {},
        "water": {},
    }


def test_devexpress_resource_fields():
    soup = api.BeautifulSoup(login_page(captcha_visible=False), "html.parser")
    assert api._devexpress_resource_fields(soup) == {
        "DXScript": "1_10,1_253",
        "DXCss": "0_2,1_5,Content/site.css",
    }


# ----- login -----


def test_first_stage_has_hidden_captcha(client):
    client.responses = [response(login_page(captcha_visible=False), url=BASE_URL + "ResLogin")]
    assert client.start_login() is None
    assert client._login_form["__VIEWSTATE"] == "vs"
    assert client._login_form["DXScript"] == "1_10,1_253"
    # Buttons aren't carried over from the page; submit_login adds Login itself.
    assert "ASPxRoundPanel1$btnLogin" not in client._login_form
    assert client._login_action == BASE_URL + "ResLogin"


def test_first_stage_redirects_to_captcha(client):
    client.responses = [response(login_page(captcha_visible=False), url=BASE_URL + "ResLogin")]
    client.start_login()
    stage2_url = BASE_URL + "ResLogin?sc=token"
    client.responses = [
        response(login_page(captcha_visible=True, action="./ResLogin?sc=token"), url=stage2_url),
        response(content=b"PNG", headers={"Content-Type": "image/png"}),
    ]

    challenge = client.submit_login("00000000", "secret")

    assert challenge == api.CaptchaChallenge(image=b"PNG", content_type="image/png")
    method, url, kwargs = client.requests[1]
    assert (method, url) == ("post", BASE_URL + "ResLogin")
    assert kwargs["data"][FORM_CAPTCHA] == ""  # stage 1 posts an empty captcha
    assert kwargs["data"]["ASPxRoundPanel1$btnLogin"] == "Login"
    assert client.requests[2][1] == BASE_URL + "DXB.axd?DXCache=captcha"
    assert client._login_action == stage2_url  # stage 2 posts back to the sc URL


def test_second_stage_logs_in(client):
    client.responses = [
        response(login_page(captcha_visible=True), url=BASE_URL + "ResLogin?sc=t"),
        response(content=b"PNG"),
    ]
    client.start_login()
    client.responses = [response(LOGGED_IN_PAGE, url=BASE_URL + "MainPage")]

    assert client.submit_login("00000000", "secret", "AB12C") is None
    assert client.requests[-1][2]["data"][FORM_CAPTCHA] == "AB12C"


def test_wrong_captcha(client):
    client.responses = [response(login_page(captcha_visible=True)), response(content=b"PNG")]
    client.start_login()
    rejected = login_page(captcha_visible=True).replace(
        'style="visibility:hidden">Wrong code', ">Wrong code"
    )
    client.responses = [response(rejected)]

    with pytest.raises(api.InvalidCaptcha):
        client.submit_login("00000000", "secret", "WRONG")


def test_wrong_password_reports_site_message(client):
    client.responses = [response(login_page(captcha_visible=False))]
    client.start_login()
    error = '<span id="pcErr_lblErrMsg">Invalid password. 4 attempts left.</span>'
    client.responses = [response(login_page(captcha_visible=False, extra=error))]

    with pytest.raises(api.InvalidCredentials, match="4 attempts left"):
        client.submit_login("00000000", "wrong")


def test_captcha_is_single_use(client):
    client.responses = [response(login_page(captcha_visible=False))]
    client.start_login()
    client.responses = [response(LOGGED_IN_PAGE)]
    client.submit_login("00000000", "secret")

    with pytest.raises(api.SiteError):
        client.submit_login("00000000", "secret")


# ----- session reads -----


def test_fetch_meter_data(client):
    client.responses = [
        response(LOGGED_IN_PAGE, url=BASE_URL + "MainPage"),
        response(METER_PAGE, url=BASE_URL + "Default.aspx"),
    ]
    data = client.fetch_meter_data()
    assert data["water"]["remaining_balance"] == "$3.04"
    assert [r[1] for r in client.requests] == [BASE_URL + "MainPage", BASE_URL + "Default.aspx"]


def test_expired_session(client):
    client.responses = [response("<html></html>", url=BASE_URL + "SessionExpire")]
    with pytest.raises(api.SessionExpired):
        client.keep_alive()


def test_reads_never_touch_the_login_page(client):
    # Serving the login page ends the USMS session.
    client.responses = [response(LOGGED_IN_PAGE), response(METER_PAGE)]
    client.fetch_meter_data()
    client.responses = [response(LOGGED_IN_PAGE)]
    client.keep_alive()
    assert all("ResLogin" not in url and url != BASE_URL for _, url, _ in client.requests)


def test_restored_cookies_use_site_host():
    c = api.USMSClient(BASE_URL, cookies={"ASP.NET_SessionId": "abc"})
    (cookie,) = list(c.session.cookies)
    assert (cookie.domain, cookie.path) == ("usms.example", "/")
