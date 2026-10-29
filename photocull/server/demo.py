"""Dev-only stand-in for ClaudeReviewer (PHOTOCULL_DEMO=1): exercises the UI without API spend."""
import random
import time


class DemoReviewer:
    model = "demo"

    def review(self, photos, read_thumb, taste_notes="", final_round=False):
        time.sleep(random.uniform(0.3, 1.0))
        results = {}
        for p in photos:
            score = round(min(9.5, 4 + p["technical"] * 5 + random.uniform(-1, 1)), 1)
            dark = "underexposed" in p.get("notes", [])
            results[p["id"]] = {
                "id": p["id"], "score": score - (1.5 if dark else 0), "reason": "Demo score from technical quality",
                "reject": False, "edit_potential": score + (1.5 if dark else 0.3),
                "fixable_flaws": ["underexposed"] if dark else [],
                "edit_recipe": "Lift exposure +1, open shadows" if dark else "",
            }
        best = max(results.values(), key=lambda r: r["score"])
        return results, ([best["id"]] if best["score"] >= 6 else []), \
            {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
