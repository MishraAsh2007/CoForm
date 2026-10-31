"""Dump bounding boxes of every solid in an official board STEP file.

Used once to read connector positions/sizes from Raspberry Pi's own STEP model
(see components/sources.md). Output: a JSON list of {name, xmin..zmax}.

Run:  .venv\\Scripts\\python.exe -m cad.inspect_step <file.step> <out.json>
"""
import json
import sys

import cadquery as cq
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.TDF import TDF_LabelSequence
from OCP.TDataStd import TDataStd_Name


def label_name(label):
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return attr.Get().ToExtString()
    return ""


def walk(tool, label, loc, out, prefix=""):
    from OCP.TopLoc import TopLoc_Location
    name = label_name(label)
    if tool.IsReference_s(label):
        ref = label.__class__()
        tool.GetReferredShape_s(label, ref)
        walk(tool, ref, loc * tool.GetLocation_s(label), out, prefix + "/" + name)
        return
    if tool.IsAssembly_s(label):
        kids = TDF_LabelSequence()
        tool.GetComponents_s(label, kids)
        for i in range(1, kids.Length() + 1):
            walk(tool, kids.Value(i), loc, out, prefix + "/" + name)
        return
    shape = tool.GetShape_s(label).Moved(loc)
    bb = cq.Shape.cast(shape).BoundingBox()
    out.append({"name": (prefix + "/" + name).strip("/"),
                "xmin": round(bb.xmin, 2), "xmax": round(bb.xmax, 2),
                "ymin": round(bb.ymin, 2), "ymax": round(bb.ymax, 2),
                "zmin": round(bb.zmin, 2), "zmax": round(bb.zmax, 2)})


def main(step, out_path):
    from OCP.TopLoc import TopLoc_Location
    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.ReadFile(step)
    reader.Transfer(doc)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    roots = TDF_LabelSequence()
    tool.GetFreeShapes(roots)
    out = []
    for i in range(1, roots.Length() + 1):
        walk(tool, roots.Value(i), TopLoc_Location(), out)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"{len(out)} solids -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
