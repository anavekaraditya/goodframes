"""Cluster near-duplicate frames (bursts, slight reframes) with perceptual hashes + capture time."""
import imagehash


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def hash_distance(a, b):
    """Combined pHash + dHash Hamming distance (0-128)."""
    pa, da = imagehash.hex_to_hash(a["phash"]), imagehash.hex_to_hash(a["dhash"])
    pb, db = imagehash.hex_to_hash(b["phash"]), imagehash.hex_to_hash(b["dhash"])
    return (pa - pb) + (da - db)


def group(photos, burst_seconds=3.0, strict=20, loose=40):
    """Return a list of groups (lists of photo indices).

    Two photos are similar if their combined hash distance is <= `strict`, or
    <= `loose` when they were also shot within `burst_seconds` of each other
    (bursts with small subject movement drift further in hash space).
    """
    n = len(photos)
    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            d = hash_distance(photos[i], photos[j])
            ti, tj = photos[i].get("taken_ts"), photos[j].get("taken_ts")
            close_in_time = ti is not None and tj is not None and abs(ti - tj) <= burst_seconds
            if d <= strict or (close_in_time and d <= loose):
                uf.union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: min(g))
