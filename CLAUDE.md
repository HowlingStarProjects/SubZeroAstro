# SubZeroAstro - notes for Claude sessions

Desktop astrophotography app (Python 3.13 + PySide6, NumPy) for Windows and Apple-silicon Macs, by Brian Poteet
for **Howling Star Projects, LLC** (DBA SubZeroAstro). Repository: HowlingStarProjects/SubZeroAstro (moved from
bapoteet/SubZeroAstro on 2026-11-17; the old address forwards).

## Every change is released
1. Bump `APP_VERSION` in `subzeroastro.py` ("0.9.NNN beta").
2. Rebuild the manual in two passes (page numbers in the index):
   ```
   D=<scratch dir>; cd docs/manual_src && node build_manual.js
   cd $D && soffice --headless --convert-to pdf <repo>/docs/manual_src/SubZeroAstro_User_Manual.docx
   python3 <repo>/docs/manual_src/pages.py $D/SubZeroAstro_User_Manual.pdf     # writes pages.json in the cwd
   cd <repo>/docs/manual_src && node build_manual.js $D/pages.json
   cd $D && soffice --headless --convert-to pdf <repo>/docs/manual_src/SubZeroAstro_User_Manual.docx
   cp the PDF to docs/ and docs/manual_src/, and the .docx to docs/
   ```
3. Commit as the user: `git -c user.name=bapoteet -c user.email=bapoteet@users.noreply.github.com commit` with a
   message starting "0.9.NNN: what the user gets".
4. Push (never while a build is running), then release: dispatch `build-windows.yml` with `release_tag=v0.9.NNN-beta`,
   or push that tag (tags `v*` build a release too).
5. Check the self-tests: branches `selftest-results` (Windows) and `selftest-results-mac`, file
   `*/console.txt`, last line `RESULT: ALL CHECKS PASSED`. Check the release has the Windows zip, the Mac zip and
   the manual, and that its notes say both apps are signed.
6. Tell the user what changed, in plain words.

## Self-tests
`python subzeroastro.py --selftest <dir>` runs `selftest.py` in a real window. Add a `check(ok, msg)` for every
feature; use variable names with a unique prefix (the test is one long function).

## Signing (releases only, both `continue-on-error`: a signing problem ships unsigned, never blocks)
- Windows: Azure Artifact Signing (account SubZeroAstro, profile SubZeroAstroRelease), secrets AZURE_TENANT_ID,
  AZURE_CLIENT_ID, AZURE_CLIENT_SECRET (expires ~Nov 2028).
- Mac: Developer ID "Brian Poteet (9D8Q6YBTQ4)" + notarization, secrets MACOS_CERT_P12, MACOS_CERT_PASSWORD,
  APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD (certificate expires Dec 2027). Bundle id stays
  io.github.bapoteet.subzeroastro (changing it would lose Mac users' settings).
- Both are in Brian's name until the LLC has a D-U-N-S number.

## Ground rules
- Never paste or ask for secrets in chat; the user adds them in GitHub himself.
- Never delete files in the user's library without a confirmation in the app; no passwords in logs.
- Don't send the user to GitHub pages for app support - the app and the manual explain things.
- Write for an astrophotographer, not a programmer: plain words in the UI, manual and replies.

## Plans in progress (Nov 2026)
- Repository goes **private** once the user's Windows PC and Mac are set up as self-hosted GitHub Actions runners
  (private repos pay for hosted minutes; Mac minutes count 10x).
- Then: copyright/About/EULA in the LLC's name (proprietary licence replacing MIT for new versions - lawyer reviews),
  a build with Nuitka (compiled, harder to copy), and downloads + update checks from subzeroastro.com instead of
  GitHub releases (updater.py reads GitHub releases today).
- Filter Identifier (filter_id.py): reference fingerprints from NGC 281 subs, ASI2600MC Air + Askar SQA55.
