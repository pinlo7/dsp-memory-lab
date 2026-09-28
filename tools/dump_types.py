"""Parse Assembly-CSharp.dll metadata and list types/fields matching keywords."""
import sys
import dnfile

def field_static(flags):
    return bool(getattr(flags, "fdStatic", False))

def main():
    path = sys.argv[1]
    args = sys.argv[2:] or ["Package", "Player", "GameData", "Goods", "Item", "Inventory"]
    exact = [k[1:] for k in args if k.startswith("=")]
    keywords = [k for k in args if not k.startswith("=")]
    pe = dnfile.dnPE(path)
    td = pe.net.mdtables.TypeDef

    hits = []
    for row in td.rows:
        ns = str(row.TypeNamespace or "")
        name = str(row.TypeName or "")
        full = (ns + "." + name).strip(".")
        if full in exact or any(k.lower() in full.lower() for k in keywords):
            hits.append((full, row))

    print(f"== {len(td.rows)} typedefs total, {len(hits)} match (keywords={keywords}, exact={exact}) ==")
    for full, row in hits:
        flist = row.FieldList or []
        print(f"\n[{full}]  fields={len(flist)}")
        for f in flist:
            fr = f.row if hasattr(f, "row") else f
            sig = getattr(fr, "Signature", None)
            sig_s = str(sig) if sig else ""
            print(f"    {'static ' if field_static(fr.Flags) else ''}{fr.Name}  {sig_s}")

if __name__ == "__main__":
    main()