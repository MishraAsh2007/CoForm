# Recording a physical load cycle

The current hardware plan is a 3D-printed U-channel measured by a webcam.
No physical equipment or experimental validation is assumed ready. Use the
explicit simulation mode until fabricated specimens, a stable fixture,
measured loads and calibrated camera measurements are available. For printed
parts, also record material batch, print orientation, layer height, wall count,
infill, supports, postprocessing and any dimensional deviations. The same
measurement contracts also support the proposal's folded-sheet fallback.

Freeze the pilot-selected force, load station, dwell and recovery times,
deflection limit, permanent-set limit, and measurement-quality thresholds
before comparing search methods. Defaults in this prototype are planning
settings, not validated material specifications.

1. Record the specimen ID, candidate ID, actual section dimensions and material
   batch. Keep the same fixture when diagnosing stiffness and root compliance.
2. Fix the camera approximately perpendicular to the plane of bending. Place a
   ruler or calibration target in that plane. Record pixels per metre and the
   calibration uncertainty. Verify a known displacement before collecting data.
3. Track separate fixture, specimen-root, and load-station markers. Record a
   settled unloaded window. Apply a measured centred load, including its holder
   and attachments, then wait the frozen dwell time and record a loaded window.
4. Remove the load, wait the frozen recovery interval, and record a recovered
   window. Save original frames, timestamps, force, position, and operational
   notes. Keep raw files unchanged and reference their stored hashes.
5. Check fixture movement, permanent set, missing markers, and abrupt jumps.
   Retire damaged or slipped specimens from elastic fitting. More camera frames
   do not replace independent load cycles or independent fabricated copies.

Manual ruler readings are supported: record the same metadata, displacement,
residual displacement, fixture movement and a defensible uncertainty estimate.
Record the reading method and operator assistance in `human_notes`. A zero
uncertainty is not allowed. If the available ruler cannot resolve the acceptance
margin, the outcome must remain uncertain.

`classify_observation` uses the absolute displacement plus twice the supplied
standard uncertainty for a passing result. The same guard band applies to
permanent set. A confidence interval crossing a limit returns `uncertain`.
Fixture motion, bad tracking, inadequate wait times, or displacement outside
the small-deformation screen returns `invalid`. A valid diagnostic observation
away from the fixed proof force/position cannot receive a proof-pass label.

Final acceptance requires the protocol's number of **independent copies** of
one nominated candidate. Search and reserved-condition results are excluded.
Repeated cycles count as one specimen and failing repeats are retained. Supply
all run observations to `acceptance_summary` so it can detect reuse of search
specimens. Simulated and physical observations cannot be mixed into a pass.
The result establishes this proof protocol only, not ultimate strength,
fatigue behaviour, or a certified safe working load.

## Offline marker trace format

The initial camera interface consumes existing marker coordinates; automatic
camera capture and marker detection are not implemented yet. Export one CSV
per load cycle, with these exact column names:

```csv
frame,time_s,phase,fixture_x_px,fixture_y_px,root_x_px,root_y_px,load_x_px,load_y_px
1,0.033,unloaded,50,50,60,50,150,50
2,0.067,unloaded,50,50,60,50,150,50
3,0.100,unloaded,50,50,60,50,150,50
4,3.033,loaded,50,50,60,50,150,150
5,3.067,loaded,50,50,60,50,150,150
6,3.100,loaded,50,50,60,50,150,150
7,6.033,recovered,50,50,60,50,150,52
8,6.067,recovered,50,50,60,50,150,52
9,6.100,recovered,50,50,60,50,150,52
```

Coordinates are in pixels, with positive image y downward. Frame/time values
must increase; phases must be ordered `unloaded`, `loaded`, `recovered`, with
at least three valid samples in each. Export only settled measurement windows.
Frame gaps inside a window, missing coordinates and large jumps are flagged.
All numeric values and calibration settings must be finite.

```python
from coform.measurement import analyze_trace_file

result = analyze_trace_file("cycle.csv", pixels_per_m=100_000)
```

The example gives 1 mm downward displacement and 0.02 mm residual. The reducer
subtracts fixture position from load/root positions, but still rejects excessive
absolute fixture movement. It combines per-window noise, calibration scale
uncertainty and a half-pixel resolution floor. It deliberately does not divide
noise by the square root of the video frame count. The estimate does not cover
unmodeled perspective error or fabrication variability; establish those in the
pilot and independent repeats. Metadata such as force, dwell/recovery timing,
geometry, evidence hashes and physical/simulated source must be supplied by
the experiment record rather than inferred from marker coordinates.
