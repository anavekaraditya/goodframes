"""Command-line interface; the Claude Code `photo-cull` skill drives the engine through this.

  photocull analyze FOLDER [--genre G]       technical pass + moments + contact sheets
  photocull review FOLDER [--model M]        Claude API review (key from Keychain or ANTHROPIC_API_KEY)
  photocull import-scores FOLDER FILE.json   store scores written by Claude Code instead of the API
  photocull finalize FOLDER [--dry-run]      decide picks/gems and copy them out
  photocull override FOLDER ID ACTION        pick | unpick | gem | ungem | clear
"""
import argparse
import json
import os
import sys

from . import settings
from .engine import export, pipeline, sheets
from .engine.review import RUBRIC_VERSION, ClaudeReviewer
from .engine.store import Store


def _shoot(folder, genre="auto", quiet=False):
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        sys.exit(f"Not a folder: {folder}")

    def on_event(e):
        if not quiet and e["type"] == "ingest" and (e["i"] % 50 == 0 or e["i"] == e["n"]):
            print(f"  analysed {e['i']}/{e['n']}", flush=True)

    shoot = pipeline.analyze(folder, Store(folder), genre, on_event)
    if not shoot.photos:
        sys.exit(f"No JPEG/HEIC photos found in {folder}")
    return shoot


def cmd_analyze(args):
    shoot = _shoot(args.folder, args.genre)
    pending = {m for m, _ in pipeline.pending_batches(shoot)}
    cands = [p for m in shoot.moments if m["id"] in pending for p in pipeline.candidates(shoot, m)]
    out = sheets.contact_sheets(shoot, cands)
    n_rej = sum(p["status"] == "rejected" for p in shoot.photos)
    print(f"{len(shoot.photos)} photos -> {len(shoot.moments)} moments, {n_rej} rejected technically")
    print(f"{len(cands)} candidates in {len(pending)} moments still need review")
    for m in shoot.moments:
        if m["id"] in pending:
            ids = [p["id"] for p in pipeline.candidates(shoot, m)]
            notes = {p["id"]: p["notes"] for p in pipeline.candidates(shoot, m) if p["notes"]}
            print(f"  m{m['id']}: {ids}" + (f"  notes {notes}" if notes else ""))
    print("Contact sheets:")
    for s in out:
        print(f"  {s['path']}")


def cmd_review(args):
    shoot = _shoot(args.folder)
    key = settings.get_api_key()
    if not key:
        sys.exit("No API key: set one in the PhotoCull app or export ANTHROPIC_API_KEY.")
    model = args.model or settings.load()["model"]
    est = pipeline.review_estimate(shoot, model)
    print(f"Reviewing {est['photos']} photos in {est['moments']} moments (~${est['cost']})")

    def on_event(e):
        if e["type"] == "review_done":
            print(f"  moment {e['moment']} done (${e['spent']:.2f} so far)", flush=True)
        elif e["type"] == "review_error":
            print(f"  ! {e['error']}", file=sys.stderr)

    res = pipeline.review(shoot, ClaudeReviewer(key, model, settings.load()["effort"]), on_event)
    print(f"Spent ${res['spent']:.2f}")


def cmd_import(args):
    shoot = _shoot(args.folder, quiet=True)
    with open(args.file) as f:
        data = {int(k): v for k, v in json.load(f).items()}
    items = {}
    for m in shoot.moments:
        cands = [p for p in pipeline.candidates(shoot, m) if p["id"] in data]
        if not cands:
            continue
        flagged = [p["id"] for p in cands if data[p["id"]].get("best")]
        if not flagged:  # default: highest-scoring non-rejected frame of the moment
            ok = [p for p in cands if not data[p["id"]].get("reject")]
            flagged = [max(ok, key=lambda p: data[p["id"]]["score"])["id"]] if ok else []
        for p in cands:
            d = data[p["id"]]
            items[p["fp"]] = {
                "id": p["id"], "score": float(d["score"]), "reason": d.get("reason", ""),
                "reject": bool(d.get("reject")), "best": p["id"] in flagged,
                "edit_potential": float(max(d.get("edit_potential", d["score"]), d["score"])),
                "fixable_flaws": d.get("fixable_flaws", []), "edit_recipe": d.get("edit_recipe", ""),
            }
    shoot.store.put_reviews(items, "claude-code", RUBRIC_VERSION)
    print(f"Imported {len(items)} reviews")


def cmd_finalize(args):
    shoot = _shoot(args.folder, quiet=True)
    if args.min_score is not None:
        s = dict(shoot.store.get_meta("settings", {}), min_score=args.min_score)
        shoot.store.set_meta("settings", s)
        pipeline.decide(shoot)
    out = export.plan(shoot)
    print(f"{len(out['picks'])} picks, {len(out['gems'])} hidden gems from {len(shoot.photos)} photos")
    for i, p in enumerate(out["picks"], 1):
        print(f"  {i:>3}. #{p['id']:<4} {p['file']:<24} {p['final']:.1f}  {p['review']['reason']}")
    for p in out["gems"]:
        print(f"  gem #{p['id']:<4} {p['file']:<24} {p['review']['edit_potential']:.0f}  {p['why']}")
    if not args.dry_run:
        res = export.export(shoot)
        print(f"Copied to {res['picks_dir']}" + (f" and {res['gems_dir']}" if res["gems"] else ""))


def cmd_override(args):
    shoot = _shoot(args.folder, quiet=True)
    s = pipeline.set_override(shoot, args.id, None if args.action == "clear" else args.action)
    print(f"#{s['id']} is now {s['status']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="photocull", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("folder")
    a.add_argument("--genre", default="auto", choices=["auto", "portrait", "street", "landscape"])
    r = sub.add_parser("review")
    r.add_argument("folder")
    r.add_argument("--model")
    i = sub.add_parser("import-scores")
    i.add_argument("folder")
    i.add_argument("file")
    f = sub.add_parser("finalize")
    f.add_argument("folder")
    f.add_argument("--dry-run", action="store_true")
    f.add_argument("--min-score", type=float)
    o = sub.add_parser("override")
    o.add_argument("folder")
    o.add_argument("id", type=int)
    o.add_argument("action", choices=["pick", "unpick", "gem", "ungem", "clear"])
    args = ap.parse_args(argv)
    {"analyze": cmd_analyze, "review": cmd_review, "import-scores": cmd_import,
     "finalize": cmd_finalize, "override": cmd_override}[args.cmd](args)


if __name__ == "__main__":
    main()
