from loguru import logger
import os
import shutil
import tempfile
from app.workers.celery_app import celery_app
from app.services.face_verification.face_service import verify_faces, prewarm_deepface_model
from app.services.face_verification.liveness_service import detect_blinks_in_video, prewarm_landmarker
from app.services.face_verification.video_utils import extract_frames_from_video, get_minio_object_bytes, save_bytes_to_local
from app.db.redis_client import redis_client
import json

@celery_app.task(name="prewarm_face_models", queue="face_verification")
@logger.catch
def prewarm_face_models(session_ulid: str = "") -> dict:
    """
    Pre-loads DeepFace (OpenFace) and MediaPipe FaceLandmarker into the
    worker process memory before the user reaches the face capture screen.

    Dispatched at two trigger points:
      Stage 1 — when document upload completes (user is reviewing OCR data)
      Stage 2 — when the orchestrator returns RENDER_FACE_VERIFICATION

    Idempotent: both underlying prewarm functions exit immediately if the
    model is already cached. Safe to dispatch multiple times.
    """
    logger.info(f"[PreWarm][{session_ulid or 'global'}] Starting face model pre-warm...")

    deepface_ok = False
    landmarker_ok = False

    try:
        prewarm_deepface_model()
        deepface_ok = True
    except Exception as e:
        logger.warning(f"[PreWarm][{session_ulid}] DeepFace prewarm failed (non-fatal): {e}")

    try:
        prewarm_landmarker()
        landmarker_ok = True
    except Exception as e:
        logger.warning(f"[PreWarm][{session_ulid}] MediaPipe prewarm failed (non-fatal): {e}")

    status = "warmed" if (deepface_ok and landmarker_ok) else "partial"
    logger.info(f"[PreWarm][{session_ulid}] Complete. deepface={deepface_ok} landmarker={landmarker_ok}")
    return {"status": status, "deepface": deepface_ok, "landmarker": landmarker_ok}




@celery_app.task(name="verify_face_liveness_async")
@logger.catch
def verify_face_liveness_async(session_ulid: str, live_photo_path: str, live_video_path: str):
    """
    Background task to perform face matching and liveness detection.
    
    Args:
        session_ulid: The unique session ID.
        live_photo_path: MinIO path to the live-captured selfie (e.g., "temp/ulid/live_photo.jpg").
        live_video_path: MinIO path to the live-captured video (e.g., "temp/ulid/live_video.webm").
    """
    temp_dir = tempfile.mkdtemp(prefix=f"face_verify_{session_ulid}_")
    logger.info(f"[FaceVerify][{session_ulid}] Starting background task in {temp_dir}")
    
    try:
        # 1. Download files from MinIO to local temp storage
        photo_bytes = get_minio_object_bytes("temp", live_photo_path.replace("temp/", ""))
        video_bytes = get_minio_object_bytes("temp", live_video_path.replace("temp/", ""))
        
        local_photo_path = save_bytes_to_local(photo_bytes, os.path.join(temp_dir, "ref_photo.jpg"))
        local_video_path = save_bytes_to_local(video_bytes, os.path.join(temp_dir, "liveness_video.webm"))
        
        # 2. Extract frames for face matching
        frames_dir = os.path.join(temp_dir, "frames")
        frame_paths = extract_frames_from_video(local_video_path, frames_dir, max_frames=10)
        
        if not frame_paths:
            error_res = {"status": "error", "message": "Failed to extract frames from video."}
            redis_client.setex(f"face_verification:{session_ulid}", 3600, json.dumps(error_res))
            return error_res

        # 3. Perform Face Verification
        logger.info(f"[FaceVerify][{session_ulid}] Running face matching...")
        face_result = verify_faces(local_photo_path, frame_paths)
        
        # 4. Perform Liveness Detection
        logger.info(f"[FaceVerify][{session_ulid}] Running liveness detection...")
        liveness_result = detect_blinks_in_video(local_video_path)
        
        # 5. Compile Result
        # ── Defensive extraction: verify_faces returns {success: False, error: ...}
        #    when no face is detected in the reference image or all frames fail.
        #    Only access is_verified / average_similarity on the success path.
        if face_result.get("success"):
            is_verified = face_result.get("is_verified", False)
            avg_similarity = face_result.get("average_similarity", 0)
            matched_frames = face_result.get("matched_frames", 0)
            total_frames = face_result.get("total_frames", len(frame_paths))
            face_error = None
        else:
            is_verified = False
            avg_similarity = 0
            matched_frames = 0
            total_frames = face_result.get("total_frames", len(frame_paths))
            face_error = face_result.get("error", "Face verification failed.")
            logger.warning(
                f"[FaceVerify][{session_ulid}] Face comparison failed: {face_error}"
            )

        final_result = {
            "status": "success" if face_error is None else "error",
            "face_verification": {
                "is_verified": is_verified,
                "average_similarity": avg_similarity,
                "matched_frames": matched_frames,
                "total_frames": total_frames,
            },
            "liveness": {
                "is_live": liveness_result.is_live,
                "blink_count": liveness_result.blink_count,
                "confidence": liveness_result.confidence,
                "message": liveness_result.message,
            },
            "overall_verdict": is_verified and liveness_result.is_live,
        }

        # Attach the face-service error so the orchestrator / UI can surface it
        if face_error:
            final_result["message"] = face_error
        
        # 5.1 Persistence Note: Orchestrator handles DB sync during status polling.
        
        logger.info(f"[FaceVerify][{session_ulid}] Completed. Verdict: {final_result['overall_verdict']}")
        
        # 6. Save to Redis for polling
        redis_client.setex(f"face_verification:{session_ulid}", 3600, json.dumps(final_result))
        return final_result

    except Exception as e:
        logger.exception(f"[FaceVerify][{session_ulid}] Task failed: {e}")
        error_res = {"status": "error", "message": str(e)}
        redis_client.setex(f"face_verification:{session_ulid}", 3600, json.dumps(error_res))
        return error_res
        
    finally:
        # Cleanup local temp files
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info(f"[FaceVerify][{session_ulid}] Cleaned up temp directory.")
