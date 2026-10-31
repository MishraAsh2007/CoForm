// ScienceClaw support arm: one prismatic section extruded along +X from the clamp end.
// Paste everything BELOW the two header lines Onshape generates in a new Feature Studio
// (keep Onshape's own "FeatureScript NNNN;" and import lines; they set the library version).
// Section matches scienceclaw.mechanics: base width b along Y, total height t + h along Z.

export enum ScienceClawSection
{
    annotation { "Name" : "U-channel" }
    U_CHANNEL,
    annotation { "Name" : "Solid bar" }
    SOLID_BAR,
    annotation { "Name" : "Rectangular tube" }
    RECTANGULAR_TUBE
}

annotation { "Feature Type Name" : "ScienceClaw support arm" }
export const scienceClawSupportArm = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "Section family" }
        definition.family is ScienceClawSection;

        annotation { "Name" : "Total length (#sc_length)" }
        isLength(definition.length, LENGTH_BOUNDS);

        annotation { "Name" : "Base width (#sc_base_width)" }
        isLength(definition.baseWidth, LENGTH_BOUNDS);

        annotation { "Name" : "Wall height (#sc_wall_height)" }
        isLength(definition.wallHeight, NONNEGATIVE_LENGTH_BOUNDS);

        annotation { "Name" : "Thickness (#sc_thickness)" }
        isLength(definition.thickness, LENGTH_BOUNDS);
    }
    {
        const b = definition.baseWidth;
        const h = definition.wallHeight;
        const t = definition.thickness;
        const zero = 0 * meter;
        if (b <= 2 * t)
            throw regenError("Base width must exceed two wall thicknesses", ["baseWidth"]);

        // Sketch plane normal +X; sketch x -> world Y, sketch y -> world Z.
        const sketchPlane = plane(vector(0, 0, 0) * meter, vector(1, 0, 0), vector(0, 1, 0));
        const sketch = newSketchOnPlane(context, id + "section", { "sketchPlane" : sketchPlane });
        var outline;
        if (definition.family == ScienceClawSection.U_CHANNEL && h > zero)
        {
            // Walls stand on top of the full-width base.
            outline = [vector(zero, zero), vector(b, zero), vector(b, t + h), vector(b - t, t + h),
                       vector(b - t, t), vector(t, t), vector(t, t + h), vector(zero, t + h), vector(zero, zero)];
        }
        else
        {
            outline = [vector(zero, zero), vector(b, zero), vector(b, t + h), vector(zero, t + h), vector(zero, zero)];
        }
        skPolyline(sketch, "outline", { "points" : outline });
        if (definition.family == ScienceClawSection.RECTANGULAR_TUBE)
        {
            if (h <= t)
                throw regenError("Tube outside height must exceed two wall thicknesses", ["wallHeight"]);
            skRectangle(sketch, "bore", { "firstCorner" : vector(t, t), "secondCorner" : vector(b - t, h) });
        }
        skSolve(sketch);

        opExtrude(context, id + "extrude", {
                    "entities" : qSketchRegion(id + "section", true),
                    "direction" : vector(1, 0, 0),
                    "endBound" : BoundingType.BLIND,
                    "endDepth" : definition.length
                });
        opDeleteBodies(context, id + "deleteSketch", { "entities" : qCreatedBy(id + "section", EntityType.BODY) });
    });
