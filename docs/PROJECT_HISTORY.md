# SubZeroAstro - project history and decisions

A summary of the long first conversation ("FITS blaster app Windows conversion", Oct-Nov 2026) in which the app was
built, so later sessions know what was decided and why. CLAUDE.md has the working rules; this has the background.

## The app
SubZeroAstro started as a Windows conversion of the Mac app "FITS Blaster" (Tom van Peer's culling viewer inspired
page 1) and grew into a full pipeline for Windows and Apple-silicon Macs:
- **Page 1 - Quality & Cull:** star quality per frame (FWHM, eccentricity, SNR, stars, background), Auto-cull presets,
  flags, Set Filter Name, Edit FITS Headers (with undo), FITS/XISF viewer, **Filter Identifier** (0.9.284).
  Tools around it: Organize My Astro Files (+ Organize Automatically, Change My Library's Layout), Fetch From My
  ASIAIRs (overnight fetch, Refresh, Right now column), Clean Up My N.I.N.A. Computers (+ Delete Old Frames, two
  warnings), Tonight's Target Picker (DSS2 colour picture via CDS hips2fits, My Rigs dropdown), My Weather Forecast,
  Watch for New Frames, N.I.N.A. Session Metadata guiding, Same Framing Next Session.
- **Page 2 - Calibrate and Stack:** calibration library and masters, rig naming by My Rigs (deepest folder name wins),
  unusable darks left out automatically (brighter than the lights), NAS read retries (Errno 22), comet stacking.
- **Page 3 - Astro Image Processing:** crop, ASTAP plate solve, gradient removal (GraXpert / built-in),
  Blur/Noise/StarXTerminator via the RC-Astro CLI, checkpoints and step history. Since 0.9.282 the previews and the
  quick look show the stars put back (masters stay starless + *_stars files).
- **Page 4 - Finishing Touches:** palettes (HOO, SHO from dual-band pairs like Askar D1+D2 / L-Ultimate+L-Synergy with
  measured OIII leakage), brighten (colour-preserving / GHS), Take Stars Out / Put Stars Back, Tame Bright Core (soft
  knee; galaxy nucleus moved back from the stars layer), Clean Bright-Star Leftovers, Spot Fix, Mote Buster.
- **Fast Track** runs pages 1-4 with the recommended choices.
- Releases: 0.9.284 (Nov 17) is the first with the Windows app signed (Azure) and the Mac app signed + notarized.

## The user's setup (for testing and examples)
- Brian Poteet, DVM, Tomball TX (Bortle 7 suburban). Backyard rigs on ZWO mounts; ASIAIRs and N.I.N.A. PCs
  (PrimaLuceLab Eagles, RBFocus Gaius, mini PCs) copying to a NAS (Synology, \\astronas / DS1522).
- Telescopes include Askar SQA55 (265 mm), SQA60 Pro, SQA106, a 270 mm Sightron, a 300 mm Newtonian (~1200 mm),
  Williams Optics FL132 and Pleiades 181, Celestron C11 EdgeHD, Artesky 250, RASA 11.
- Cameras include ZWO ASI2600MC Pro / Air / Duo, ASI2600MM Pro, ASI6200MC / MM Pro.
- Filters: Optolong L-Pro, L-Quad Enhance, L-Para, L-eXtreme, L-Ultimate, L-CCD; IDAS NBZ II; Askar ColorMagic D1/D2;
  mono LRGB / SHO sets.

## Filter Identifier (filter_id.py, 0.9.284)
- Reference subs: NGC 281, ASI2600MC Air + SQA55, gain 100, 120 s, 2-6 subs per filter, autofocus before each
  filter, 2026-11-16/17. Features: sky R/G and B/G, star R/G and B/G, sky e-/s/arcsec², focus position.
- Starlight passed vs no filter (green): L-CCD 99%, L-Pro 54%, L-Quad 52%, L-Para 7.5%, NBZ II 6.5%, D1 4.9%,
  L-eXtreme 4.7%, D2 4.4%, L-Ultimate 2.6%. L-Pro and L-Quad differ in colour (sky R/G 0.39 vs 0.29), not brightness.
  No filter vs L-CCD: same colours; focus position differs ~650 steps on the SQA55 (17,4xx vs ~18,100).
- Results: all 35 reference subs right (leave-one-out too); the "Mystery" set (L-Quad) identified; M81 subs from
  March 2025 (ASI2600MC Duo, 270 mm, drawer filter unknown) -> L-Quad Enhance (could also be L-Pro), confirmed by
  the user. R/G/B/Ha/OIII/SII not tested on purpose (mono filters; told by colour alone).
- Ideas for later: plate-solve + Gaia catalogue colours for absolute starlight; IMX533 and other sensors need
  frames with a known filter (Teach) - marked "unverified" until then.

## Business and accounts (Nov 2026)
- **Howling Star Projects, LLC** formed, EIN issued; DBA "SubZeroAstro" (Texas) pending; trademark "SubZeroAstro"
  filed; book copyright filed (in Brian's name - a written assignment to the LLC is for the lawyer).
- **D-U-N-S number** for the LLC still to request - needed to sign as the LLC (Azure org validation, Apple org).
- GitHub: organization HowlingStarProjects (display name "Howling Star Projects, LLC"), repository moved there
  2026-11-17; Claude GitHub app installed on the organization.
- Windows signing: Azure Artifact Signing, identity validated as Brian Poteet (expires 11/12/2027); client secret
  "GitHub3" expires ~Nov 2028 (a scheduled reminder exists for Oct 2028).
- Mac: Apple Developer Program (individual, Brian Poteet, Team 9D8Q6YBTQ4) valid to Nov 17 2027; Developer ID
  Application certificate valid to Dec 2027. First notarization took >45 min; later ones minutes.

## Decisions and plans
1. **Go private:** first set up the user's Windows PC and Mac as self-hosted GitHub Actions runners (free; hosted
   minutes for private repos cost money, Mac x10), then make the repository private.
2. **LLC name everywhere:** About box, file details, README, manual, startup screen -> "© Howling Star Projects, LLC";
   MIT licence replaced by a proprietary licence + EULA for new versions (past MIT releases stay MIT). Lawyer reviews.
3. **Harder to copy:** build with Nuitka (compiled) instead of PyInstaller.
4. **Downloads only from subzeroastro.com:** releases uploaded to the website with a version file; updater.py reads
   it instead of GitHub releases. Monetization (licence keys / sign-in) later.
5. **Next features asked for (Nov 18):** a manual tip + in-app guard so automatic Organize / overnight ASIAIR fetch /
   automatic N.I.N.A. clean-up run from **one computer only** (marker file in the library, "take over here"); then
   **user profiles** - Settings › My Profile… stored on the NAS, shared settings (My Rigs, location, Organize, ASIAIR
   list, N.I.N.A. computers, Target Picker, weather, processing defaults), per-computer ones local (tool paths, window
   sizes, automatic-job computer), Windows <-> Mac NAS path translation, Export / Import My Settings.
6. Older optional ideas: Astro-Smart safety-monitor integration, a moon/planetary page, a comparison chart for the
   launch, website and EULA.

## The book
"Astrophotography from Dust to Done - A Beginner's Guide" (Claude Docs artifact, 14 chapters + bonus + appendices,
71 pictures, ~159 letter pages). Exported 2026-11-20 as source (book.md + images/), EPUB, PDF and Word. Before
publishing: remove "draft 2026-10-23" from the title line; a 1600 x 2560 cover for Kindle. Plan: PDF sold from the
website, Kindle (no KDP Select) and Apple Books with the EPUB.
