"""
Live Preprocessing Pipeline — Stage 0
Runs before VGGT receives any frames.
Input:  raw user video upload (any format, any era)
Output: clean normalized frames ready for VGGT

Pipeline order matches Chapter 12 of the roadmap exactly,
with additions for modern footage problems:
orientation correction, and frame selection.

Each function returns its output path so the next step always
receives the actual file that was written. Steps that are
conditionally skipped return input_path unchanged.

VIDEO CLASSIFICATION:
metadata["is_archival"] — True for VHS/Hi8/MiniDV/Super8 digitized footage
metadata["is_modern"]   — True for iPhone/DSLR/modern camera footage
These flags are derived in probe_metadata and used throughout
to conditionally skip or adjust steps that only apply to one era.

These flags are mutually exclusive: if both would be True, is_modern wins.
If neither is True, is_archival is set as the safer default.
"""

import os
import shutil
import subprocess
import json

from helpers.probe_metadata_helpers import _detect_black_and_white


# =============================================================================
# STEP 1: PROBE VIDEO METADATA
# Must run first. Every downstream step reads from the returned dict.
# =============================================================================

def probe_metadata(input_path):
    # - Use ffprobe to read all stream and format tags
    # - Extract raw fields:
    #     codec_name, field_order, color_primaries, color_transfer,
    #     color_space, pix_fmt, width, height, r_frame_rate,
    #     duration, nb_frames, stream rotation tag
    #
    # - Derive classification flags:
    #
    #   is_archival = True if ANY of:
    #     field_order in ["tt", "bb"]              (interlaced = definitive archival)
    #     codec in ["mpeg2video", "dvvideo",        (archival codecs)
    #               "huffyuv", "ffv1"]
    #     color_primaries in ["bt470bg",            (NTSC/PAL color space)
    #                         "smpte170m"]
    #     width <= 720 and height <= 480            (SD resolution)
    #
    #   is_modern = True if ANY of:
    #     codec in ["hevc", "av1"]                 (modern codecs)
    #     color_primaries == "bt2020"              (HDR = definitively modern)
    #     pix_fmt == "yuv420p10le"                 (10-bit = modern)
    #
    #   Edge cases:
    #     h264 at 1080p with bt709 → treat as is_modern
    #     h264 at 480p or below → treat as is_archival
    #     Unknown/ambiguous → default to is_archival (safer — extra processing
    #     on a modern clip costs time, skipping it on archival breaks the pipeline)
    #
    #   Mutual exclusivity:
    #     is_archival and is_modern can both be True from the raw signals above
    #     (e.g. hevc-encoded SD resolution file). In that case is_modern wins —
    #     codec/pixel format is a stronger signal than resolution alone.
    #
    # - Derive additional flags:
    #   is_hdr          = color_primaries == "bt2020" or
    #                     color_transfer in ["arib-std-b67", "smpte2084"]
    #   is_portrait     = height > width (after accounting for rotation tag)
    #   is_interlaced   = field_order in ["tt", "bb"]
    #   is_black_and_white = detect from chroma variance across sample frames
    #   rotation        = int from stream rotation tag (0, 90, 180, 270)
    #
    # - Returns: dict with all raw and derived values
    #   {
    #     "codec": "hevc",
    #     "field_order": "progressive",
    #     "color_primaries": "bt2020",
    #     "pix_fmt": "yuv420p10le",
    #     "width": 1080, "height": 1920,
    #     "fps": 30.0, "duration": 8.4, "nb_frames": 253,
    #     "rotation": 90,
    #     "is_archival": False,
    #     "is_modern": True,
    #     "is_portrait": True,
    #     "is_interlaced": False,
    #     "is_black_and_white": False,
    #   }

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        input_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)

    video_stream = next(
        (s for s in data["streams"] if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise ValueError(f"No video stream found in {input_path}")

    codec           = video_stream.get("codec_name", "unknown")
    field_order     = video_stream.get("field_order", "progressive")
    color_primaries = video_stream.get("color_primaries", "unknown")
    color_transfer  = video_stream.get("color_transfer", "unknown")
    pix_fmt         = video_stream.get("pix_fmt", "unknown")
    width           = int(video_stream.get("width", 0))
    height          = int(video_stream.get("height", 0))
    duration        = float(video_stream.get("duration", 0))

    fps_raw = video_stream.get("r_frame_rate", "30/1")
    num, den = fps_raw.split("/")
    fps = float(num) / float(den)

    # FIX #9: nb_frames is frequently absent from container metadata (MKV, MP4).
    # Fall back to duration * fps rather than silently returning 0.
    nb_frames_raw = video_stream.get("nb_frames")
    nb_frames = int(nb_frames_raw) if nb_frames_raw else int(duration * fps)

    rotation = int(video_stream.get("tags", {}).get("rotate", 0))

    is_interlaced = field_order in ["tt", "bb"]

    is_black_and_white = _detect_black_and_white(input_path)

    is_archival = (
        is_interlaced
        or codec in ["mpeg2video", "dvvideo", "huffyuv", "ffv1"]
        or color_primaries in ["bt470bg", "smpte170m"]
        or (width <= 720 and height <= 480)
    )

    is_modern = (
        codec in ["hevc", "av1"]
        or color_primaries == "bt2020"
        or pix_fmt == "yuv420p10le"
    )

    if codec == "h264" and not is_archival:
        if width >= 1280:
            is_modern = True
        else:
            is_archival = True

    # FIX #10: is_archival and is_modern are not mutually exclusive from the
    # signals above. Resolve conflicts explicitly:
    #   - Both True  → is_modern wins (codec/format > resolution as signal)
    #   - Both False → is_archival (safer default per spec)
    if is_modern and is_archival:
        is_archival = False
    elif not is_archival and not is_modern:
        is_archival = True

    # FIX: is_hdr was documented in the docstring but never computed or returned.
    is_hdr = (
        color_primaries == "bt2020"
        or color_transfer in ["arib-std-b67", "smpte2084"]
    )

    # is_portrait accounts for rotation: if the stream tag says 90/270, the
    # pixel buffer is stored sideways, so width/height are already swapped
    # relative to the display orientation.
    if rotation in [90, 270]:
        is_portrait = width > height
    else:
        is_portrait = height > width

    return {
        "codec":              codec,
        "field_order":        field_order,
        "color_primaries":    color_primaries,
        "color_transfer":     color_transfer,
        "pix_fmt":            pix_fmt,
        "width":              width,
        "height":             height,
        "fps":                fps,
        "duration":           duration,
        "nb_frames":          nb_frames,
        "rotation":           rotation,
        "is_archival":        is_archival,
        "is_modern":          is_modern,
        "is_portrait":        is_portrait,
        "is_interlaced":      is_interlaced,
        "is_black_and_white": is_black_and_white,
        "is_hdr":             is_hdr,
    }


# Maps stream rotation tag value to the ffmpeg vf filter that corrects it.
# The tag describes how pixels are stored (e.g. rotate=90 means pixels are
# stored CW), so the correction is always the inverse transform.
_ROTATION_TO_VF = {
    90:  "transpose=2",   # stored CW  → correct with CCW
    180: "vflip,hflip",
    270: "transpose=1",   # stored CCW → correct with CW
}


# =============================================================================
# STEP 2: ORIENTATION CORRECTION
# Modern footage only — archival cameras were always held landscape.
# Conditioned on is_portrait and rotation.
# =============================================================================

def correct_orientation(input_path, output_path, is_modern, is_portrait, rotation):
    # - Modern footage only (is_modern=True) — archival cameras were always
    #   held landscape, so is_modern guards against spurious rotation tags
    #   on archival files.
    #
    # - FIX #8: transpose directions are the inverse of the rotation tag.
    #   The tag describes how pixels are stored relative to display orientation,
    #   so the correction must undo that:
    #   rotation=90  → pixels stored CW  → apply transpose=2 (CCW) to fix
    #   rotation=180 → pixels stored 180 → apply vflip,hflip to fix
    #   rotation=270 → pixels stored CCW → apply transpose=1 (CW) to fix
    #   rotation=0   → no correction needed
    #
    # - Use -noautorotate so ffmpeg does not silently apply its own rotation
    # - Verify output width >= height before returning (landscape check)
    # - Returns: output_path if corrected, input_path if skipped

    if not is_modern:
        return input_path

    if rotation == 0 and not is_portrait:
        return input_path

    vf = _ROTATION_TO_VF.get(rotation)
    if vf is None:
        # rotation=0 but is_portrait — no tag to guide correction, skip
        return input_path

    subprocess.run([
        "ffmpeg", "-y",
        "-noautorotate",
        "-i", input_path,
        "-vf", vf,
        "-metadata:s:v:0", "rotate=0",
        output_path
    ], check=True)

    return output_path


# =============================================================================
# STEP 3: DEINTERLACING + NOISE REDUCTION + DEFLICKER
# Chapter 12, Step 1.
# Deinterlacing: archival only — conditioned on is_interlaced
# Noise reduction + deflicker: all footage, but strength differs by era
# =============================================================================

def deinterlace_and_denoise(input_path, output_path, is_interlaced, is_archival, is_modern):
    # - Deinterlacing (yadif=mode=1):
    #     Only run if is_interlaced == True
    #     is_interlaced will be True for VHS/Hi8/early camcorder
    #     is_interlaced will be False for all modern footage — skip yadif
    #
    # - Noise reduction (hqdn3d):
    #     Always run, but adjust strength by era:
    #     is_archival → hqdn3d=4:3:6:4.5   (strong — tape noise is heavy)
    #     is_modern   → hqdn3d=2:1:3:2     (light — just compression artifact smoothing)
    #
    # - Deflicker:
    #     Always run for is_archival (auto-exposure flicker is universal in archival)
    #     Skip for is_modern (modern cameras have stable exposure)
    #
    # - Run all applicable filters as a single chained ffmpeg pass
    # - Returns: output_path
    pass  # returns: str


# =============================================================================
# STEP 4: VIDEO STABILIZATION
# Chapter 12, Step 2. Always runs — shake exists in both eras.
# Strength differs: archival footage needs heavier smoothing.
# =============================================================================

def stabilize(input_path, output_path, temp_dir, is_archival, is_modern):
    # - Pass 1 — vidstabdetect:
    #     is_archival → shakiness=8, accuracy=15  (heavier analysis)
    #     is_modern   → shakiness=5, accuracy=15  (lighter analysis)
    #
    # FIX #14: transforms file is namespaced under a UUID so concurrent
    # pipeline runs on the same VM do not collide on transforms.trf.
    #     result=temp_dir/{job_id}_transforms.trf
    #
    # - Pass 2 — vidstabtransform:
    #     input=temp_dir/{job_id}_transforms.trf
    #     is_archival → smoothing=15, crop=black  (heavier smoothing)
    #     is_modern   → smoothing=10, crop=black  (lighter smoothing)
    #
    # - Always runs — both archival and modern footage has shake
    # - Returns: output_path
    pass  # returns: str


# =============================================================================
# STEP 5: PERSON DETECTION AND TRACKING — Stage 0b
# Always runs. SAM 2.1 + DeepFace. Runs before SeedVR2 (heavy compute)
# as specified in roadmap: "must complete before any GPU-intensive work begins"
# =============================================================================

def detect_and_track_persons(input_path, masks_dir, thumbnails_dir):
    # - Always runs regardless of video era
    # - Load SAM 2.1 model (Apache 2.0)
    # - Auto-detect all people in first frame using bounding box proposals
    # - Track each person mask across all frames using SAM 2.1 memory bank
    #   SAM 2.1 processes ~30fps on A100 — fast, intentionally before SeedVR2
    #
    # - For each unique person:
    #     Save per-frame masks to masks_dir/{person_id}/frame_{n:06d}.png
    #     Extract face embedding with DeepFace (MIT license)
    #     Save thumbnail to thumbnails_dir/{person_id}_thumb.jpg
    #     Thumbnail shown in Stage 0c person selection UI
    #
    # - Face embeddings saved to masks_dir/face_embeddings.json
    #   passed through pipeline to CodeFormer in Stage 6 for
    #   identity-guided inpainting
    #
    # - Edge case: if two person masks merge (occlusion), flag in
    #   masks_dir/ambiguous_frames.json — SAM 2.1 memory bank will
    #   re-separate when they diverge
    #
    # - Returns: list of person_ids e.g. ["person_0", "person_1"]
    pass  # returns: list[str]


# =============================================================================
# STEP 6: PERSON MASKING FOR VGGT
# Always runs. Uses masks from Step 5 to inpaint people out before
# SeedVR2 so the upscaler never learns people as static scene geometry.
# =============================================================================

def mask_people_for_vggt(input_path, output_path, masks_dir, person_ids):
    # - Always runs regardless of video era
    # - For each frame, load all person masks from masks_dir
    # - Combine all person masks into a single foreground mask
    # - Inpaint masked regions with cv2.inpaint (Telea method)
    #   replaces people with plausible background pixels
    # - Write inpainted frames as video to output_path
    # - Returns: output_path
    pass  # returns: str


# =============================================================================
# STEP 7: RESTORATION AND UPSCALING
# Chapter 12, Step 3. Always runs but tool selection differs by era.
# Runs after person masking so SeedVR2 sees clean static scene only.
# =============================================================================

def restore_and_upscale(input_path, output_path, is_archival, is_modern, duration):
    # - Always runs, tool selection conditioned on era:
    #
    #   is_archival:
    #     Primary: SeedVR2 7B model (ByteDance, Apache 2.0)
    #       batch_size must follow 4n+1 formula: 1, 5, 9, 13, 17, 21...
    #       LAB color correction enabled
    #       temporal_overlap: 4-8 frames between chunks
    #     Reason: archival footage needs temporal attention to enforce
    #     inter-frame texture consistency that yadif+hqdn3d alone can't provide
    #
    #   is_modern AND duration < 10 seconds:
    #     Acceptable to use Real-ESRGAN as fallback
    #     Reason: short clean clips have less temporal consistency risk
    #     Note in output metadata that fallback was used
    #
    #   is_modern AND duration >= 10 seconds:
    #     Still use SeedVR2 — temporal consistency matters at any length
    #
    # - Returns: output_path
    pass  # returns: str


# =============================================================================
# STEP 8: COLORIZATION
# Chapter 12, Step 5. Archival only — conditioned on is_black_and_white.
# Modern footage will never be black and white.
# =============================================================================

def colorize(input_path, output_path, is_black_and_white):
    # - Check is_black_and_white — if False, skip and return input_path
    #   is_modern footage is never black and white so skip is guaranteed there
    #   is_archival may be True or False depending on era of footage
    #
    # - If True: run DeOldify 'video' model (MIT license, free commercial use)
    #   Use 'video' model not 'artistic' — NoGAN training = stable, no flicker
    #
    # - MANDATORY on colorized output: set colorization_applied = True
    #   in clip metadata so UI displays disclosure before delivery:
    #   "Colors in this clip were predicted by AI based on context and
    #    learned patterns. They represent a plausible interpretation,
    #    not a verified historical record."
    #
    # - Returns: output_path if colorized, input_path if skipped
    pass  # returns: str


# =============================================================================
# STEP 9: FRAME SELECTION
# Always runs. Fixes back-and-forth movement, respects VGGT memory budget.
# =============================================================================

def select_frames(input_path, nb_frames, target_count=80):
    # FIX #11: accepts nb_frames so the function can validate that
    # target_count does not exceed the actual frame count of the video.
    # If the video is shorter than target_count, the returned list will
    # contain at most nb_frames entries — callers must not assume exactly
    # target_count indices are returned.
    #
    # - Always runs regardless of video era
    # - Extract all frames as grayscale into memory
    # - Compute Farneback optical flow between every consecutive frame pair
    # - Calculate per-frame cumulative camera displacement (x, y)
    # - Detect direction reversals: frames where x-displacement sign flips
    # - Score each frame by unique spatial coverage contributed
    # - Select min(target_count, nb_frames) frames maximizing viewpoint diversity:
    #     Prefer frames with net forward displacement
    #     Skip redundant frames from return movement
    #     Skip frames where flow magnitude variance > threshold (motion blur)
    #     target_count=80: safe within VGGT GPU memory budget at 8fps
    # - Returns: selected_indices — sorted list of integer frame indices,
    #            length <= min(target_count, nb_frames)
    pass  # returns: list[int]


# =============================================================================
# STEP 10: FRAME EXTRACTION AND SCALE NORMALIZATION
# Always runs. Final output — the only thing VGGT ever sees.
# =============================================================================

def normalize_and_extract_frames(input_path, output_dir, selected_indices):
    # - Always runs regardless of video era
    # - Create output_dir if it does not exist
    # - Extract only frames at selected_indices from input_path
    #   using ffmpeg select filter
    # - Scale each frame to 960x540 (landscape 16:9)
    # - Write as sequential JPEGs: frame_000001.jpg, frame_000002.jpg ...
    #   Sequential numbering from 1 required by VGGT load_images()
    # - Returns: output_dir (path to frames directory)
    pass  # returns: str


# =============================================================================
# MAIN PIPELINE ORCHESTRATOR
# =============================================================================

def preprocess(input_path, output_dir):

    os.makedirs(output_dir, exist_ok=True)
    temp_dir   = os.path.join(output_dir, "temp")
    masks_dir  = os.path.join(output_dir, "masks")
    thumbs_dir = os.path.join(output_dir, "thumbnails")
    frames_dir = os.path.join(output_dir, "frames")
    for d in [temp_dir, masks_dir, thumbs_dir, frames_dir]:
        os.makedirs(d, exist_ok=True)

    # FIX #15: wrap the entire pipeline in try/except so temp files are
    # cleaned up on failure rather than accumulating on the VM disk.
    try:
        # Step 1: probe — derives is_archival, is_modern, is_portrait,
        #                 is_interlaced, is_black_and_white, rotation
        #                 every downstream step reads from this dict
        metadata = probe_metadata(input_path)

        # Step 2: modern only — skips automatically if already landscape or archival.
        # Passes only the three scalars correct_orientation needs.
        oriented_path = correct_orientation(
            input_path,
            os.path.join(temp_dir, "step2_oriented.mp4"),
            metadata["is_modern"],
            metadata["is_portrait"],
            metadata["rotation"],
        )

        # Step 3: yadif = archival only, hqdn3d + deflicker = always
        #         strength adjusted by era via metadata
        denoised_path = deinterlace_and_denoise(
            oriented_path,
            os.path.join(temp_dir, "step3_denoised.mp4"),
            metadata["is_interlaced"],
            metadata["is_archival"],
            metadata["is_modern"],
        )

        # Step 4: always runs, strength adjusted by era via metadata
        stabilized_path = stabilize(
            denoised_path,
            os.path.join(temp_dir, "step4_stabilized.mp4"),
            temp_dir,
            metadata["is_archival"],
            metadata["is_modern"],
        )

        # Step 5: always runs — before SeedVR2 as roadmap specifies
        person_ids = detect_and_track_persons(
            stabilized_path,
            masks_dir,
            thumbs_dir,
        )

        # Step 6: always runs — people removed before upscaling
        masked_path = mask_people_for_vggt(
            stabilized_path,
            os.path.join(temp_dir, "step6_masked.mp4"),
            masks_dir,
            person_ids,
        )

        # Step 7: always runs — SeedVR2 primary, Real-ESRGAN fallback for
        #         short modern clips only
        restored_path = restore_and_upscale(
            masked_path,
            os.path.join(temp_dir, "step7_restored.mp4"),
            metadata["is_archival"],
            metadata["is_modern"],
            metadata["duration"],
        )

        # Step 8: archival only — skips automatically if not black and white
        colorized_path = colorize(
            restored_path,
            os.path.join(temp_dir, "step8_colorized.mp4"),
            metadata["is_black_and_white"],
        )

        # Step 9: always runs.
        # FIX #11: pass nb_frames so select_frames can cap against actual
        # frame count and never return indices beyond the video length.
        selected_indices = select_frames(
            colorized_path,
            nb_frames=metadata["nb_frames"],
            target_count=80,
        )

        # Step 10: always runs — final output for VGGT
        # final_frames_dir kept separate from frames_dir to avoid overwriting
        # the directory path defined at the top of this function
        final_frames_dir = normalize_and_extract_frames(
            colorized_path,
            frames_dir,
            selected_indices,
        )

    except Exception:
        # Clean up all temp files before re-raising so the VM disk does not
        # accumulate partial outputs from failed jobs.
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return {
        "frames_dir":       final_frames_dir,
        "masks_dir":        masks_dir,
        "thumbnails_dir":   thumbs_dir,
        "person_ids":       person_ids,
        "selected_indices": selected_indices,
        "metadata":         metadata,
    }


# FIX #16: __main__ block now runs the full pipeline, not just probe_metadata.
# output_dir argument was accepted but silently unused in the original.
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: preprocess.py <input_path> <output_dir>")
        sys.exit(1)
    input_path = sys.argv[1]
    output_dir = sys.argv[2]
    result = preprocess(input_path, output_dir)
    # selected_indices may contain numpy int64 from optical flow — cast to
    # plain int so json.dumps does not raise TypeError.
    result["selected_indices"] = [int(i) for i in result["selected_indices"]]
    print(json.dumps(result, indent=2))
