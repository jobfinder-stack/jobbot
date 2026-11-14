"""Daily remote-job digest -> Gmail. Runs on GitHub Actions (free)."""
import json, os, smtplib, html
from email.message import EmailMessage
from pathlib import Path

import requests

GMAIL = os.environ["GMAIL_ADDRESS"]
GMAIL_PW = os.environ["GMAIL_APP_PASSWORD"]
TO = [e.strip() for e in (os.environ.get("RECIPIENT") or GMAIL).split(",") if e.strip()]  # comma-separated list
KEYWORDS = [k.strip().lower() for k in os.environ.get("KEYWORDS", "python").split(",") if k.strip()]
SEEN = Path("seen_jobs.json")
MAX_JOBS = 40


def remotive(kw):
    r = requests.get("https://remotive.com/api/remote-jobs",
                     params={"search": kw, "limit": 50}, timeout=30)
    for j in r.json().get("jobs", []):
        yield {"title": j["title"], "company": j["company_name"], "url": j["url"],
               "location": j.get("candidate_required_location") or "Anywhere",
               "text": f'{j["title"]} {" ".join(j.get("tags", []))}'.lower(), "source": "Remotive"}


def remoteok():
    r = requests.get("https://remoteok.com/api", headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    for j in r.json()[1:]:
        yield {"title": j.get("position", ""), "company": j.get("company", ""), "url": j.get("url", ""),
               "location": j.get("location") or "Anywhere",
               "text": f'{j.get("position","")} {" ".join(j.get("tags", []))}'.lower(), "source": "RemoteOK"}


def collect():
    found = []
    for kw in KEYWORDS:
        try:
            found += list(remotive(kw))
        except Exception as e:
            print("Remotive failed:", e)
    try:
        found += list(remoteok())
    except Exception as e:
        print("RemoteOK failed:", e)
    # keep only jobs whose title/tags contain a keyword, drop duplicates
    out, urls = [], set()
    for j in found:
        if j["url"] and j["url"] not in urls and any(k in j["text"] for k in KEYWORDS):
            urls.add(j["url"])
            out.append(j)
    return out


def send(jobs):
    rows = "".join(
        f'<li><a href="{html.escape(j["url"])}"><b>{html.escape(j["title"])}</b></a> - '
        f'{html.escape(j["company"])} ({html.escape(j["location"])}) <i>[{j["source"]}]</i></li>'
        for j in jobs)
    msg = EmailMessage()
    msg["Subject"] = f"{len(jobs)} new remote jobs: {', '.join(KEYWORDS)}"
    msg["From"], msg["To"] = GMAIL, GMAIL  # recipients go in hidden BCC
    msg.set_content("\n".join(f'{j["title"]} - {j["company"]}: {j["url"]}' for j in jobs))
    msg.add_alternative(f"<h3>New remote jobs</h3><ul>{rows}</ul>", subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL, GMAIL_PW)
        s.send_message(msg, to_addrs=TO)


def main():
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    new = [j for j in collect() if j["url"] not in seen][:MAX_JOBS]
    if not new:
        print("No new jobs today.")
        return
    send(new)
    seen.update(j["url"] for j in new)
    SEEN.write_text(json.dumps(sorted(seen)[-2000:]))
    print(f"Emailed {len(new)} jobs.")


if __name__ == "__main__":
    main()
