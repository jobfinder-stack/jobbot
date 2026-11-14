"""Telegram job bot that runs on GitHub Actions (free).
Each run answers the messages that arrived since the last run, then exits."""
import html, json, os, re, smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import requests

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GMAIL = os.environ["GMAIL_ADDRESS"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
API = f"https://api.telegram.org/bot{TOKEN}"
STATE = Path("state.json")
EMAILS_PER_DAY = 5  # per chat, so strangers can't use your Gmail to spam

HELP = ("Commands:\n/search python developer - find jobs by keywords\n"
        "/categories - browse by category\n/category data - browse a category\n"
        "/category software-dev react - category + keyword\n"
        "/setemail you@gmail.com - get results by email too")

FALLBACK_CATS = [("software-dev", "Software Development"), ("customer-support", "Customer Support"),
                 ("design", "Design"), ("marketing", "Marketing"), ("sales", "Sales"),
                 ("product", "Product"), ("business", "Business"), ("data", "Data"),
                 ("devops", "DevOps / Sysadmin"), ("finance-legal", "Finance / Legal"),
                 ("human-resources", "Human Resources"), ("qa", "QA"), ("writing", "Writing"),
                 ("all-others", "All Others")]


def tg(method, **params):
    return requests.post(f"{API}/{method}", json=params, timeout=30).json()


def say(chat, text, markup=None):
    p = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if markup:
        p["reply_markup"] = markup
    tg("sendMessage", **p)


# ---------- jobs ----------
def get_categories():
    try:
        r = requests.get("https://remotive.com/api/remote-jobs/categories", timeout=15)
        return [(c["slug"], c["name"]) for c in r.json().get("jobs", [])] or FALLBACK_CATS
    except Exception:
        return FALLBACK_CATS


def resolve_category(text):
    text = text.lower().strip()
    cats = get_categories()
    for slug, name in cats:
        if text in (slug.lower(), name.lower()):
            return slug, name
    for slug, name in cats:
        if text and (text in slug.lower() or text in name.lower()):
            return slug, name
    return None


def find_jobs(q="", category=None):
    jobs = []
    try:
        params = {"limit": 50}
        if q:
            params["search"] = q
        if category:
            params["category"] = category
        r = requests.get("https://remotive.com/api/remote-jobs", params=params, timeout=20)
        for j in r.json().get("jobs", []):
            jobs.append({"title": j["title"], "company": j["company_name"], "url": j["url"],
                         "location": j.get("candidate_required_location") or "Anywhere",
                         "text": f'{j["title"]} {" ".join(j.get("tags", []))} {j.get("category", "")}'.lower(),
                         "source": "Remotive"})
    except Exception as e:
        print("Remotive failed:", e)
    if not category:
        try:
            r = requests.get("https://remoteok.com/api", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            for j in r.json()[1:]:
                jobs.append({"title": j.get("position", ""), "company": j.get("company", ""),
                             "url": j.get("url", ""), "location": j.get("location") or "Anywhere",
                             "text": f'{j.get("position", "")} {" ".join(j.get("tags", []))}'.lower(),
                             "source": "RemoteOK"})
        except Exception as e:
            print("RemoteOK failed:", e)
    words = q.lower().split()
    seen, out = set(), []
    for j in jobs:
        if j["url"] and j["url"] not in seen and all(w in j["text"] for w in words):
            seen.add(j["url"])
            out.append(j)
    return out[:40]


def send_email(to, label, jobs):
    rows = "".join(
        f'<li><a href="{html.escape(j["url"])}"><b>{html.escape(j["title"])}</b></a> - '
        f'{html.escape(j["company"])} ({html.escape(j["location"])}) <i>[{j["source"]}]</i></li>'
        for j in jobs)
    msg = EmailMessage()
    msg["Subject"] = f"Remote jobs: {label} ({len(jobs)} found)"
    msg["From"], msg["To"] = GMAIL, to
    msg.set_content("\n".join(f'{j["title"]} - {j["company"]}: {j["url"]}' for j in jobs))
    msg.add_alternative(f"<h3>Remote jobs: {html.escape(label)}</h3><ul>{rows}</ul>", subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL, GMAIL_PW)
        s.send_message(msg)


# ---------- bot logic ----------
def email_allowed(state, chat):
    q = state["quota"].get(chat, {})
    today = str(date.today())
    if q.get("day") != today:
        q = {"day": today, "n": 0}
    if q["n"] >= EMAILS_PER_DAY:
        return False
    q["n"] += 1
    state["quota"][chat] = q
    return True


def do_search(chat, state, q="", cat=None, cat_name=""):
    label = " + ".join(x for x in [cat_name, q] if x) or "all"
    jobs = find_jobs(q, cat)
    if not jobs:
        return say(chat, f"No jobs found for “{label}”. Try other keywords or /categories.")
    top = "\n".join(f'• {j["title"]} - {j["company"]}\n{j["url"]}' for j in jobs[:5])
    note = "Send /setemail you@gmail.com to get the full list by email."
    addr = state["emails"].get(chat)
    if addr:
        if email_allowed(state, chat):
            try:
                send_email(addr, label, jobs)
                note = f"Emailed {len(jobs)} jobs to {addr}"
            except Exception as e:
                print("email failed:", e)
                note = "Could not send the email right now."
        else:
            note = f"Daily email limit ({EMAILS_PER_DAY}) reached. Try again tomorrow."
    say(chat, f"{top}\n\n{note}")


def handle_text(chat, text, state):
    parts = text.split()
    cmd, args = parts[0].split("@")[0].lower(), parts[1:]
    if cmd in ("/start", "/help"):
        say(chat, HELP)
    elif cmd == "/search":
        if not args:
            return say(chat, "Usage: /search python developer")
        say(chat, f"Searching: {' '.join(args)}…")
        do_search(chat, state, q=" ".join(args))
    elif cmd == "/categories" or (cmd == "/category" and not args):
        cats = get_categories()
        btns = [{"text": n, "callback_data": f"cat:{s}"} for s, n in cats]
        say(chat, "Pick a category:", {"inline_keyboard": [btns[i:i + 2] for i in range(0, len(btns), 2)]})
    elif cmd == "/category":
        found = resolve_category(args[0])
        if not found:
            return say(chat, "Category not found. Send /categories to see them all.")
        say(chat, f"Searching: {found[1]}…")
        do_search(chat, state, q=" ".join(args[1:]), cat=found[0], cat_name=found[1])
    elif cmd == "/setemail":
        if not args or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", args[0]):
            return say(chat, "Usage: /setemail you@gmail.com")
        state["emails"][chat] = args[0]
        say(chat, f"Done. Results will be emailed to {args[0]}")
    else:
        say(chat, HELP)


def process(update, state):
    if "message" in update and update["message"].get("text", "").startswith("/"):
        m = update["message"]
        handle_text(str(m["chat"]["id"]), m["text"], state)
    elif "callback_query" in update:
        cq = update["callback_query"]
        tg("answerCallbackQuery", callback_query_id=cq["id"])
        chat = str(cq["message"]["chat"]["id"])
        slug = cq.get("data", "").split(":", 1)[-1]
        found = resolve_category(slug)
        say(chat, f"Searching: {found[1] if found else slug}…")
        do_search(chat, state, cat=slug, cat_name=found[1] if found else slug)


def main():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state.setdefault("offset", 0)
    state.setdefault("emails", {})
    state.setdefault("quota", {})
    r = tg("getUpdates", offset=state["offset"], timeout=0, limit=20)
    if not r.get("ok"):
        print("getUpdates failed:", r)  # e.g. 409 = another copy of the bot is running
        return
    for u in r["result"]:
        state["offset"] = u["update_id"] + 1
        try:
            process(u, state)
        except Exception as e:
            print("error:", e)
    STATE.write_text(json.dumps(state))
    print(f"Handled {len(r['result'])} updates.")


if __name__ == "__main__":
    main()
