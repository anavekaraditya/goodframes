"""Compare the app's picks with a list of the photographer's own keepers."""
import os


def _stem(name):
    return os.path.splitext(os.path.basename(name.strip()))[0].lower()


def read_keepers(path):
    """One filename per line; extensions are ignored so RAW names match their JPEGs."""
    with open(path, encoding="utf-8") as f:
        return {_stem(line) for line in f if line.strip() and not line.startswith("#")}


def evaluate(shoot, keepers):
    """How many keepers the app found, how many extras it picked, and how many keeper moments it covered."""
    by_stem = {_stem(p["file"]): p for p in shoot.photos}
    known = keepers & set(by_stem)
    picks = {_stem(p["file"]) for p in shoot.photos if p["status"] == "pick"}
    found = picks & known
    keeper_moments = {by_stem[k].get("moment") for k in known}
    pick_moments = {by_stem[k].get("moment") for k in picks}
    return {
        "keepers": len(known), "unmatched": sorted(keepers - known), "picks": len(picks),
        "found": len(found), "extras": len(picks - known), "missed": sorted(known - picks),
        "recall": len(found) / len(known) if known else 0.0,
        "precision": len(found) / len(picks) if picks else 0.0,
        "moments_total": len(keeper_moments), "moments_covered": len(keeper_moments & pick_moments),
    }
