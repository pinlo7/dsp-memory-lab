"""Find singleton entry classes: types holding static refs to GameData / Player / GameRoot-like roots."""
import sys
import dnfile

ET_SIG = {0x11: "vt", 0x12: "cls"}

def read_compressed_int(b, pos):
    n = b[pos]
    if n & 0x80 == 0:
        return n & 0x7F, pos + 1
    if n & 0xC0 == 0x80:
        return ((n & 0x3F) << 8) | b[pos + 1], pos + 2
    return ((n & 0x3F) << 24) | (b[pos + 1] << 16) | (b[pos + 2] << 8) | b[pos + 3], pos + 4

def field_type_ref(pe, raw):
    if not raw:
        return None
    idx = raw.find(b"\x06")
    if idx < 0:
        return None
    b = raw[idx + 1:]
    if not b:
        return None
    et = b[0]
    if et not in ET_SIG:
        return None
    try:
        word, _ = read_compressed_int(b, 1)
    except IndexError:
        return None
    tag = word & 3
    rid = (word >> 2) - 1
    if rid < 0:
        return None
    if tag == 0:
        row = pe.net.mdtables.TypeDef.rows[rid]
        return (str(row.TypeNamespace or "") + "." + str(row.TypeName or "")).strip(".")
    if tag == 1:
        row = pe.net.mdtables.TypeRef.rows[rid]
        return (str(row.TypeNamespace or "") + "." + str(row.TypeName or "")).strip(".")
    return None

def main():
    path = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else "GameData"
    pe = dnfile.dnPE(path)
    td = pe.net.mdtables.TypeDef
    hits = []
    for row in td.rows:
        full = (str(row.TypeNamespace or "") + "." + str(row.TypeName or "")).strip(".")
        for f in (row.FieldList or []):
            fr = f.row if hasattr(f, "row") else f
            raw = b""
            sig = getattr(fr, "Signature", None)
            if sig is not None:
                rb = getattr(sig, "raw_data", None)
                raw = bytes(rb) if rb else b""
            t = field_type_ref(pe, raw)
            if t and t.endswith(target):
                stat = "static" if bool(getattr(fr.Flags, "fdStatic", False)) else "inst."
                hits.append((full, stat, str(fr.Name), t))
    print(f"== {len(hits)} fields of type *{target} ==")
    for full, stat, fname, t in hits:
        print(f"{full}: {stat} {fname} : {t}")

if __name__ == "__main__":
    main()