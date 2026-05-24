"""
Face verification service using DeepFace with OpenFace model.
Compares a reference image against multiple frames and returns average similarity.
"""
from loguru import logger
import numpy as np
from typing import List
import cv2

from app.config import settings

# ── Module-level cache (pre-warm support) ──────────────────────────────────
# Caches the deepface module import across Celery task invocations.
# With worker_pool='solo' the worker is a single long-lived process,
# so this dict survives between tasks — exactly what we want.
_DEEPFACE_MODULE = None


def _load_deepface():
    """Lazy import DeepFace — result is cached for reuse across tasks in the same process."""
    global _DEEPFACE_MODULE
    if _DEEPFACE_MODULE is not None:
        return _DEEPFACE_MODULE
    try:
        from deepface import DeepFace
        _DEEPFACE_MODULE = DeepFace
        return DeepFace
    except ImportError:
        raise RuntimeError("deepface is not installed. Run: pip install deepface")


def prewarm_deepface_model() -> None:
    """
    Pre-loads the DeepFace model weights into this worker process's memory.
    Idempotent — exits immediately if already warm.
    Safe to call multiple times.
    """
    global _DEEPFACE_MODULE
    if _DEEPFACE_MODULE is not None:
        logger.info("[PreWarm] DeepFace already cached — skipping.")
        return
    DeepFace = _load_deepface()
    model_name = getattr(settings, "FACE_MODEL_NAME", "OpenFace")
    logger.info(f"[PreWarm] Warming DeepFace '{model_name}' model weights...")
    try:
        # build_model forces weight loading + stores inside DeepFace's own internal cache.
        # The next call to DeepFace.represent() finds the weights already in memory.
        DeepFace.build_model(model_name)
        logger.info(f"[PreWarm] DeepFace '{model_name}' model ready \u2713")
    except Exception as e:
        logger.warning(f"[PreWarm] build_model raised: {e} — model will load on first inference call.")


def get_face_embedding(image_path: str, DeepFace) -> np.ndarray | None:
    """Extract face embedding from an image using the configured model."""
    try:
        # Default model_name to OpenFace if not in settings
        model_name = getattr(settings, "FACE_MODEL_NAME", "OpenFace")
        result = DeepFace.represent(
            img_path=image_path,
            model_name=model_name,
            enforce_detection=True,
            detector_backend="opencv",
        )
        if result and len(result) > 0:
            return np.array(result[0]["embedding"])
        return None
    except Exception as e:
        logger.warning(f"Could not extract embedding from {image_path}: {e}")
        return None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def verify_faces(
    reference_image_path: str,
    frame_paths: List[str],
) -> dict:
    """
    Compare reference image against a list of frame paths.
    Returns average similarity score between 0–100.
    """
    DeepFace = _load_deepface()
    threshold = getattr(settings, "FACE_SIMILARITY_THRESHOLD", 0.6)

    logger.info(f"Extracting embedding from reference image: {reference_image_path}")
    ref_embedding = get_face_embedding(reference_image_path, DeepFace)

    if ref_embedding is None:
        return {
            "success": False,
            "error": "No face detected in reference image",
            "average_similarity": 0,
            "frame_similarities": [],
            "matched_frames": 0,
            "total_frames": len(frame_paths),
        }

    similarities = []
    frame_results = []

    for i, frame_path in enumerate(frame_paths):
        try:
            frame_embedding = get_face_embedding(frame_path, DeepFace)
            if frame_embedding is None:
                frame_results.append({
                    "frame_index": i,
                    "similarity": 0,
                    "face_detected": False,
                })
                continue

            # Compute cosine similarity
            cos_sim = cosine_similarity(ref_embedding, frame_embedding)
            # Convert to 0–100 scale
            similarity_score = round(max(0.0, cos_sim) * 100, 2)

            similarities.append(similarity_score)
            frame_results.append({
                "frame_index": i,
                "similarity": similarity_score,
                "face_detected": True,
                "is_match": cos_sim >= threshold,
            })

            logger.info(f"Frame {i}: cosine_sim={cos_sim:.4f}, score={similarity_score}")

        except Exception as e:
            logger.warning(f"Error processing frame {i}: {e}")
            frame_results.append({
                "frame_index": i,
                "similarity": 0,
                "face_detected": False,
                "error": str(e),
            })

    avg_similarity = round(float(np.mean(similarities)), 2) if similarities else 0.0
    matched_frames = sum(
        1 for r in frame_results
        if r.get("is_match", False)
    )

    return {
        "success": True,
        "average_similarity": avg_similarity,
        "frame_similarities": frame_results,
        "matched_frames": matched_frames,
        "total_frames": len(frame_paths),
        "frames_with_face": len(similarities),
        "is_verified": avg_similarity >= (threshold * 100),
    }
