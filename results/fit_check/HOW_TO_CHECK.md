# Fit-check frame: what to look at

Print `fit_check_frame.stl` (about 14 ml). Remove supports from the pins and the wall tops.
Fit the Active Cooler to the Pi first, then lower the Pi straight down so the 4 locating pins go
into its mounting holes. Walls are 1.2 mm thick, 0.5 mm from the board edge. Every notch is the generic size:
connector (or cable plug, if the wall sticks out past the connector) + 0.5 mm per side.

Check and write down (add to `notes` in `data/fit_test_results.yaml`, or tell the Fit agent):
1. Does the Pi drop in without forcing? If not, which connector hits which wall?
2. For each notch (USB-C, HDMI0, HDMI1, Ethernet, USB x2, power button, LEDs, microSD): is the connector
   roughly centred, with a visible gap on both sides and below? Note any side that touches.
3. Plug in a real USB-C cable, both micro-HDMI cables, Ethernet, and USB. Does each plug seat fully?
4. Can you press the power button and remove the microSD card?
5. Use a ruler for the height of the cooler top above the PCB top. The model assumes **12.7 mm** (UNVERIFIED).
   Also check the height of the USB stack top above the PCB top. The model assumes **16.2 mm** (from the STEP).
