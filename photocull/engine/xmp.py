"""Write the cull into XMP sidecars, so Lightroom and Capture One open the shoot already rated.

Picks get 5 stars and a green label, Hidden gems 4 stars and a purple label, rejects the reject
flag (rating -1), with Claude's reason as the caption. Sidecars only reach proprietary RAW files:
editors read JPEG, HEIC and DNG metadata from inside the file, and originals are never modified.

A sidecar that already exists (one Lightroom wrote, with your edits) is backed up once and then
changed in place: only the rating, and the label and caption Goodframes wrote, are touched.
"""
import os
import re
import shutil
import xml.etree.ElementTree as ET

from .ingest import companions
from .raw import RAW_EXTS

SIDECAR_EXTS = RAW_EXTS - {".dng"}
NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
}
for _prefix, _uri in NS.items():  # write the standard prefixes editors expect, not ns0
    ET.register_namespace(_prefix, _uri)
XML_LANG ="{http://www.w3.org/XML/1998/namespace}lang"
RATING, LABEL = f"{{{NS['xmp']}}}Rating", f"{{{NS['xmp']}}}Label"
CAPTION = f"{{{NS['dc']}}}description"
MARKS = {"pick": (5, "Green"), "gem": (4, "Purple"), "rejected": (-1, None)}
EMPTY = (f'<x:xmpmeta xmlns:x="{NS["x"]}"><rdf:RDF xmlns:rdf="{NS["rdf"]}">'
         f'<rdf:Description rdf:about=""/></rdf:RDF></x:xmpmeta>')
PACKET = '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n{}\n<?xpacket end="w"?>\n'


def _caption(p):
    r = p.get("review") or {}
    if p["status"] == "pick":
        rank = f" #{p['rank']}" if p.get("rank") else ""
        return f"Goodframes pick{rank}: {r.get('reason') or p.get('why') or ''}".strip()
    if p["status"] == "gem":
        return f"Goodframes hidden gem: {r.get('reason', '')} Edit: {p.get('why') or ''}".strip()
    return None


def _register(text):
    """Keep the file's own namespace prefixes when it is written back."""
    for prefix, uri in re.findall(r'xmlns:([\w.-]+)="([^"]*)"', text):
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:  # reserved prefix: ElementTree picks its own
            pass


def _get_caption(desc):
    li = desc.find(f"{CAPTION}/rdf:Alt/rdf:li", NS)
    return li.text if li is not None else None


def _apply(text, rating, label, caption, last):
    """The sidecar text with this cull applied. last: what Goodframes wrote here before, if anything."""
    text = re.sub(r"<\?xpacket[^>]*\?>", "", text).strip()
    _register(text)
    root = ET.fromstring(text)
    descs = root.findall(".//rdf:Description", NS)
    if not descs:
        rdf = root.find("rdf:RDF", NS) if root.tag != f"{{{NS['rdf']}}}RDF" else root
        descs = [ET.SubElement(rdf, f"{{{NS['rdf']}}}Description", {f"{{{NS['rdf']}}}about": ""})]
    old_label = next((d.get(LABEL) or d.findtext(LABEL) for d in descs if d.get(LABEL) or d.findtext(LABEL)), None)
    old_caption = next((c for c in map(_get_caption, descs) if c), None)
    for d in descs:  # the value may sit in an attribute or a child element, on any Description
        for key in (RATING, LABEL):
            d.attrib.pop(key, None)
            for child in d.findall(key):
                d.remove(child)
    main = descs[0]
    main.set(RATING, str(rating))
    if old_label and old_label != last.get("label"):  # a label you set yourself wins
        label = old_label
    if label:
        main.set(LABEL, label)
    if not old_caption or old_caption == last.get("caption"):
        for d in descs:
            for child in d.findall(CAPTION):
                d.remove(child)
        if caption:
            alt = ET.SubElement(ET.SubElement(main, CAPTION), f"{{{NS['rdf']}}}Alt")
            ET.SubElement(alt, f"{{{NS['rdf']}}}li", {XML_LANG: "x-default"}).text = caption
    return PACKET.format(ET.tostring(root, encoding="unicode"))


def write_sidecars(shoot):
    """Rate every culled frame's RAW file. Returns counts, and how many frames had no RAW to rate."""
    store, folder = shoot.store, shoot.folder
    manifest = store.get_meta("xmp_manifest", {})
    backup_dir = os.path.join(store.work, "xmp-backup")
    counts = {"pick": 0, "gem": 0, "rejected": 0}
    no_raw = 0
    for p in shoot.photos:
        mark = MARKS.get(p["status"])
        raws = [f for f in [p["file"]] + companions(folder, p["file"])
                if os.path.splitext(f)[1].lower() in SIDECAR_EXTS]
        if not raws:
            no_raw += mark is not None
            continue
        for raw in raws:
            name = os.path.splitext(raw)[0] + ".xmp"
            path, last = os.path.join(folder, name), manifest.get(name)
            if mark is None and last is None:
                continue  # never rated here: leave it alone
            rating, label = mark or (0, None)
            caption = _caption(p) if mark else None
            if os.path.exists(path):
                if last is None:
                    os.makedirs(backup_dir, exist_ok=True)
                    shutil.copy2(path, os.path.join(backup_dir, name))
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            else:
                text = EMPTY
            out = _apply(text, rating, label, caption, last or {})
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                f.write(out)
            os.replace(path + ".tmp", path)
            manifest[name] = {"rating": rating, "label": label, "caption": caption}
        if mark:
            counts[p["status"]] += 1
    store.set_meta("xmp_manifest", manifest)
    return {"picks": counts["pick"], "gems": counts["gem"], "rejects": counts["rejected"], "no_raw": no_raw}
