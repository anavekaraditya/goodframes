"""Dev-only stand-in for ClaudeReviewer (PHOTOCULL_DEMO=1): exercises the UI without API spend."""
import random
import time


class DemoReviewer:
    model = "demo"  # stands in for both tiers of a two-tier review

    def review(self, photos, read_thumb, taste_notes="", final_round=False, read_crop=None, brief_notes=""):
        time.sleep(random.uniform(0.3, 1.0))
        # Demo tagging: every listed shot but the last is "found" in a few frames, so both states show.
        listed = sum(line.split(". ")[0].isdigit() for line in brief_notes.splitlines())
        results = {}
        for p in photos:
            score = round(min(9.5, 4 + p["technical"] * 5 + random.uniform(-1, 1)), 1)
            dark = "underexposed" in p.get("notes", [])
            results[p["id"]] = {
                "id": p["id"], "score": score - (1.5 if dark else 0), "reason": "Demo score from technical quality",
                "reject": False, "shoot_type": "other", "impact": score, "moment": score, "light": score,
                "composition": score, "subject_focus": score, "edit_potential": score + (1.5 if dark else 0.3),
                "fixable_flaws": ["underexposed"] if dark else [],
                "edit_recipe": "Lift exposure +1, open shadows" if dark else "",
                "shots": [n for n in range(1, listed) if p["id"] % 6 == n],
            }
        best = max(results.values(), key=lambda r: r["score"])
        return results, ([best["id"]] if best["score"] >= 6 else []), \
            {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}

    def final_edit(self, photos, read_preview, taste_notes="", brief_notes=""):
        time.sleep(random.uniform(0.3, 1.0))
        ranked = sorted(photos, key=lambda p: -p["final"])
        ranking = [(p["id"], f"Demo final edit, rank {k}") for k, p in enumerate(ranked, 1)]
        return ranking, max(1, round(len(ranked) * 0.7)), [], {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}

    def brief_chat(self, messages, sample, context):
        time.sleep(0.4)
        said = [m["text"] for m in messages if m["role"] == "user"]
        words = " ".join(said).lower()
        count = next((int(w) for w in words.split() if w.isdigit()), 0)
        reply = ("Demo chat: I can see the sample. What's this shoot, and what matters most to you?" if not said
                 else "Noted in the brief. Anything else, or is it ready to confirm?")
        shots = [s.strip().capitalize() for m in said if m.lower().startswith("shots:") for s in m[6:].split(",")]
        return {"reply": reply, "shoot": said[0] if said else "", "priorities": said[1:], "avoid": [], "look": "",
                **({"shots": shots} if shots else {}),
                "target_count": count, "per_moment": "few" if "several" in words else "one",
                "technical_tolerance": "loose" if "loose" in words else "standard",
                "rerun": "final_edit" if "more" in words else "full_review",
                "style_suggestions": ["Likes grain and motion blur"] if "grain" in words else []}, \
            {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
