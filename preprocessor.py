"""
Live Preprocessing Pipeline
Runs before VGGT receives any frames.
Input: raw user video upload
Output: clean, normalized frames ready for VGGT
"""

import os
import subprocess
import json


def preprocess(input_path, output_dir):

    # -------------------------------------------------------------------------
    # STEP 1: PROBE VIDEO METADATA
    # - Read rotation tag from stream metadata
    # - Detect color space (BT.2020 HDR vs BT.709 SDR)
    # - Get resolution, fps, duration, frame count
    # - Determine if portrait or landscape from width/height
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # STEP 2: HDR TO SDR TONEMAPPING
    # - Only run if color_primaries == bt2020 (iPhone Dolby Vision / HDR10)
    # - Use zscale + hable tonemap to mathematically convert pixel values
    # - Output must be yuv420p BT.709 so VGGT receives expected color range
    # - If already SDR, skip this step entirely
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # STEP 3: ORIENTATION CORRECTION
    # - Read rotation tag detected in Step 1
    # - Strip metadata rotation flag with -noautorotate
    # - Apply correct transpose filter manually based on rotation value
    #     90  degrees -> transpose=1
    #     180 degrees -> transpose=2,transpose=2
    #     270 degrees -> transpose=2
    #     0   degrees -> no transpose needed
    # - Output must be landscape (width > height)
    # - If already landscape with no rotation tag, skip
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # STEP 4: VIDEO STABILIZATION
    # - Run FFmpeg vidstabdetect pass to generate transforms.trf
    # - Run FFmpeg vidstabtransform pass to apply stabilization
    # - Settings: shakiness=8, accuracy=15, smoothing=15, crop=black
    # - Reduces hand wobble so optical flow in Step 6 is cleaner
    # - Always run regardless of apparent stability
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # STEP 5: PERSON DETECTION AND MASKING (SAM 2.1)
    # - Load SAM 2.1 model
    # - Auto-detect people in first frame using bounding box proposals
    # - Track each person mask across all frames using SAM 2.1 memory bank
    # - For each frame: inpaint masked person regions with background fill
    #     using cv2.inpaint (Telea method) so VGGT sees static scene only
    # - Save person masks separately for Stage 0b person selection UI later
    # - Edge case: if two people merge into one mask, flag as ambiguous
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # STEP 6: FRAME SELECTION (fix back-and-forth movement)
    # - Compute optical flow between every consecutive frame pair
    # - Calculate cumulative camera displacement across full clip
    # - Detect direction reversals (where flow flips sign)
    # - Select subset of frames that maximizes viewpoint diversity:
    #     - Prefer frames with net forward displacement
    #     - Skip redundant frames from return movement
    #     - Skip frames with motion blur (high flow magnitude variance)
    #     - Target 60-100 frames total for VGGT memory budget at 8fps
    # - Output ordered list of selected frame indices
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # STEP 7: FRAMERATE AND SCALE NORMALIZATION
    # - Extract only the selected frames from Step 6
    # - Scale to 960x540 (landscape 16:9)
    # - Output as sequential JPEGs: frame_000001.jpg, frame_000002.jpg ...
    # - Naming format must match what VGGT's load_images() expects
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # STEP 8: SCENE GRAPH INITIALIZATION
    # - Create scene_graph.json with per-frame metadata shell
    # - Fields to populate now: frame_index, source_timestamp, brightness
    # - Fields left empty for downstream stages to fill:
    #     motion_category, gaze_direction, reconstruction_confidence
    # - Create scene_graph_confidence.npz placeholder (empty, filled by VGGT)
    # -------------------------------------------------------------------------


    pass


if __name__ == "__main__":
    import sys
    input_path = sys.argv[1]
    output_dir = sys.argv[2]
    preprocess(input_path, output_dir)
