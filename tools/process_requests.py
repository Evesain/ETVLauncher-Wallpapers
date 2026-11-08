#!/usr/bin/env python3
"""Run by the new-places workflow. Reads the places that TVs asked for from the Worker, finds photos for each one, opens a pull
request with the new pack, and tells the Worker what happened. A person reviews and merges the pull request.

Environment: EVENTS_URL (the Worker's .../events address), SETUP_KEY, GH_TOKEN (the workflow's own token), GH_REPO.
The Worker holds no GitHub key: this script pulls the queue (the same way the crash-reports workflow does).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "wallpaper_tool.py")


def run(*cmd, check=True):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd[:3]), (r.stderr or r.stdout)[-400:]))
    return r


def worker(base, key, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def pr_body(folder, key):
    place = json.load(open(os.path.join(ROOT, folder, "place.json"), encoding="utf-8"))
    lines = ["A TV asked for photos near cell `%s`. These were picked by the tool, not by a person. **Please check each one before merging:**" % key,
             "", "- it is a view or landmark of the place, not a street, a sign, a building interior or a person",
             "- the day photos look like day and the night photos look like night",
             "- there is no watermark and no text on the picture", "",
             "To remove a photo, delete its file and its entry in `place.json`, then run `python tools/wallpaper_tool.py index`.", "",
             "| File | Title | Credit | Source |", "|---|---|---|---|"]
    for p in place["photos"]:
        lines.append("| %s | %s | %s | [Commons](%s) |" % (p["file"], p["title"], p["credit"], p["source"]))
    return "\n".join(lines)


def main():
    url, key = os.environ.get("EVENTS_URL", ""), os.environ.get("SETUP_KEY", "")
    if not url or not key:
        print("::notice::EVENTS_URL and SETUP_KEY are not set. Nothing to do.")
        return 0
    base = url[:-len("events")] if url.endswith("events") else url
    items = worker(base, key, "wallpapers/pending")["items"]
    if not items:
        print("No new places.")
        return 0
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    acks = []
    for it in items:
        cell = str(it.get("key", ""))
        if not re.fullmatch(r"-?\d+\.\d,-?\d+\.\d", cell):
            continue
        branch = "place/" + cell.replace(",", "_")
        print("== %s (%s)" % (cell, it.get("place", "")))
        try:
            run("git", "checkout", "-q", "main")
            run("git", "pull", "-q", "--ff-only")
            if run("git", "ls-remote", "--exit-code", "--heads", "origin", branch, check=False).returncode == 0:
                print("a branch for this cell exists already")
                acks.append({"key": cell, "status": "done"})
                continue
            run("git", "checkout", "-q", "-B", branch)
            work = tempfile.mkdtemp(prefix="wp-")
            out = run(sys.executable, TOOL, "auto", "--lat", str(it["lat"]), "--lon", str(it["lon"]),
                      "--place", str(it.get("place", "")), "--work", work).stdout
            print(out[-600:])
            status = re.search(r"RESULT=(\w+)", out).group(1)
            if status == "done":
                folder = re.search(r"done: (.+)", out).group(1).strip()
                run("git", "add", "-A")
                run("git", "commit", "-q", "-m", "Add photos for %s" % folder.split("/")[-1])
                run("git", "push", "-q", "-u", "origin", branch)
                body = os.path.join(work, "body.md")
                open(body, "w", encoding="utf-8").write(pr_body(folder, cell))
                try:
                    run("gh", "pr", "create", "--title", "Add photos for %s" % folder.split("/")[-1], "--body-file", body,
                        "--base", "main", "--head", branch)
                except RuntimeError:
                    # No pull request means no review: take the branch away again, so the next run tries the whole place again.
                    run("git", "push", "-q", "origin", "--delete", branch, check=False)
                    raise
                acks.append({"key": cell, "status": "done"})
            elif status == "covered":
                acks.append({"key": cell, "status": "done"})
            else:
                acks.append({"key": cell, "status": "none"})
        except Exception as e:  # leave it queued; the next run tries again
            print("::warning::%s: %s" % (cell, e))
        finally:
            run("git", "checkout", "-q", "-f", "main", check=False)
            run("git", "clean", "-fdq", check=False)
    if acks:
        print(worker(base, key, "wallpapers/ack", {"items": acks}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
