# Segmentation Standards

Conventions for image segmentation in mycelium-enabled repositories.

## General Principles

1. **Segmentation is a means, not an end.** Always define what biological question the segmentation serves before choosing a method.
2. **Validate against ground truth.** Every segmentation pipeline must be validated on representative images, ideally with manual annotations.
3. **Document everything.** Method, parameters, validation results, and known failure modes.

## Methods

### Classical Methods

| Method | Best For | Limitations |
|--------|----------|-------------|
| Otsu thresholding | Well-separated objects, uniform background | Fails with uneven illumination |
| Adaptive thresholding | Uneven illumination | Sensitive to block size parameter |
| Watershed | Touching round objects | Requires good seed detection |
| Active contours | Irregular shapes with clear edges | Slow, sensitive to initialization |

### Deep Learning Methods

| Method | Best For | Notes |
|--------|----------|-------|
| Cellpose | Cells and nuclei (general) | Good out-of-box, can fine-tune |
| StarDist | Star-convex shapes (nuclei) | Fast, reliable for nuclei |
| Mesmer / DeepCell | Whole-cell + nuclear segmentation | Trained on tissue data |
| Custom U-Net | Domain-specific objects | Requires training data |

### Method Selection Guide

1. **Start simple.** Try thresholding + watershed before deep learning.
2. **Use pre-trained models** (Cellpose, StarDist) before training custom models.
3. **Fine-tune** a pre-trained model on your data if out-of-box performance is insufficient.
4. **Train from scratch** only when no existing model applies to your object type.

## Segmentation Output Format

- **Labeled images**: Save as 16-bit or 32-bit TIFF with unique integer labels per object. Background = 0.
- **Do not use binary masks** unless you have guaranteed non-touching objects.
- **File naming**: `[image-name]_segmentation_[object-type].tif` (e.g., `img001_segmentation_nuclei.tif`)
- **Object tables**: CSV with object ID, centroid, area, and parent image ID

## Validation Protocol

### Minimum Validation

1. **Visual inspection**: Overlay segmentation on raw images for at least 10 representative images
2. **Edge cases**: Check images with high density, low signal, and artifacts
3. **Metrics**: If ground truth exists, report IoU (Jaccard index) and F1 score at IoU > 0.5

### Quantitative Validation

When manual ground truth is available:

| Metric | Description | Target |
|--------|-------------|--------|
| F1 @ IoU 0.5 | Detection accuracy | > 0.85 |
| Mean IoU | Segmentation quality | > 0.70 |
| Over-segmentation rate | Objects split into multiple | < 10% |
| Under-segmentation rate | Multiple objects merged | < 5% |
| False positive rate | Spurious detections | < 10% |

Report these metrics in the analysis README.

### Creating Ground Truth

- Use napari, QuPath, or Labkit for manual annotation
- Annotate at least 5-10 images covering the range of conditions
- Include difficult cases (touching objects, low signal, artifacts)
- Store ground truth in `data/processed/[experiment]/ground_truth/`
- Document the annotation protocol and who annotated

## Handling Common Challenges

### Touching Objects

- **Round objects (nuclei)**: StarDist or watershed with distance transform seeds
- **Irregular objects (cells)**: Cellpose or Voronoi expansion from nuclei
- **Dense clusters**: Consider splitting into smaller regions or using deep learning

### Variable Intensity

- Normalize or use adaptive methods
- Do not use global thresholds on images with uneven illumination
- Consider per-image or per-region parameter optimization

### 3D Segmentation

- Process slice-by-slice only if objects don't span many z-planes
- For true 3D: use 3D Cellpose, StarDist-3D, or 3D watershed
- Document the z-spacing and whether it matches xy resolution (isotropic?)
- Report 3D metrics (volume, surface area) in physical units

## Reproducibility

Every segmentation must be reproducible:

```python
# Document in run.py or run.sh:
# Method: Cellpose
# Model: cyto2
# Diameter: 30 pixels (= 15 microns at 0.5 um/px)
# Flow threshold: 0.4
# Cellprob threshold: 0.0
# GPU: True
# Version: cellpose 2.2.3
```

Pin the software version. Segmentation results can change between versions.
