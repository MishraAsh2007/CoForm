$ErrorActionPreference = 'Stop'
$workspace = 'C:\Users\Jash_\ScienceClaw'
$buildDir = Join-Path $workspace 'presentation\.build-judge'
$outputDir = Join-Path $workspace 'presentation\judge-ready'
$renderDir = Join-Path $buildDir 'revised-v2'
New-Item -ItemType Directory -Force -Path $outputDir,$renderDir | Out-Null
# v2 adds the photo of the printed backpack holder (slides 1 and 10).
$finalPptx = Join-Path $outputDir 'CoForm_Judge_Ready_v2.pptx'
$finalPdf = Join-Path $outputDir 'CoForm_Judge_Ready_v2.pdf'
if (Test-Path -LiteralPath $finalPptx) { throw 'Use a new output filename rather than overwriting a delivered deck.' }

function RGB([string]$hex) {
    return [Convert]::ToInt32($hex.Substring(0,2),16) + 256*[Convert]::ToInt32($hex.Substring(2,2),16) + 65536*[Convert]::ToInt32($hex.Substring(4,2),16)
}
$bg = RGB '101718'
$fg = RGB 'EFF5F2'
$muted = RGB 'ACBEC1'
$mint = RGB 'B7EBD5'
$blue = RGB '81CDEA'
$amber = RGB 'E5B180'
$grid = RGB '344348'
$rowBg = RGB '172124'
$headBg = RGB '20332D'
$app = New-Object -ComObject PowerPoint.Application
$deck = $app.Presentations.Add(0)
$deck.PageSetup.SlideWidth = 959.976
$deck.PageSetup.SlideHeight = 540

function Text($slide,[string]$value,[double]$x,[double]$y,[double]$w,[double]$h,[double]$size=20,[int]$color=$fg,[bool]$bold=$false) {
    $shape = $slide.Shapes.AddTextbox(1,$x,$y,$w,$h)
    $shape.Name = 'Text ' + $slide.Shapes.Count
    $tf = $shape.TextFrame
    $tf.MarginLeft=0; $tf.MarginRight=0; $tf.MarginTop=0; $tf.MarginBottom=0
    $tf.WordWrap=-1; $tf.AutoSize=0; $tf.VerticalAnchor=1
    $range=$tf.TextRange
    $range.Text=$value
    $range.Font.Name='Arial'; $range.Font.Size=$size; $range.Font.Color.RGB=$color
    $range.Font.Bold=$(if ($bold) {-1} else {0})
    $range.ParagraphFormat.SpaceAfter=0; $range.ParagraphFormat.SpaceBefore=0
    $range.ParagraphFormat.Bullet.Visible=0
    $shape.TextFrame2.AutoSize=0
    $shape.TextFrame2.WordWrap=-1
    $shape.Left=$x; $shape.Top=$y; $shape.Width=$w; $shape.Height=$h
    return $shape
}
function NewSlide([string]$title,[bool]$appendix=$false) {
    $s=$deck.Slides.Add($deck.Slides.Count+1,12)
    $s.FollowMasterBackground=0; $s.Background.Fill.Solid(); $s.Background.Fill.ForeColor.RGB=$bg
    if ($title) { $null=Text $s $title 46 31 868 66 30 $fg $true }
    $footer=if ($appendix) {'CoForm   Supporting evidence'} else {'CoForm   ScienceClaw 2026'}
    $null=Text $s $footer 46 511 770 16 9.5 $muted
    $null=Text $s ([string]$s.SlideIndex) 891 511 23 16 9.5 $muted
    return $s
}
function Clean([string]$value) {
    # House style: no semicolons or dashes. In source lists they become commas, between clauses a full stop.
    $value=[regex]::Replace($value,'\s*;\s*(?=[^\s;]*[/.])',', ')
    $value=[regex]::Replace($value,'\s*;\s*([a-z])',[System.Text.RegularExpressions.MatchEvaluator]{ param($m) '. ' + $m.Groups[1].Value.ToUpper() })
    return $value.Replace(';',',').Replace([string][char]0x2014,', ').Replace([string][char]0x2013,'-')
}
function Notes($slide,[string]$value) {
    $slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text=(Clean $value)
}
function Picture($slide,[string]$relative,[double]$x,[double]$y,[double]$w,[double]$h,[string]$alt) {
    $p=$slide.Shapes.AddPicture((Join-Path $workspace $relative),0,-1,0,0,-1,-1)
    $ratio=$p.Width/$p.Height
    if ($w/$h -gt $ratio) { $p.Height=$h; $p.Width=$h*$ratio } else { $p.Width=$w; $p.Height=$w/$ratio }
    $p.Left=$x+($w-$p.Width)/2; $p.Top=$y+($h-$p.Height)/2
    $p.AlternativeText=$alt
}
function Table($slide,$rows,$widths,[double]$x,[double]$y,[double]$w,[double]$h,[double]$font=17) {
    $shape=$slide.Shapes.AddTable($rows.Count,$rows[0].Count,$x,$y,$w,$h)
    $t=$shape.Table
    for ($c=1;$c -le $widths.Count;$c++) { $t.Columns.Item($c).Width=$widths[$c-1] }
    for ($r=1;$r -le $rows.Count;$r++) {
        $t.Rows.Item($r).Height=$h/$rows.Count
        for ($c=1;$c -le $rows[0].Count;$c++) {
            $cell=$t.Cell($r,$c)
            $cell.Shape.Fill.Solid(); $cell.Shape.Fill.ForeColor.RGB=$(if ($r -eq 1) {$headBg} else {$rowBg})
            $tf=$cell.Shape.TextFrame
            $tf.MarginLeft=10; $tf.MarginRight=10; $tf.MarginTop=7; $tf.MarginBottom=7
            $tf.VerticalAnchor=3
            $tr=$tf.TextRange; $tr.Text=[string]$rows[$r-1][$c-1]
            $tr.Font.Name='Arial'; $tr.Font.Size=$font; $tr.Font.Color.RGB=$(if ($r -eq 1) {$mint} else {$fg})
            $tr.Font.Bold=$(if ($r -eq 1 -or $c -eq 1) {-1} else {0})
            $tr.ParagraphFormat.SpaceAfter=0; $tr.ParagraphFormat.SpaceBefore=0; $tr.ParagraphFormat.Bullet.Visible=0
            for ($b=1;$b -le 4;$b++) { $cell.Borders.Item($b).ForeColor.RGB=$grid; $cell.Borders.Item($b).Weight=0.5 }
        }
    }
    return $shape
}

# MAIN PRESENTATION: seven slides, 165 seconds of narration plus transitions.
$s=NewSlide ''
$null=Text $s 'CoForm' 46 57 520 83 62 $mint $true
$null=Text $s ('AI specialists for custom' + [char]11 + '3D-printed parts') 46 150 520 87 34 $fg $true
$null=Text $s 'A simple holder needs decisions about fit, strength and printing.' 46 262 520 68 25 $fg
$null=Text $s 'We built CoForm so a person can describe a part, inspect a checked design, and prepare it for fabrication.' 46 350 520 100 22 $muted
$null=Text $s 'Manufacturing & Materials     MIT Media Lab     November 1, 2026' 46 478 520 20 13 $blue
Picture $s 'docs\images\backpack-holder-printed.jpg' 600 40 314 400 'Photograph of the printed wall-mounted backpack holder: back plate with four screw holes, arm, gusset and round hook tip. Designed by the CoForm agents and printed in resin.'
$null=Text $s 'Designed by our AI agents, printed in resin' 600 450 314 20 12 $muted
Notes $s @'
MAIN PRESENTATION. Target 15 seconds. The main talk ends after slide 7. Slides 8-12 are supporting evidence for reading and questions. Total narration target: 165 seconds, leaving 15 seconds for transitions.

Script: A bottle holder sounds simple, but making one that fits, holds a load and prints cleanly requires several skills. We built CoForm to turn a plain-language request into a design people can inspect, prepare to print and eventually test.

Sources: README.md, sections The problem and How it works. Official event brief: https://scienceclawhack.ai/#competition and https://scienceclawhack.ai/ (accessed November 1, 2026). Manufacturing & Materials is a challenge area, not a separate competition track. The intended users are people needing custom parts without doing all the CAD themselves. Ease of use and engineering reliability have not been established by a user study.

Photo: the printed earlier backpack holder (project part_5c0e9eda2c4146de, adopted test test_a04a2a8e339e4fb0), photographed by the team on November 1, 2026 (docs/images/backpack-holder-printed.jpg). It is shown unloaded. No mass or bend has been measured yet. If asked: this is the agents' design, printed from the approved file.
'@

$s=NewSlide 'One request becomes a checked design'
$null=Text $s '"Build a water bottle holder which can attach to a desk and hold it."' 46 102 868 57 23 $blue $true
$null=Text $s '1  Describe the need' 46 178 443 27 20 $mint $true
$null=Text $s 'The user gives a request and any known sizes or load limits.' 46 209 438 44 18 $fg
$null=Text $s '2  Choose tested components' 46 259 443 27 20 $mint $true
$null=Text $s 'Mechanics chooses the clamp and cup. Fabrication and measurement review the concept.' 46 290 438 45 18 $fg
$null=Text $s '3  Build, review and refine' 46 340 443 27 20 $mint $true
$null=Text $s 'Code assembles and tests the geometry. Agents review failures and propose improvements.' 46 371 438 45 18 $fg
$null=Text $s '4  Inspect, print and measure' 46 421 443 27 20 $mint $true
$null=Text $s 'The user authorizes the print, starts the printer, then enters the measured bend.' 46 452 438 45 18 $fg
Picture $s 'docs\images\bottle-holder-in-use.png' 515 174 399 272 'CoForm simulation showing a desk-edge clamp and cup around a reference desk and bottle. This is a virtual model, not a photograph.'
$null=Text $s 'Tested assumptions: 25 mm desk, 75 mm bottle diameter, 1 kg load. The request supplied no sizes.' 525 448 379 47 15 $muted
Notes $s @'
Target 25 seconds.

Script: Here is the actual request. Mechanics chooses tested components, and the other agents review the concept. Code assembles the geometry and checks fit and strength. The agents review the results and propose improvements. The user inspects the model, authorizes the print and starts the printer. Afterward, a measured bend can update the material model.

Sources: README.md, Desk-edge water bottle holder and How it works; coform/concepts.py; coform/components.py; coform/scene.py; coform/designer.py; coform/static/designer.js. Image: docs/images/bottle-holder-in-use.png, an existing CoForm finite-element viewer render. No physical measurement has yet been recorded for this example. Printing requires user action and the checks described on slide 10. 'Checked' means within the encoded geometry and simulation assumptions. The current catalogue covers tested combinations of desk/wall mounts with cup, cradle or hook. Unsupported mechanisms fall back to less reliable freehand drafting.
'@

$s=NewSlide 'The three agents and where they come from'
$null=Text $s 'CoForm creates three separate AI roles. In the demonstrated runs, all three use NVIDIA Nemotron 3 Ultra through NVIDIA''s API.' 46 106 868 59 21 $fg
$rows=@(
    @('Agent','Responsibility','Contribution to the design'),
    @('Mechanics','Will it hold the load?','Chooses the mechanism and proposes changes to strength and stiffness.'),
    @('Fabrication','Can we print it?','Reviews resin drainage, supports and clearances. Proposes material savings.'),
    @('Measurement','Can we test it?','Plans how to hold, load and measure the part. Does not change geometry.')
)
$null=Table $s $rows @(148,241,479) 46 188 868 236 18
$null=Text $s 'Each role gets its own instructions and model call. They coordinate through a shared record of proposals, reviews and measured results.' 46 445 868 52 19 $muted
Notes $s @'
Target 25 seconds.

Script: These are three software roles we created, using separate calls to the same NVIDIA Nemotron model. Mechanics owns whether the part holds. Fabrication owns whether it can print and where material can be saved. Measurement owns how to test it. They receive different instructions and publish proposals and reviews into a shared evidence record.

Sources: coform/designer.py, ROLES, FOCUS, REVIEW_FOCUS, _propose and _review; coform/providers.py; docs/providers.md; README.md, Who decides what. Demonstrated model identifier: nvidia/nemotron-3-ultra-550b-a55b. The roles are not three separately trained models and are not agents recruited from external teams. Each role can be configured to use OpenAI, Anthropic, Google Gemini or NVIDIA, but that flexibility is not evidence of model diversity in these runs. In the designer's optimization stage, measurement proposes the test plan and concerns rather than geometry changes. Agents communicate through software-managed evidence, not a direct chat channel.
'@

$s=NewSlide 'Why a collective: the requirements compete'
$null=Text $s 'Saving resin can make a part bend too far. Separate roles keep strength, fabrication and testability visible during the same decision.' 46 105 868 63 22 $fg
$null=Text $s 'A real decision on the backpack holder' 46 188 868 28 22 $mint $true
$rows=@(
    @('Proposal','Predicted bend','Decision'),
    @('Mechanics: lighter geometry','2.52 mm','Reject: exceeds 2 mm limit'),
    @('Fabrication: moderate thinning','1.81 mm','Adopt: 99.1 g and passes')
)
$null=Table $s $rows @(361,191,316) 46 232 868 148 18
$null=Text $s 'Agents propose and review. Code tests the alternatives and selects the lightest passing design. A verified failure cannot be voted away.' 46 402 868 56 20 $fg
$null=Text $s 'There is no lead AI. Fixed software stages coordinate the roles. This example shows a useful contribution, not proof that three agents beat one.' 46 468 868 38 14.5 $muted
Notes $s @'
Target 25 seconds.

Script: The reason for a collective is that the requirements compete. On the backpack holder, mechanics proposed a lighter version that bent beyond our limit. Fabrication proposed more moderate thinning that passed. The agents supplied alternatives and reviews, while code tested them and selected the lightest passing design. This is a concrete contribution, although it does not establish an advantage over one agent.

Sources: README.md, Wall-mounted backpack holder; original deck slide 5; coform/designer.py, _propose, _review, _choose and _print_votes. The example is the saved project part_5c0e9eda2c4146de and chosen result test_a04a2a8e339e4fb0. Mechanics/fabrication can propose geometry changes; measurement supplies the test plan and reviews. The workflow is centrally implemented in deterministic software with prescribed stages and selection rules. Design judgments are distributed among specialists within those stages. Do not describe this as an unconstrained, self-organizing network. Machine-checkable blocking objections are verified; unsupported qualitative advice remains advice. At print time, every hard gate must pass and at least two of three specialists must approve.
'@

$s=NewSlide 'Latest hook: 48% less modeled material'
$null=Text $s 'Request: a wall hook for a backpack. The current component workflow produced this design.' 46 103 868 55 22 $fg
Picture $s 'presentation\.build-judge\latest-hook.png' 46 170 410 275 'Current CoForm viewer screenshot of the latest wall hook, with a wall, mounting screws and a strap applying a 49.05 N load. Simulation only.'
$null=Text $s '5 kg modeled load. Bend limit: 2 mm. Stress limit: 23 MPa.' 482 175 432 46 18 $muted
$rows=@(
    @('Prediction','First accepted','Best design'),
    @('Part mass','44.2 g','22.9 g (-48%)'),
    @('Bend','0.21 mm','1.87 mm'),
    @('Peak stress','3.6 MPa','15.7 MPa')
)
$null=Table $s $rows @(138,145,149) 482 236 432 158 16.5
$null=Text $s 'This newer hook passes the modeled checks. Print preparation is still pending.' 46 452 410 49 17 $muted
$null=Text $s 'An earlier 99.1 g backpack holder has finished printing. Its physical load test is still pending.' 482 422 432 77 20 $mint $true
Notes $s @'
Target 30 seconds.

Script: The newest run uses that component workflow. Its backpack hook became 48 percent lighter while passing the modeled five-kilogram load checks. Both mechanics and fabrication supplied adopted improvements. This new hook has not been prepared for printing yet. A different, earlier 99-gram holder has finished printing, but we have not weighed it or measured its bend under load.

Sources: current saved run part_3abc7acbd34746e8, prompt 'generate me a wall mounted hook whcih i can hang my backpack from'. Accepted concept: wall + hook, 5 kg, strap width 40 mm and loop depth 40 mm. First accepted test test_6043aa2f2bbd4574: 44.170516 g, 0.214025 mm, 3.577752 MPa. Best test test_11c6170970d14f92 (round 9, fabrication): 22.881164 g, 1.865086 mm, 15.667973 MPa. Reduction = 48.1981%. Force = 49.05 N. Mechanics supplied adopted rounds 1, 2 and 8; fabrication supplied adopted rounds 6 and 9. Twelve rounds ran; the last three did not improve on round 9. The first concept required a repair before acceptance, so this is not a first-attempt success claim. There is no saved print approval for this newer hook. Screenshot: current local CoForm viewer, captured November 1, 2026, latest adopted test selected. This image represents simulated stress, not physical deformation or a photographed print. The earlier printed part is project part_5c0e9eda2c4146de, chosen test test_a04a2a8e339e4fb0, 99.140412 g and 100 N modeled load. The user explicitly confirmed that this earlier holder, not the newer 22.9 g hook, has finished printing. No physical measurements were supplied or found in the records.
'@

$s=NewSlide 'The evidence supports a prototype, with limits'
$null=Text $s 'A working request-to-print workflow' 46 108 411 59 23 $mint $true
$null=Text $s 'It creates geometry, checks fit, runs physics simulations and prepares prints in Formlabs PreForm.' 46 159 411 82 21 $fg
$null=Text $s '348 automated tests cover the software workflow. One design has finished printing.' 46 264 411 82 21 $fg
$null=Text $s 'Physical load testing and measurement-based calibration are still pending.' 46 375 411 77 21 $amber $true
$null=Text $s 'A collective advantage is still unproven' 500 108 414 62 23 $mint $true
$null=Text $s 'Beam-sizing benchmark: 4 runs per method. All passed the simulated acceptance checks.' 500 181 414 53 17.5 $muted
$rows=@(
    @('Method','Material vs reference'),
    @('Numerical optimizer','76%'),
    @('Single AI agent','88%'),
    @('Three-agent collective','93%')
)
$null=Table $s $rows @(232,182) 500 252 414 155 17
$null=Text $s 'Lower is better. The optimizer won this narrow task. Full protocol and costs: slide 9.' 500 426 414 57 18 $fg
Notes $s @'
Target 30 seconds.

Script: We can demonstrate the pipeline, simulations, print preparation and a completed print. Physical load testing is still pending. We also tested whether the collective helps: in a small beam-sizing benchmark, a conventional optimizer used less material than either one agent or three. Our evidence therefore supports a functioning design workflow, but not a general claim that a collective is better.

Sources: README.md, Does the collective add capability? and Validation and limits; data/comparison-live-alpha-fast/comparison.json, summary; tests directory. '348 automated tests' is the project's recorded test count, not a new test execution during this slide edit. Ratios are mean candidate/reference geometry volume in passing runs: adaptive 0.7555556, single_agent 0.8777778, collective 0.9333333. The benchmark uses a simulated beam rig and equal allowed budgets; actual model-call and token consumption differ. It does not compare the full open-ended designer. Physical-print status comes from the user's November 1 update. The open-ended bottle before/after also changed code, checks and drafting strategy, so it cannot isolate the collective's effect.
'@

$s=NewSlide 'CoForm makes the design decision inspectable'
$null=Text $s 'A request produces a design, the reasons behind it, and the evidence needed to decide what to print and test.' 46 119 868 122 31 $mint $true
$null=Text $s 'Next physical step' 46 284 394 30 22 $fg $true
$null=Text $s 'Load the printed backpack holder, measure its bend, and recheck the design with corrected material stiffness.' 46 327 394 101 21 $muted
$null=Text $s 'Next research step' 512 284 402 30 22 $fg $true
$null=Text $s 'Compare one agent and the collective on the same open-ended requests, tools, checks and budget.' 512 327 402 101 21 $muted
$link=Text $s 'Code and evidence: ScienceClaw_Project / Jash_ver' 46 467 868 27 18 $blue
$link.ActionSettings.Item(1).Hyperlink.Address='https://github.com/MishraAsh2007/ScienceClaw_Project/tree/Jash_ver'
Notes $s @'
Target 15 seconds. End the three-minute talk here. The next five slides are supporting evidence for prejudging or questions.

Script: CoForm's contribution is an inspectable path from a request to a part we can print and test. Next, we will measure the printed holder and run a controlled comparison on open-ended designs to find out when the collective adds value.

Sources: coform/store.py, append-only records and hash-chained events; coform/designer.py, record_measurement; README.md. Repository: https://github.com/MishraAsh2007/ScienceClaw_Project/tree/Jash_ver . The physical test and controlled open-ended benchmark are next steps, not completed results. Calibration updates effective elastic stiffness from measured versus predicted bend; it does not directly calibrate failure strength.
'@

# SUPPORTING EVIDENCE: readable without a live demonstration.
$s=NewSlide 'What the software checks' $true
$null=Text $s 'Code builds the geometry from tested components, then evaluates it against the desk, bottle, screws and other reference bodies.' 46 104 868 59 21 $fg
$rows=@(
    @('Check','Meaning in the bottle-holder example'),
    @('Fit and contact','The clamp leaves room for the desk and touches the required faces.'),
    @('Insertion and retention','The bottle and screw can enter. Geometry blocks unwanted escape directions.'),
    @('Strength and stiffness','A finite-element simulation estimates stress and bending under the chosen load.'),
    @('Manufacturability','Checks cover minimum walls, printer bounds and resin drainage. PreForm checks supports.')
)
$null=Table $s $rows @(243,625) 46 187 868 258 18
$null=Text $s 'Failed checks trigger repair. Code keeps the best checkpoint and rolls back regressions. Unverified blocking objections prevent approval.' 46 466 868 38 16 $amber
Notes $s @'
Supporting evidence. Finite-element analysis divides a 3D part into small elements and calculates its deformation and stress under specified loads and constraints. CoForm uses Gmsh/OpenCASCADE for geometry and meshing and CalculiX for 3D linear-elastic analysis, with Formlabs Alpha Fast Resin properties. 'Retention' here refers to the encoded geometric direction checks, not experimental proof of friction, clamping force or resistance to every real-world disturbance.

Sources: coform/scene.py; coform/components.py; coform/concepts.py; coform/freeform.py; coform/designer.py; README.md, Build and test and Validation and limits. Unsupported mechanisms fall back to freehand CAD drafting, which the recorded experience shows is less reliable. Draft review failures trigger repair/redesign attempts. The record retains attempts and the best accepted checkpoint. Fabrication review identified insufficient thread clearance at the M6 clamp hole as a should-fix concern; this remains unresolved in the recorded example.
'@

$s=NewSlide 'Benchmark protocol and full results' $true
$null=Text $s 'Task: size a support beam in a simulated rig. Two fixture scenarios and two seeds give four runs per method, with the same allowed budgets.' 46 103 868 60 21 $fg
$rows=@(
    @('Method','Passed','New design\npassed','Mean volume\nvs reference','AI tokens\nper run'),
    @('Numerical optimizer','4/4','4/4','0.76','0'),
    @('Collective','4/4','1/4','0.93','161k'),
    @('Single agent, same tools','4/4','2/4','0.88','31k'),
    @('Agents without evidence exchange','4/4','2/4','0.87','52k')
)
for ($ri=0;$ri -lt $rows.Count;$ri++) { for ($ci=0;$ci -lt $rows[$ri].Count;$ci++) { $rows[$ri][$ci]=$rows[$ri][$ci].Replace('\n',"`r") } }
$null=Table $s $rows @(298,82,118,185,185) 46 188 868 232 16.5
$null=Text $s 'Lower volume is better. All AI methods used Nemotron 3 Ultra. The collective consumed the most tokens and did not outperform the baselines.' 46 438 868 43 18 $fg
$null=Text $s 'This small simulated benchmark does not test open-ended part design or establish a general ranking.' 46 486 868 21 14 $amber
Notes $s @'
Supporting evidence. Source: data/comparison-live-alpha-fast/comparison.json and comparison.csv. Scenarios: rig_alpha_fast_printed_jaws and rig_alpha_fast_rubber_clamp. Seeds: 17 and 18. Each matched pair shares the same initial evidence. Allowed protocol includes eight search cycles, three design candidates, three rounds and a 500,000-token ceiling. Actual consumption varies by method.

Mean volume ratios: adaptive 0.7555555556, collective 0.9333333333, single_agent 0.8777777778, independent 0.8666666667. Total reported tokens divided by four: 0, 161451.75, 30776.5, 51833.5. Mean calls: 0, 48.5, 7.75, 21. 'Passed' counts runs whose final copies passed simulated acceptance; 'New design passed' distinguishes a new passing candidate from retaining the reference. Volume ratios reflect nominal geometry, not measured printed mass. The benchmark has only four runs per method and uses a simulator, so it is a case study rather than evidence of a statistically reliable general advantage.

Open-ended-design evidence: the bottle-holder workflow improved after introducing concept selection, component assembly and measurement-based review arbitration. Both code and checks changed, so that before/after is not a controlled multi-agent versus single-agent comparison. Source: README.md, Does the collective add capability?
'@

$s=NewSlide 'Print preparation and physical validation' $true
$null=Text $s 'PreForm orients and supports the part. Queueing requires every hard gate to pass, at least two agent approvals, and a user action.' 46 103 868 60 21 $fg
$rows=@(
    @('PreForm result','Bottle holder','Backpack holder'),
    @('Grade / support warning','WARN / 0.5% unsupported','WARN / 3.9% unsupported'),
    @('Estimated print / resin','1.8 h / 157 mL','1.5 h / 117 mL'),
    @('Agent approvals','3 of 3','3 of 3')
)
$null=Table $s $rows @(180,190,190) 46 176 560 190 14
$null=Text $s 'Physical status: backpack print complete. No measured mass or bend yet.' 46 382 574 50 19 $amber $true
$null=Text $s 'The test plan is to match the modeled fixture and load, measure bend at the load point, then enter it in CoForm. Code updates effective stiffness and reruns the checks.' 46 438 574 66 16 $fg
Picture $s 'docs\images\backpack-holder-printed-wide.jpg' 630 176 284 260 'Photograph of the printed wall-mounted backpack holder, unloaded, lying on its back plate.'
$null=Text $s 'The printed backpack holder, before load testing' 630 444 284 36 12 $muted
Notes $s @'
Supporting evidence. Sources: README.md; coform/designer.py, prepare_print, _print_votes, queue and record_measurement; saved print reviews print_review_55b8128754e74044 (backpack) and the approved review for part_941f3eb27f4d40aa (bottle). The backpack estimate is 5555 seconds and 116.5002 mL, rounded on-slide. Unsupported surface fractions are approximately 0.03859 for the backpack and 0.005 for the bottle. PreForm grade WARN means a non-fatal warning, not a clean PASS. Human review of supports is still relevant. The quoted resin volume includes supports and differs from the modeled part mass. Hardware/software: Formlabs PreForm 3.65 with Alpha Fast Resin.

User status update, November 1, 2026: backpack holder finished printing; no weight or load-deflection measurements yet. No claim that the bottle holder has printed. Intended backpack model load is 100 N at the hook tip. The measurement agent plans how to hold/load the part and assess resolution. Code uses the measured/predicted deflection ratio to update effective elastic stiffness, with configured bounds, and reruns the existing checks. This loop is implemented and tested with simulated measurements but has not yet been validated with this physical print. It does not establish certified strength.

Photo: docs/images/backpack-holder-printed-wide.jpg, the same printed part as slide 1, photographed unloaded on November 1, 2026.
'@

$s=NewSlide 'Evidence for the ScienceClaw judging rubric' $true
$rows=@(
    @('Criterion','Evidence in this submission','Slides'),
    @('Problem significance / 20%','A physical part must satisfy fit, strength and fabrication constraints.','1-2, 8'),
    @('Technical impact / 25%','Generated parts with 16-48% modeled mass savings. One completed print.','5, 12'),
    @('Decentralized agency / 20%','Separate role instructions, proposals and reviews. Code enforces shared rules.','3-4'),
    @('Collective capability / 25%','Concrete design tradeoff plus single-agent and numerical baselines.','4, 6, 9'),
    @('Execution and validation / 10%','Working pipeline, saved evidence, simulations and PreForm. Load test pending.','6, 8, 10')
)
$null=Table $s $rows @(258,512,98) 46 115 868 326 17
$link=Text $s 'Official brief and rubric: scienceclawhack.ai' 46 465 868 27 18 $blue
$link.ActionSettings.Item(1).Hyperlink.Address='https://scienceclawhack.ai/'
Notes $s @'
Supporting evidence. Official source: https://scienceclawhack.ai/ (Competition / Judging and Overview / Challenge), accessed November 1, 2026. Required demonstration areas are problem, functioning system, result and validation. Core weights are 20%, 25%, 20%, 25%, 10%. The official brief also offers up to ten collaboration-bonus points for capabilities that materially strengthen other teams; this submission does not claim demonstrated cross-team reuse. The public website did not specify a slide count. User supplied the three-minute presentation duration.

Repository and evidence: https://github.com/MishraAsh2007/ScienceClaw_Project/tree/Jash_ver ; README.md; coform/designer.py; coform/scene.py; coform/store.py; data/comparison-live-alpha-fast/comparison.json. Refer to the specific saved project/test IDs in slides 4-5 notes for the showcased designs. Potential usefulness is demonstrated through the examples, not a user adoption study or a claim of research priority.
'@

$s=NewSlide 'Other demonstrated designs and current scope' $true
Picture $s 'docs\images\bottle-holder.png' 46 95 412 185 'Simulated bottle holder made through the current component-based workflow.'
Picture $s 'docs\images\backpack-holder.png' 502 95 412 185 'Simulated earlier backpack holder. The user confirmed this is the design that finished printing.'
$rows=@(
    @('Result','Bottle holder','Earlier backpack holder'),
    @('Predicted part mass','158.1 g to 125.4 g (-21%)','118.5 g to 99.1 g (-16%)'),
    @('Predicted bend','0.08 mm under 1 kg','1.81 mm under 100 N'),
    @('Fabrication status','Approved for printer queue','Print complete, no load test yet')
)
$null=Table $s $rows @(214,327,327) 46 291 868 155 16.5
$null=Text $s 'Current catalogue: desk or wall mounts with cups, cradles or hooks. A recent portable phone-stand request has no accepted design in the saved record.' 46 466 868 38 16 $amber
Notes $s @'
Supporting evidence. Sources: README.md; current saved projects part_941f3eb27f4d40aa (bottle), part_5c0e9eda2c4146de (earlier backpack), and part_5f2cc54ce5314399 (portable phone stand). Bottle adopted test test_21ac10ac1b184d93, backpack adopted test test_a04a2a8e339e4fb0. Reductions from rounded masses are 20.68% and 16.37%. Both mass and bend are model predictions. Bottle peak stress 0.76 MPa, earlier backpack peak stress 11.6 MPa; both compare with 23 MPa allowable. Bottle uses the current concept/component approach. The earlier backpack came from freehand drafting before that stage existed. The phone-stand run contains attempts but no accepted design_spec or design_test. Its failure shows that a general request may not match the available component catalogue. Unsupported concepts can fall back to freehand drafting, which remains less reliable. Do not claim arbitrary-part generation is solved.

The current catalogue in coform/concepts.py combines desk_edge or wall mounts with cup, cradle or hook. Dimensions have tested ranges; omitted sizes become disclosed assumptions and explicit user limits take priority. Repairs preserve reference bodies and requirements, roll back regressions, and can request a different mechanism after stalled progress. Sources: coform/designer.py; coform/design_search.py; coform/designer_repair.py; coform/scene.py. The actual program has two interfaces: Part designer and Support studies. The benchmark on slide 9 belongs to the support-study lab, not an ablation of the open-ended designer. Test collection on November 1, 2026 found 348 tests; the suite was not executed as part of this presentation edit. The current database has zero design_measurement records.
'@

# Capture text-fit diagnostics before rendering and saving.
$diagnostics=@()
foreach ($sl in $deck.Slides) {
    foreach ($sh in $sl.Shapes) {
        if ($sh.HasTextFrame -and $sh.TextFrame.HasText) {
            $bh=$sh.TextFrame.TextRange.BoundHeight
            $bw=$sh.TextFrame.TextRange.BoundWidth
            if ($bh -gt $sh.Height+1 -or $bw -gt $sh.Width+2) {
                $diagnostics += [pscustomobject]@{slide=$sl.SlideIndex;shape=$sh.Name;text=$sh.TextFrame.TextRange.Text;boundHeight=$bh;frameHeight=$sh.Height;boundWidth=$bw;frameWidth=$sh.Width}
            }
        }
        if ($sh.HasTable) {
            for ($r=1;$r -le $sh.Table.Rows.Count;$r++) {
                for ($c=1;$c -le $sh.Table.Columns.Count;$c++) {
                    $cell=$sh.Table.Cell($r,$c).Shape
                    $bh=$cell.TextFrame.TextRange.BoundHeight
                    if ($bh -gt $cell.Height-$cell.TextFrame.MarginTop-$cell.TextFrame.MarginBottom+1) {
                        $diagnostics += [pscustomobject]@{slide=$sl.SlideIndex;cell="$r,$c";text=$cell.TextFrame.TextRange.Text;boundHeight=$bh;availableHeight=$cell.Height-14}
                    }
                }
            }
        }
    }
}
ConvertTo-Json -InputObject @($diagnostics) -Depth 4 | Set-Content -LiteralPath (Join-Path $buildDir 'text-fit.json') -Encoding UTF8
# Refuse to save a deck that breaks the house style (slides, tables and speaker notes).
$banned='[;' + [char]0x2014 + [char]0x2013 + ']'
$found=@()
foreach ($sl in $deck.Slides) {
    $texts=@($sl.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text)
    foreach ($sh in $sl.Shapes) {
        if ($sh.HasTextFrame -and $sh.TextFrame.HasText) { $texts+=$sh.TextFrame.TextRange.Text }
        if ($sh.HasTable) { foreach ($row in $sh.Table.Rows) { foreach ($cell in $row.Cells) { $texts+=$cell.Shape.TextFrame.TextRange.Text } } }
    }
    foreach ($t in $texts) { if ($t -match $banned) { $found+="slide $($sl.SlideIndex): $($t.Substring(0,[Math]::Min(80,$t.Length)))" } }
}
if ($found.Count) { $deck.Close(); throw ("Semicolons or dashes left: " + ($found -join ' | ')) }
$deck.SlideShowSettings.RangeType=2
$deck.SlideShowSettings.StartingSlide=1
$deck.SlideShowSettings.EndingSlide=7
$deck.SaveAs($finalPptx,24)
$deck.Export($renderDir,'PNG',1280,720)
$deck.SaveAs($finalPdf,32)
$count=$deck.Slides.Count
$deck.Close()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($deck) | Out-Null
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
Write-Output "Created $count slides. Text-fit flags: $($diagnostics.Count)."
Get-Item -LiteralPath $finalPptx,$finalPdf | Select-Object Name,Length
