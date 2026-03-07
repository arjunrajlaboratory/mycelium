# Image Analysis Conventions

Domain-specific conventions for image analysis. These extend the core mycelium analysis conventions.

## Image Data Organization

### Raw Image Storage

```
data/raw/[experiment-name]/
├── EXPERIMENT_NAME.md            # UPPER_SNAKE_CASE of folder; acquisition parameters, microscope, objective
├── images/                      # Original image files (TIFF, CZI, ND2, etc.)
├── metadata/                    # Microscope metadata exports
└── plate_map.csv               # If plate-based: well-condition mapping
```

- Store images in their original format — do not convert to JPEG or PNG for archival
- Proprietary formats (CZI, ND2, LIF) are acceptable; document the reader needed
- Large image datasets: follow the large-file convention (gitignore + download instructions)

### Processed Image Storage

```
data/processed/[experiment-name]/
├── preprocessed/                # Background subtracted, normalized, registered
├── segmentation_masks/          # Label images (16-bit or 32-bit TIFF)
├── montages/                    # Overview images for QC
└── processing_log.md            # Parameters used for each step
```

## Standard Workflow

1. **Quality control** — Inspect images for artifacts, focus issues, saturation
2. **Preprocessing** — Background subtraction, illumination correction, registration
3. **Segmentation** — Identify objects of interest (cells, nuclei, organelles)
4. **Measurement** — Extract quantitative features from segmented objects
5. **Statistical analysis** — Analyze measurements with appropriate methods
6. **Visualization** — Generate figures following conventions

## Preprocessing Standards

### Background Subtraction

- Document the method (rolling ball, flat-field correction, median filter)
- Save and document parameters (radius, reference image)
- For flat-field correction: acquire reference images during the experiment

### Illumination Correction

- Critical for quantitative comparisons across a field of view
- Use BaSiC, CIDRE, or flat-field division
- Apply per-channel, per-experiment
- Document the correction method and reference images used

### Image Registration

- Required when comparing images across time points or channels acquired sequentially
- Document the registration method and reference channel/timepoint
- Save transformation matrices for reproducibility

## Segmentation Standards

See [segmentation-standards.md](segmentation-standards.md) for detailed conventions.

Key principles:
- Always validate segmentation visually on representative images
- Document the method, parameters, and validation approach
- Save segmentation masks as labeled images (not binary)
- Handle touching objects explicitly (watershed, Voronoi, deep learning)

## Measurement Conventions

### Feature Extraction

Standard features to extract per object:

| Category | Features |
|----------|----------|
| Morphology | Area, perimeter, eccentricity, solidity, circularity |
| Intensity | Mean, median, integrated, min, max, std (per channel) |
| Texture | Haralick features, entropy (if relevant) |
| Location | Centroid (x, y), distance to nearest neighbor |

- Report features in physical units when possible (microns, not pixels)
- Document the pixel-to-micron conversion factor
- Store per-object measurements as CSV/Parquet with object ID and image ID

### Normalization

- For intensity comparisons across experiments: normalize to a reference (e.g., control well median)
- Document the normalization method and reference
- Store both raw and normalized measurements

## Modality-Specific Conventions

### Fluorescence Microscopy

- Record excitation/emission wavelengths for each channel
- Check for bleed-through between channels
- Document exposure times and laser power
- Note whether images are widefield, confocal, or light-sheet

### Brightfield / Phase Contrast

- Document illumination settings (Kohler illumination configured?)
- Note if images are suitable for quantitative intensity analysis (usually not)
- For cell counting: validate against manual counts on a subset

### Time-Lapse

- Document the time interval and total duration
- Note any drift correction applied
- Track objects across frames — document the tracking method and parameters
- Report track quality metrics (gap closing, track length distribution)

## Naming Conventions

Images and outputs should follow a consistent naming scheme:

```
[experiment]_[well/position]_[channel]_[timepoint].[ext]

# Examples:
exp001_A01_DAPI_t000.tif
exp001_A01_GFP_t000.tif
exp001_A01_segmentation_nuclei.tif
```

Document the naming convention in the experiment README.
