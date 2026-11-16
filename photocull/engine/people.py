"""Who is in the shoot: faces grouped by person, so the cull can show who made the picks.

Each face gets a 128-number signature from SFace (OpenCV's face-recognition model, run on this
Mac), computed during the technical pass from the eye, nose and mouth positions Apple Vision
finds. Faces whose signatures are close are the same person. Nobody is named or identified:
a "person" here is only a group of similar faces within one shoot.
"""
import os

import cv2
import numpy as np

MODEL = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "face_recognition_sface_2021dec.onnx")
SAME_PERSON = 0.6    # cosine distance under which two groups of faces are one person
MIN_PHOTOS = 3       # someone seen once or twice is a passer-by (or a false match), not part of the shoot
MAX_FACES = 2500     # the largest faces of a very big shoot; grouping compares every pair
_FAR = 1e6           # "never merge": two faces in one photo are two people

_recognizer = None


def recognizer():
    """The SFace model, or None where it isn't installed."""
    global _recognizer
    if _recognizer is None:
        try:
            _recognizer = cv2.FaceRecognizerSF.create(MODEL, "") if os.path.exists(MODEL) else False
        except (cv2.error, AttributeError):
            _recognizer = False
    return _recognizer or None


def embed(bgr, box, points):
    """A face's signature as a unit vector, or None.

    box: [x, y, w, h]; points: the two eyes, the nose and the two mouth corners as (x, y),
    each pair ordered left to right in the image. All in the pixels of `bgr`.
    """
    rec = recognizer()
    if rec is None or points is None:
        return None
    row = np.array([*box, *(v for p in points for v in p), 1.0], dtype=np.float32)
    try:
        v = rec.feature(rec.alignCrop(bgr, row))[0]
    except cv2.error:
        return None
    norm = float(np.linalg.norm(v))
    return [round(float(x) / norm, 4) for x in v] if norm > 0 else None


def cluster(vectors, owners, limit=SAME_PERSON):
    """Average-linkage grouping of unit vectors. Returns lists of indices, one per group.

    owners: the photo each vector came from. Two faces from one photo are never grouped.
    """
    n = len(vectors)
    if n == 0:
        return []
    v = np.asarray(vectors, dtype=np.float32)
    d = 1 - v @ v.T
    owners = np.asarray(owners)
    d[owners[:, None] == owners[None, :]] = _FAR  # includes the diagonal
    size = np.ones(n, dtype=np.float32)
    members = {i: [i] for i in range(n)}
    while True:
        i, j = divmod(int(np.argmin(d)), n)
        if d[i, j] >= limit:
            break
        merged = (size[i] * d[i] + size[j] * d[j]) / (size[i] + size[j])
        merged[(d[i] >= _FAR) | (d[j] >= _FAR)] = _FAR
        d[i] = d[:, i] = merged
        d[j] = d[:, j] = _FAR
        d[i, i] = _FAR
        size[i] += size[j]
        members[i] += members.pop(j)
    return list(members.values())


def _prominence(face):
    """How good a face is as the picture of its person: Vision's capture quality, then size."""
    return (face.get("quality") or 0, face["box"][2] * face["box"][3])


def group(photos):
    """The people of a shoot, most-photographed first.

    Returns [{"id", "photos": [photo ids], "faces": {photo id: face index}, "cover": [photo id, face index]}].
    """
    faces = [(p["id"], k, f) for p in photos for k, f in enumerate(p["metrics"]["faces"]) if f.get("embedding")]
    faces = sorted(faces, key=lambda t: -t[2]["box"][2] * t[2]["box"][3])[:MAX_FACES]
    groups = cluster([f["embedding"] for _, _, f in faces], [pid for pid, _, _ in faces])
    people = []
    for idx in groups:
        seen = {faces[i][0]: faces[i][1] for i in idx}
        if len(seen) < MIN_PHOTOS:
            continue
        best = max(idx, key=lambda i: _prominence(faces[i][2]))
        people.append({"photos": sorted(seen), "faces": seen, "cover": [faces[best][0], faces[best][1]]})
    people.sort(key=lambda person: (-len(person["photos"]), person["photos"][0]))
    for k, person in enumerate(people, 1):
        person["id"] = k
    return people


def apply_edits(groups, photos, edits):
    """The grouping after the photographer's corrections, which outlive a re-run of the grouping.

    edits: {"names": {face: name}, "hidden": [face], "merges": [[face, face]], "removed": [face]},
    where a face is "<photo fingerprint>:<face index>". A correction follows the person who has
    that face, so it holds even when the groups are numbered differently next time.
    Each person gains "name", "hidden", "merged" and "keys" (their faces, in that form).
    """
    edits = edits or {}
    fp = {p["id"]: p["fp"] for p in photos}
    key = lambda pid, k: f"{fp[pid]}:{k}"
    removed = set(edits.get("removed", []))
    out = []
    for g in groups:
        faces = {pid: k for pid, k in g["faces"].items() if key(pid, k) not in removed}
        if not faces:
            continue
        cover = g["cover"] if faces.get(g["cover"][0]) == g["cover"][1] else [min(faces), faces[min(faces)]]
        out.append({"id": g["id"], "faces": faces, "photos": sorted(faces), "cover": cover,
                    "keys": {key(pid, k) for pid, k in faces.items()}, "name": "", "hidden": False, "merged": False})
    find = lambda face: next((g for g in out if face in g["keys"]), None)
    for a, b in edits.get("merges", []):
        ga, gb = find(a), find(b)
        if ga and gb and ga is not gb:  # b's faces join a; where both are in a photo, a's face stays
            for pid, k in gb["faces"].items():
                ga["faces"].setdefault(pid, k)
            ga["photos"], ga["keys"] = sorted(ga["faces"]), {key(pid, k) for pid, k in ga["faces"].items()}
            ga["merged"] = True
            out.remove(gb)
    for face, name in (edits.get("names") or {}).items():
        if (g := find(face)) is not None:
            g["name"] = name
    for face in edits.get("hidden", []):
        if (g := find(face)) is not None:
            g["hidden"] = True
    out.sort(key=lambda g: (-len(g["photos"]), g["photos"][0]))
    return out


def face_key(photos, person):
    """The face a correction to this person is pinned to: their cover face."""
    pid, k = person["cover"]
    return f"{photos[pid - 1]['fp']}:{k}"
