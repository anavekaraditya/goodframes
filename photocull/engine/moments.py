"""Group photos into 'moments': everything shot of one subject in one go.

Near-duplicate groups (grouping.py) only merge near-identical frames, so reframes of the
same subject - a second angle on the Space Needle, the balloon artist from closer in -
end up in separate groups. A moment joins consecutive frames that are close in time and
still look alike (similar composition or similar colours). The app keeps at most one
pick per moment unless Claude says a moment holds clearly different subjects.
"""
import cv2
import numpy as np

from .grouping import hash_distance

# Deliberately generous (tuned on a hand-labelled 278-photo shoot): merging two subjects
# costs little because Claude names one best frame per distinct subject, while splitting
# one subject across moments yields duplicate picks nobody compares.
MAX_GAP_S = 180      # a pause longer than this always starts a new moment
HASH_CLOSE = 56      # combined pHash+dHash distance (0-128) for "same composition"
HIST_CLOSE = 0.70    # HSV histogram correlation for "same colours/light"
LOOKBACK = 6         # compare with this many recent frames of the moment


def _hist_corr(a, b):
    return float(cv2.compareHist(np.float32(a), np.float32(b), cv2.HISTCMP_CORREL))


def similar(a, b):
    return hash_distance(a, b) <= HASH_CLOSE or _hist_corr(a["hist"], b["hist"]) >= HIST_CLOSE


def build_moments(photos, groups):
    """photos sorted by capture time; groups = near-duplicate index lists.

    Returns a list of moments, each a list of photo indices in time order.
    """
    group_of = {i: g for g, members in enumerate(groups) for i in members}
    moment_of = [0] * len(photos)
    chains = [[]]
    for i, p in enumerate(photos):
        cur = chains[-1]
        if cur:
            prev = photos[cur[-1]]
            gap = p["taken_ts"] - prev["taken_ts"]
            same = gap <= MAX_GAP_S and any(similar(p, photos[j]) for j in cur[-LOOKBACK:])
            if not same:
                chains.append([])
        chains[-1].append(i)
        moment_of[i] = len(chains) - 1

    # A near-duplicate group must never be split across moments (bursts can straddle a
    # boundary when frames are interleaved), so union chains that share a group.
    parent = list(range(len(chains)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    first_chain_of_group = {}
    for i in range(len(photos)):
        g = group_of[i]
        if g in first_chain_of_group:
            a, b = find(first_chain_of_group[g]), find(moment_of[i])
            if a != b:
                parent[max(a, b)] = min(a, b)
        else:
            first_chain_of_group[g] = moment_of[i]
    merged = {}
    for i in range(len(photos)):
        merged.setdefault(find(moment_of[i]), []).append(i)
    return [sorted(m) for _, m in sorted(merged.items())]


def chunks(items, size=12):
    """Split a moment's candidates into review batches of at most `size` images."""
    if len(items) <= size:
        return [items]
    n = -(-len(items) // size)
    step = -(-len(items) // n)
    return [items[k:k + step] for k in range(0, len(items), step)]
