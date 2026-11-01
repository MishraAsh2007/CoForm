# Loads credentials, document IDs, printer settings, and design constraints.
#
# Every engineering value below marked PLACEHOLDER is for pipeline development
# only. None is a validated engineering recommendation; replace each once chosen.
import os

from dotenv import load_dotenv

load_dotenv()

# Interpreter bundled with FreeCAD (must be able to `import FreeCAD`).
FREECAD_PYTHON = os.getenv("FREECAD_PYTHON", r"C:\Program Files\FreeCAD 1.1\bin\python.exe")

# --- Geometry (mm) -----------------------------------------------------------
# Fixed envelope. PLACEHOLDER.
BRACKET_WIDTH = 40.0        # along X, shared by both plates
ARM_LENGTH = 60.0           # cantilevered horizontal plate, along +Y
WALL_HEIGHT = 60.0          # vertical mounting plate, along +Z
INNER_FILLET_RADIUS = 2.0   # plate-to-plate inner corner; avoids a singular FEA corner
RIB_FILLET_RADIUS = 1.0     # rib-to-plate junction edges; avoids singular FEA stresses

# Adjustable parameter bounds, (min, max). PLACEHOLDER.
PARAMETER_BOUNDS = {
    "plate_thickness": (1.5, 8.0),
    "rib_height": (5.0, 50.0),
    "rib_thickness": (1.5, 10.0),
}

# --- Load case ---------------------------------------------------------------
# The wall plate's back face (Y=0) is fully fixed. A force is applied downward
# (-Z), spread over the arm's end face (Y=ARM_LENGTH). PLACEHOLDER magnitude.
TIP_FORCE_N = 50.0

# --- Material: SLA resin, as printed and post-cured. PLACEHOLDER values. -----
YOUNGS_MODULUS_MPA = 2500.0
POISSON_RATIO = 0.35
TENSILE_STRENGTH_MPA = 50.0
DENSITY_G_PER_MM3 = 1.18e-3

# --- Requirements. PLACEHOLDER. ----------------------------------------------
MAX_TIP_DEFLECTION_MM = 0.5
SAFETY_FACTOR = 2.0         # allowable stress = TENSILE_STRENGTH_MPA / SAFETY_FACTOR

# --- Model-form uncertainty of the L1 analytical model (relative, 1 sigma). --
# Used only when no L1 calibration exists; `python structural_evaluator.py
# calibrate` replaces these with factors fitted to converged L2 FEA.
L1_DEFLECTION_REL_UNCERTAINTY = 0.15
L1_STRESS_REL_UNCERTAINTY = 0.25
L1_CALIBRATION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "data", "l1_calibration.json")
# Reference designs (plate_thickness, rib_height, rib_thickness) for calibration,
# spread over depth ratio (t + h) / (ARM_LENGTH - t) from ~0.2 to ~0.95, with
# rib and plate thickness varied independently so their effects can be separated.
L1_REFERENCE_DESIGNS = [(2.5, 10.0, 2.5), (3.0, 20.0, 3.0), (4.0, 30.0, 5.0),
                        (5.0, 38.0, 4.0), (6.0, 45.0, 8.0),
                        (3.7, 49.1, 3.2), (4.0, 30.0, 2.0), (3.0, 15.0, 8.0),
                        (2.0, 25.0, 7.0), (7.0, 20.0, 2.5), (3.2, 50.0, 2.5)]
# Features of the L2/L1 correction (see structural_evaluator.FEATURES). "auto"
# applies the project rule on every refit: the simplest set (at most two
# features) whose leave-one-out RMSE is within 10% of the best, so each run's
# model fits its own data and updates as FEA arrives (user decision 2026-11-01).
# A dict {quantity: [features] | "auto"} overrides per quantity.
L1_CALIBRATION_FEATURES = "auto"
# Automatic selection needs this many converged FEA rows per fitted coefficient
# (1 feature: 6 rows, 2 features: 9), guarding against fitting noise.
L1_MIN_ROWS_PER_COEFFICIENT = 3

# --- Optimization and selection ----------------------------------------------
PARAMETER_RESOLUTION_MM = 0.1     # candidate dimensions are snapped to this grid
# Optimizers on L1 require value + this many sigmas to be within each limit.
OPT_CONSTRAINT_SIGMAS = 2.0
# A final design needs P(all structural limits met) >= this, from selection-grade
# evidence only (converged FEA, validated calibrated model, or measurements).
SELECTION_MIN_PROBABILITY = 0.95

# --- Level selection ---------------------------------------------------------
# L1 decides a limit on its own only when the value is more than this many
# standard deviations from the limit; otherwise the candidate escalates to L2.
L1_DECISION_SIGMAS = 2.0

# --- Level 2 FEA (gmsh + CalculiX bundled with FreeCAD) ------------------------
FREECAD_BIN = os.path.dirname(FREECAD_PYTHON)
GMSH_EXE = os.getenv("GMSH_EXE", os.path.join(FREECAD_BIN, "gmsh.exe"))
CCX_EXE = os.getenv("CCX_EXE", os.path.join(FREECAD_BIN, "ccx.exe"))
FEA_THREADS = 4
FEA_HALF_MODEL = True           # solve half the bracket using its mirror symmetry (~half the cost)
FEA_COARSE_SIZE_FACTOR = 1.0    # coarsest element size = factor * min(plate, rib thickness)
FEA_REFINEMENT_RATIO = 1.5      # element size divided by this per level; 3 levels
FEA_CURVATURE_ELEMENTS = 8      # elements per full circle on fillets (coarse level), scaled per level
FEA_MAX_ELEMENTS = 200_000      # refuse meshes larger than this (runtime guard; 6/45/8 needs ~158k)
# Peak stress is taken away from idealization singularities: within this distance
# of the fixed face, the loaded face, or the sharp rib-to-plate edges, stresses do
# not converge with refinement and are excluded. The distance used is the larger
# of this minimum and the coarsest element size. PLACEHOLDER.
FEA_SINGULARITY_EXCLUSION_MM = 1.0
# GCI uses the observed convergence order capped at this theoretical order
# (quadratic tetrahedra; Roache), so coincidentally agreeing meshes cannot claim
# near-zero error. Every FEA value also carries at least this relative
# uncertainty, for unstructured-mesh noise that a 3-mesh study cannot see.
FEA_FORMAL_ORDER = 2.0
FEA_MIN_REL_UNCERTAINTY = 0.005
# Oscillatory (non-monotonic) mesh sequences: relative uncertainty = this x the
# spread of the three solutions (Eca & Hoekstra 2014), judged against the same
# thresholds. User decision 2026-11-01, after thin-plate designs agreeing within
# 0.5% were rejected as unconverged.
FEA_OSCILLATORY_FACTOR = 3.0
# Converged only if the grid convergence index (fine mesh) is below these.
FEA_GCI_MAX_DEFLECTION = 0.02
# Rib-toe fillet stress reaches ~10% GCI in a ~2 min study; tighter needs 5-8 min.
# Accepted (user decision, 2026-10-31) because each FEA stress carries its GCI as
# uncertainty and selection must use value + uncertainty against the limit.
FEA_GCI_MAX_STRESS = 0.12

# --- Experiment budgets per run. PLACEHOLDER. --------------------------------
BUDGETS = {
    "l1": 500,
    "l2_fea": 20,
    "preform_prep": 20,
    "llm_calls": 60,
}

# --- Printer (PreForm). Codes come from PreForm's print-setting list. --------
# Default matches the scene open in PreForm on 2026-10-31: Form Studio 1 with
# Alpha Black V1 at 0.1 mm. PLACEHOLDER until the printer/resin are confirmed.
PREFORM_PORT = int(os.getenv("PREFORM_PORT", "44388"))
PRINTER_MACHINE_TYPE = "CLRK-1-2"
PRINTER_MATERIAL_CODE = "FLAPBK01"
LAYER_THICKNESS_MM = 0.1
PRINT_SETTING_PROFILE = "DEFAULT"

# Printability pass criteria. The thin-wall threshold is our choice, not a
# structural limit. PLACEHOLDER.
THIN_WALL_THRESHOLD_MM = 1.0
MAX_UNSUPPORTED_PERCENT = 1.0

# --- LLM (Anthropic SDK; credentials from ANTHROPIC_API_KEY or `ant auth login`) ---
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "high")   # low | medium | high | xhigh | max
CLAUDE_MAX_TOKENS = 16000
# Opt-in server-side retry on Anthropic's recommended model if a request is declined.
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
AGENT_MAX_API_CALLS_PER_TURN = 8     # caps one agent's tool-use loop in a round
