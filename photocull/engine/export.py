"""Copy picks and Hidden gems out of the shoot. Originals are never moved or modified."""
import os
import shutil

PICKS_DIR = "Picks"
GEMS_DIR = "Hidden Gems"


def _ranked(shoot, status):
    ps = [p for p in shoot.photos if p["status"] == status]
    key = (lambda p: -(p["final"] or 0)) if status == "pick" else \
          (lambda p: -((p.get("review") or {}).get("edit_potential") or 0))
    return sorted(ps, key=key)


def plan(shoot):
    return {"picks": _ranked(shoot, "pick"), "gems": _ranked(shoot, "gem")}


def export(shoot):
    """Write Picks/ and Hidden Gems/ (replacing only files a previous export created)."""
    store, folder = shoot.store, shoot.folder
    for rel in store.get_meta("export_manifest", []):
        path = os.path.join(folder, rel)
        if os.path.isfile(path):
            os.remove(path)
    manifest = []
    out = plan(shoot)
    for sub, items in ((PICKS_DIR, out["picks"]), (GEMS_DIR, out["gems"])):
        if not items:
            continue
        os.makedirs(os.path.join(folder, sub), exist_ok=True)
        width = max(2, len(str(len(items))))
        for rank, p in enumerate(items, 1):
            name = f"{rank:0{width}d}_{p['file']}"
            shutil.copy2(os.path.join(folder, p["file"]), os.path.join(folder, sub, name))
            manifest.append(os.path.join(sub, name))
    if out["gems"]:
        lines = ["Hidden gems - strong photos that need an edit to shine.\n"]
        for rank, p in enumerate(out["gems"], 1):
            r = p.get("review") or {}
            lines.append(f"{rank:02d}_{p['file']}\n  {r.get('reason', '')}\n"
                         f"  Edit: {r.get('edit_recipe') or p.get('why')}\n")
        rel = os.path.join(GEMS_DIR, "edit-recipes.txt")
        with open(os.path.join(folder, rel), "w") as f:
            f.write("\n".join(lines))
        manifest.append(rel)
    store.set_meta("export_manifest", manifest)
    return {"picks": len(out["picks"]), "gems": len(out["gems"]),
            "picks_dir": os.path.join(folder, PICKS_DIR), "gems_dir": os.path.join(folder, GEMS_DIR)}
