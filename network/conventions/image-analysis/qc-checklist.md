# Image Analysis QC Checklist

Quality control checks for imaging data. Run these before proceeding with analysis.

## Image Acquisition QC

### Before Imaging

- [ ] **Microscope calibration** documented (date, objective, magnification)
- [ ] **Pixel size** known and documented (microns/pixel)
- [ ] **Channel configuration** recorded (excitation/emission wavelengths, filters)
- [ ] **Exposure settings** consistent across conditions (or documented if varied)
- [ ] **Control samples** included (positive control, negative control, unstained)

### During Acquisition

- [ ] **Focus quality** consistent across fields of view
- [ ] **No saturation** — check histogram; maximum should not hit the detector ceiling
- [ ] **No photobleaching** — consistent signal intensity across time/position
- [ ] **Flat-field reference** acquired (if doing illumination correction)
- [ ] **Metadata preserved** — microscope metadata saved with images

## Image Quality QC

### Per-Image Checks

- [ ] **Focus**: No blurry images (calculate focus metric, e.g., Brenner gradient)
- [ ] **Artifacts**: No dust, bubbles, scratches, or debris in field of view
- [ ] **Saturation**: < 0.1% of pixels at maximum intensity
- [ ] **Signal-to-noise**: Sufficient contrast between signal and background
- [ ] **Illumination**: Reasonably uniform across the field of view
- [ ] **Drift**: No significant stage drift (for time-lapse or tiled acquisitions)

### Per-Experiment Checks

- [ ] **Intensity consistency**: Mean background intensity consistent across wells/positions
- [ ] **Channel registration**: Channels properly aligned (check with multi-color beads)
- [ ] **Plate effects**: No systematic edge effects or gradient across plate
- [ ] **Complete acquisition**: All expected positions/timepoints/channels present
- [ ] **File integrity**: All image files open correctly and are the expected size

## Segmentation QC

- [ ] **Visual validation**: Segmentation overlaid on raw images for representative fields
- [ ] **Object count**: Reasonable (compared to manual estimate on a few images)
- [ ] **Object size**: Distribution matches expected (flag extreme outliers)
- [ ] **Edge objects**: Handled consistently (excluded or flagged)
- [ ] **Touching objects**: Properly separated (check high-density regions)
- [ ] **Artifacts**: Segmentation not triggered by debris, dead cells, or background

## Measurement QC

- [ ] **Distribution shapes**: Intensity and morphology distributions look reasonable
- [ ] **Outliers**: Extreme values investigated (segmentation errors? real biology?)
- [ ] **Controls**: Control samples show expected behavior
- [ ] **Batch effects**: Measurements consistent across imaging sessions
- [ ] **Physical units**: Measurements in physical units (microns, not pixels)

## Quantification QC

- [ ] **Positive controls** show expected signal
- [ ] **Negative controls** show baseline/no signal
- [ ] **Dynamic range** sufficient to distinguish conditions
- [ ] **Sample size**: Sufficient cells/objects per condition for statistical power
- [ ] **Biological replicates**: Multiple independent experiments, not just technical replicates

## Recording QC Results

Document all QC results in the analysis README under a "Quality Control" section:

1. Summary table of QC metrics per image/well/condition
2. Representative QC images (montages showing good and bad examples)
3. Any images/wells excluded and why (log in `.living/decisions.md`)
4. Focus metric distribution with threshold used
5. Intensity consistency plots across the plate/experiment
