// ScienceClaw nominal U-channel. All dimensions in millimeters.
// Clamp the first clamp_length along x. No holes or clamp reinforcement.
// Print parameters, layer orientation, material batch, and actual dimensions
// must be recorded separately; this file does not prescribe a qualified process.
length = 160;
clamp_length = 25;
base_width = 30;
wall_height = 10;
thickness = 1.2;
assert(length > clamp_length && clamp_length > 0);
assert(base_width > 2 * thickness && thickness > 0 && wall_height >= 0);
union() {
    cube([length, base_width, thickness]);
    if (wall_height > 0) {
        translate([0, 0, thickness]) cube([length, thickness, wall_height]);
        translate([0, base_width - thickness, thickness]) cube([length, thickness, wall_height]);
    }
}
