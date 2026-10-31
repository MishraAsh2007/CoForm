// ScienceClaw rectangular_tube; nominal millimeters. Printer process requires validation.
length = 160;
width = 25;
height = 11.2;
thickness = 1.2;
difference() { cube([length,width,height]); translate([-1, thickness, thickness]) cube([length+2, width-2*thickness, height-2*thickness]); }
