from html.parser import HTMLParser

from sqlalchemy import select

from app.models import Service


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.dialogs = []
        self.scripts = []
        self.images = []
        self.current_form = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current_form = {"attrs": attrs, "inputs": [], "buttons": []}
            self.forms.append(self.current_form)
        elif tag == "dialog":
            self.dialogs.append(attrs)
        elif tag == "script":
            self.scripts.append(attrs)
        elif tag == "img":
            self.images.append(attrs)
        elif self.current_form is not None and tag in {"input", "button"}:
            self.current_form[f"{tag}s"].append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None


def add_trial(client, token, name, price):
    response = client.post("/api/services", data={
        "name": name, "category": "Cloud", "expires_on": "2030-01-01",
        "price_after_trial": price, "csrf_token": token(client.get("/api/services/new")),
    })
    assert response.status_code == 303


def test_dashboard_uses_safe_in_app_confirmation(client, register, token):
    register(client)
    unusual_name = """Trial "A" <img src=x onerror="alert(1)"> & 'B'"""
    add_trial(client, token, unusual_name, "200.00")
    page = client.get("/api/dashboard")
    parser = DashboardParser()
    parser.feed(page.text)
    assert "confirm(" not in page.text
    assert unusual_name not in page.text
    assert not parser.images
    assert len(parser.dialogs) == 1
    dialog = parser.dialogs[0]
    assert dialog["aria-labelledby"] == "delete-trial-title"
    assert "delete-trial-description" in dialog["aria-describedby"]
    assert "does not cancel your subscription" in page.text
    assert any(script.get("src") == "/api/static/delete-confirmation.js" and "defer" in script
               for script in parser.scripts)
    forms = [form for form in parser.forms if "data-trial-name" in form["attrs"]]
    assert len(forms) == 1
    form = forms[0]
    assert form["attrs"]["data-trial-name"] == unusual_name
    assert form["attrs"]["method"] == "post"
    assert form["attrs"]["action"].endswith("/delete")
    assert "onsubmit" not in form["attrs"]
    assert any(field.get("name") == "csrf_token" and field.get("value") for field in form["inputs"])
    assert len(form["buttons"]) == 1
    assert form["buttons"][0]["type"] == "button"
    assert "disabled" in form["buttons"][0]  # Safe until supported JS installs handlers.
    assert "delete-unavailable" in page.text
    assert 'id="delete-trial-cancel"' in page.text and "autofocus" in page.text


def test_confirmed_form_keeps_csrf_and_deletes_only_selected_trial(client, register, token, sessions):
    register(client)
    add_trial(client, token, "Remove this trial", "200.00")
    add_trial(client, token, "Keep this trial", "150.00")
    page = client.get("/api/dashboard")
    assert "₹350.00" in page.text
    parser = DashboardParser()
    parser.feed(page.text)
    form = next(form for form in parser.forms if form["attrs"].get("data-trial-name") == "Remove this trial")
    action = form["attrs"]["action"]
    csrf = next(field["value"] for field in form["inputs"] if field.get("name") == "csrf_token")
    assert client.post(action, data={"csrf_token": "invalid"}).status_code == 403
    with sessions() as db:
        assert len(list(db.scalars(select(Service)))) == 2
    response = client.post(action, data={"csrf_token": csrf})
    assert response.status_code == 303
    assert response.headers["location"] == "/api/dashboard"
    page = client.get(response.headers["location"])
    assert "Remove this trial" not in page.text
    assert "Keep this trial" in page.text and "₹150.00" in page.text
    with sessions() as db:
        assert [trial.name for trial in db.scalars(select(Service))] == ["Keep this trial"]


def test_empty_dashboard_does_not_show_delete_dialog(client, register):
    register(client)
    page = client.get("/api/dashboard")
    assert "delete-trial-dialog" not in page.text
    assert "delete-confirmation.js" not in page.text
