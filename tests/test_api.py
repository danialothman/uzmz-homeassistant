"""Tests for the USMS client against synthetic pages shaped like the live site."""

from types import SimpleNamespace

import pytest

from uzmz import api
from uzmz.const import ELEM_CAPTCHA_ERROR, FORM_CAPTCHA, FORM_PASSWORD, FORM_USERNAME

BASE_URL = "https://usms.example/SmartMeter/"


def _card(img: str, fields: dict[str, str], extra_first: bool = False, p: str = "") -> str:
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
    links = (
        f'<tr><td><a href="Account?p={p}">Account</a>'
        f'<a href="Report/UsageHistory?p={p}">Usage</a></td></tr>'
        if p
        else ""
    )
    return (
        '<td class="dxcvCard"><table>'
        f'<tr><td><img id="ASPxCardView1_DXCardLayout0_img" src="{img}"></td></tr>'
        f"{items}{links}</table></td>"
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
        p="RUxFQw==",
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
            "usage_path": "Report/UsageHistory?p=RUxFQw==",
        },
        "water": {
            "status": "ACTIVE",
            "remaining_unit": "27.667 m³",
            "remaining_balance": "$3.04",
            "last_updated": "08/10/2026",
            "usage_path": None,
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


# ----- usage reports -----


def report_page(rows: list[tuple[str, str]] = (), error: str = "") -> str:
    """A Usage History page: the search form plus its result grid."""
    data_rows = "".join(
        f'<tr class="dxgvDataRow"><td class="dxgv">{a}</td><td class="dxgv">{b}</td></tr>'
        for a, b in rows
    )
    return f"""
    <html><body><form method="post" action="./UsageHistory?p=RUxFQw%3d%3d">
      <input type="hidden" name="__VIEWSTATE" value="vs">
      <input type="hidden" name="cboType_VI" value="0">
      <input type="text" name="cboType" value="Monthly (Max 6 months)">
      <input type="text" name="cboDateFrom" value="October 2026">
      <input type="text" name="cboDateTo" value="October 2026">
      <input type="submit" name="btnRefresh" value="Search">
      <span id="pcErr_lblErrMsg">{error}</span>
      <table id="ASPxPageControl1_grid_DXMainTable">
        <tr class="dxgvArm"><td></td><td></td></tr>{data_rows}
      </table>
    </form></body></html>
    """


def test_usage_combines_daily_and_hourly_reports(client):
    report_url = BASE_URL + "Report/UsageHistory?p=RUxFQw=="
    client.responses = [
        response(report_page(), url=report_url),
        response(report_page([("08/10/2026", "105.547"), ("09/10/2026", "96.426")])),
        response(report_page(), url=report_url),
        response(report_page([("1", "3.920"), ("2", "4.063"), ("3", "1,000.5")])),
    ]

    usage = client.fetch_usage(
        "Report/UsageHistory?p=RUxFQw==", api.dt.date(2026, 10, 8), api.dt.date(2026, 10, 10), hourly=True
    )

    assert usage == {"2026-10-08": 105.547, "2026-10-09": 96.426, "2026-10-10": 1008.483}
    daily = client.requests[1][2]["data"]
    assert (daily["cboType_VI"], daily["btnRefresh"]) == ("1", "Search")
    # The server reads the raw date values: local midnight written as UTC ms.
    assert daily["cboDateFrom_Raw"] == "1791417600000"  # 2026-10-08
    assert daily["cboDateTo$State"] == '{"rawValue":"1791504000000"}'  # 2026-10-09, not today
    assert client.requests[3][2]["data"]["cboType_VI"] == "3"


def test_daily_only_meter_skips_hourly(client):
    client.responses = [response(report_page()), response(report_page([("09/10/2026", "1.9")]))]
    usage = client.fetch_usage(
        "Report/UsageHistory?p=V0FU", api.dt.date(2026, 10, 7), api.dt.date(2026, 10, 10), hourly=False
    )
    assert usage == {"2026-10-09": 1.9}
    assert len(client.requests) == 2


def test_no_daily_request_when_starting_today(client):
    client.responses = [response(report_page()), response(report_page([("1", "2.5")]))]
    today = api.dt.date(2026, 10, 10)
    assert client.fetch_usage("Report/UsageHistory?p=x", today, today, hourly=True) == {"2026-10-10": 2.5}


def test_usage_report_error_is_raised(client):
    client.responses = [
        response(report_page()),
        response(report_page(error="Date To must be smaller than the today's date")),
    ]
    with pytest.raises(api.SiteError, match="smaller than"):
        client.fetch_usage(
            "Report/UsageHistory?p=x", api.dt.date(2026, 10, 7), api.dt.date(2026, 10, 10), hourly=False
        )


def test_usage_report_detects_expired_session(client):
    client.responses = [response("<html></html>", url=BASE_URL + "SessionExpire")]
    with pytest.raises(api.SessionExpired):
        client.fetch_usage(
            "Report/UsageHistory?p=x", api.dt.date(2026, 10, 7), api.dt.date(2026, 10, 10), hourly=False
        )


# ----- hardening -----


def test_login_form_to_another_site_is_refused(client):
    page = login_page(captcha_visible=False, action="https://evil.example/collect")
    client.responses = [response(page, url=BASE_URL + "ResLogin")]
    with pytest.raises(api.SiteError, match="different site"):
        client.start_login()
    assert client._login_action is None


def test_login_form_downgrade_to_http_is_refused(client):
    page = login_page(captcha_visible=False, action="http://usms.example/SmartMeter/ResLogin")
    client.responses = [response(page, url=BASE_URL + "ResLogin")]
    with pytest.raises(api.SiteError):
        client.start_login()


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("image/png", "image/png"),
        ("image/JPEG; charset=binary", "image/jpeg"),
        ("image/png)](https://evil.example)", "image/png"),
        ("text/html", "image/png"),
        (None, "image/png"),
    ],
)
def test_captcha_content_type_is_limited_to_images(header, expected):
    assert api._captcha_content_type(header) == expected
