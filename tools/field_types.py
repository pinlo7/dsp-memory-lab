"""Decode field signatures (raw bytes) to readable type names + find fields by name."""
import sys
import dnfile

ET = {
    0x01: "void", 0x02: "bool", 0x03: "char", 0x04: "i1", 0x05: "u1",
    0x06: "i2", 0x07: "u2", 0x08: "i4", 0x09: "u4", 0x0A: "i8",
    0x0B: "u8", 0x0C: "r4", 0x0D: "r8", 0x0E: "string", 0x0F: "ptr",
    0x10: "ref", 0x14: "i", 0x15: "u", 0x16: "object",
}

def resolve_tdor(pe, word):
    tag = word & 3
    rid = (word >> 2) - 1
    if rid < 0:
        return f"?tag{tag}"
    if tag == 0:
        row = pe.net.mdtables.TypeDef.rows[rid]
        return (str(row.TypeNamespace or "") + "." + str(row.TypeName or "")).strip(".")
    if tag == 1:
        row = pe.net.mdtables.TypeRef.rows[rid]
        return (str(row.TypeNamespace or "") + "." + str(row.TypeName or "")).strip(".")
    return f"TypeSpec#{rid}"

def read_compressed_int(b, pos):
    n = b[pos]
    if n & 0x80 == 0:
        return n & 0x7F, pos + 1
    if n & 0xC0 == 0x80:
        return ((n & 0x3F) << 8) | b[pos + 1], pos + 2
    return ((n & 0x3F) << 24) | (b[pos + 1] << 16) | (b[pos + 2] << 8) | b[pos + 3], pos + 4

def decode_field_sig(pe, b):
    """b: raw field signature bytes. Returns readable type string."""
    if not b:
        return "?"
    idx = b.find(b"\x06")
    if idx < 0:
        return "?"
    b = b[idx + 1:]  # skip FIELD calling-convention marker
    pos = 0
    et = b[pos]
    pos += 1
    name = ET.get(et, f"et{et:02x}")
    if et in (0x11, 0x12):  # VALUETYPE / CLASS
        word, pos = read_compressed_int(b, pos)
        return resolve_tdor(pe, word)
    if et == 0x1F:  # SZARRAY
        return decode_field_sig(pe, b[pos:]) + "[]"
    if et == 0x1B:  # GENERICINST
        word, pos = read_compressed_int(b, pos)
        return resolve_tdor(pe, word) + "<...>"
    # remaining bytes (e.g. array rank) have no meaningful type to show
    return name

def main():
    path = sys.argv[1]
    pe = dnfile.dnPE(path)
    td = pe.net.mdtables.TypeDef
    # args after path: either class filter names or --field <name>
    field_filter = None
    class_filters = []
    args = sys.argv[2:]
    if "--field" in args:
        i = args.index("--field")
        field_filter = args[i + 1]
        args = args[:i]
    class_filters = args

    n = 0
    for row in td.rows:
        full = (str(row.TypeNamespace or "") + "." + str(row.TypeName or "")).strip(".")
        if class_filters and not any(f in full for f in class_filters):
            continue
        for f in (row.FieldList or []):
            fr = f.row if hasattr(f, "row") else f
            raw = b""
            sig = getattr(fr, "Signature", None)
            if sig is not None:
                rb = getattr(sig, "raw_data", None)
                raw = bytes(rb) if rb else b""
            t = decode_field_sig(pe, raw)
            if field_filter and field_filter not in str(fr.Name):
                continue
            n += 1
            kind = "static " if bool(getattr(fr.Flags, "fdStatic", False)) else ""
            print(f"{full}: {kind}{fr.Name} : {t}")
    if n == 0:
        print("(no fields matched)")

if __name__ == "__main__":
    main()