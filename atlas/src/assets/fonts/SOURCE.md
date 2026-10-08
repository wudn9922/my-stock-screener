# Atlas Narrow Axis font source

`AtlasNarrowAxis-Regular.ttf` is the 70% horizontal-scale derivative of
Barlow Condensed Regular, distributed under the included SIL Open Font License
1.1 (`OFL.txt`).

- Original project: [The Barlow Project](https://github.com/jpt/barlow)
- Google Fonts source: <https://raw.githubusercontent.com/google/fonts/main/ofl/barlowcondensed/BarlowCondensed-Regular.ttf>
- Original SHA-256: `583cec5da3b84bc4dc7c9c72e2a565c94d34e431518b19d7e250b7830ad5f996`
- Derivative SHA-256: `5e465e809833ef0fa73c5a65827e921c0e02aba1facc263d606838c1bd126d1f`
- License: SIL Open Font License 1.1; the complete license and original copyright notice are in `OFL.txt`.
- Published standalone license: [atlas-narrow-axis-OFL.txt](../../../public/licenses/atlas-narrow-axis-OFL.txt)

The static derivative uses the distinct family name **Atlas Narrow Axis**. The
generator loads glyphs by name and flattens all 396 source composites into
outlines before scaling every outline x-coordinate by 0.70. Horizontal
advances, side bearings, and horizontal extent metadata are scaled by the same
factor; vertical glyph coordinates and bounds are preserved. The full 694-glyph
map remains. All source TrueType hint programs and the `fpgm`, `prep`, and `cvt`
tables are removed because they were tuned to the original outline geometry.
GPOS and the absent `kern` table are also omitted. Text outside the retained
cmap uses normal system font fallback. The web runtime does not need fontTools.
Startup waits at most five seconds for the font load; on failure or timeout the
app proceeds with the system fallback. If the font asset is unavailable, the
measured axis-width reduction is not guaranteed.

The initial derivative with SHA-256
`b5fc34a0655af71f6e234feae82dbf543b6f09aab052acd2c33aa45d39f92312` was
invalidated: that FontTools pass scaled horizontal metrics but left lazily
loaded outlines unchanged, causing overlapping glyph ink. Its axis-width
measurements are not valid. The corrected derivative is verified in
[`docs/compact-axis-measurement.json`](../../../docs/compact-axis-measurement.json)
and has SHA-256
`5e465e809833ef0fa73c5a65827e921c0e02aba1facc263d606838c1bd126d1f`.

The corrected generator verifies all glyph bounds against 70% of the source
within one font unit, verifies advances and side bearings, and verifies
non-overlapping ink for all ten digits. Zero has bounds `(36, 274)`, advance
`311`, left bearing `36`, and right bearing `37`; the vertical font bounds are
unchanged. The 11px corrected font reduced right-axis width by 30.77%–35.71%
for ordinary tested prices across desktop Chromium, mobile Chromium, iPad
WebKit, and iPhone WebKit. Across the extended price samples, the reduction was
30.77%–40%. The integrated regression passed 4/4 checks. Price formatting only
removes redundant trailing fractional zeroes and preserves cent precision
(`minMove: 0.01`).

The corrected chart was also reviewed in an emulated iPhone 13 WebKit raster;
the displayed Taiwan price digits were separated and readable, without glyph
overlap. This screenshot review is not a physical iPhone or Safari test.
