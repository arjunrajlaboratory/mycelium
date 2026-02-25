# Segmentation Benchmark: [Object Type]

## Purpose

[What objects are being segmented? What downstream analysis depends on this segmentation?]

## Status

**Status**: draft

## Dataset

- **Images**: [reference to data/MANIFEST.md entry]
- **Modality**: [fluorescence/brightfield/phase contrast]
- **Object type**: [nuclei/cells/organelles/other]
- **Pixel size**: [X] microns/pixel
- **Image dimensions**: [W x H pixels, N channels, Z slices if 3D]

## Ground Truth

- **Annotation tool**: [napari/QuPath/Labkit/other]
- **Annotator**: [who annotated]
- **Images annotated**: [N images]
- **Objects annotated**: [N objects total]
- **Stored at**: `data/processed/[experiment]/ground_truth/`

## Methods Tested

### Method 1: [Name]

- **Software**: [package and version]
- **Parameters**:
  - [param1]: [value]
  - [param2]: [value]
- **Preprocessing**: [any preprocessing applied]
- **Runtime**: [time per image]

### Method 2: [Name]

- **Software**: [package and version]
- **Parameters**:
  - [param1]: [value]
  - [param2]: [value]

## Results

| Method | F1 @ IoU 0.5 | Mean IoU | Over-seg % | Under-seg % | FP % | Runtime |
|--------|--------------|----------|------------|-------------|------|---------|
| Method 1 | | | | | | |
| Method 2 | | | | | | |

## Selected Method

**Choice**: [Method name]

**Rationale**: [Why this method was selected — balance of accuracy, speed, robustness]

## Known Failure Modes

- [Failure mode 1: when does the segmentation fail?]
- [Failure mode 2]

## Representative Results

| Condition | Example Image |
|-----------|--------------|
| Typical | `outputs/figures/seg_typical.png` |
| High density | `outputs/figures/seg_dense.png` |
| Low signal | `outputs/figures/seg_low_signal.png` |

## Reproducibility

```bash
cd analysis/[analysis-name]
bash run.sh
```
