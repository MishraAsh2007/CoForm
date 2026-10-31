"""Part families. get(name) returns the family object; brief["family"] picks it (default: enclosure)."""


def get(name="enclosure"):
    if name in (None, "enclosure"):
        from families.enclosure import EnclosureFamily
        return EnclosureFamily()
    if name == "freeform":
        from families.freeform import FreeformFamily
        return FreeformFamily()
    raise ValueError(f"unknown part family {name}")


def for_brief(brief):
    return get(brief.get("family", "enclosure"))
