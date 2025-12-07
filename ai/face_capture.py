import argparse
import csv
import json
import glob
import os
from datetime import datetime
from statistics import mean
from typing import List

import logging
import cv2
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import Config
from ai.utils import load_yolo_model, detect_faces_yolo, get_face_encodings
from models import db, Student


logger = logging.getLogger(__name__)


def average_vectors(vectors: List[List[float]]) -> List[float]:
    arr = np.array(vectors, dtype=float)
    return arr.mean(axis=0).astype(float).tolist()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--student_id", type=int, required=True)
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--save-db", dest="save_db", type=lambda x: str(x).lower() in {"1","true","yes"}, default=True)
    args = parser.parse_args()

    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

    # Prepare status info
    status = {
        "ok": False,
        "student_id": args.student_id,
        "num_frames": 0,
        "saved": False,
        "message": "",
        "error_type": None
    }

    try:
        # Initialize YOLO model
        try:
            model = load_yolo_model(Config.YOLO_MODEL_PATH)
        except Exception as e:
            logger.exception("Failed to load YOLO model: %s", e)
            status["message"] = "Lỗi tải model nhận diện khuôn mặt"
            status["error_type"] = "model_error"
            raise

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            logger.error("Cannot open camera")
            status["message"] = "Không thể mở camera"
            status["error_type"] = "camera_error"
            raise Exception("Camera not available")

        embeddings: List[List[float]] = []
        start_time = datetime.now()

        while (datetime.now() - start_time).seconds < args.duration:
            ret, frame = cap.read()
            if not ret:
                status["message"] = "Lỗi đọc khung hình từ camera"
                status["error_type"] = "frame_read_error"
                break
            boxes = detect_faces_yolo(model, frame)
            for (x1, y1, x2, y2) in boxes:
                face = frame[y1:y2, x1:x2]
                enc = get_face_encodings(face)
                if enc:
                    embeddings.append(enc)
            cv2.imshow("Register Face", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cap.release()
        cv2.destroyAllWindows()

        # Update status with captured frames
        status["num_frames"] = len(embeddings)

        if not embeddings:
            logger.info("No embeddings captured")
            status["message"] = "Không phát hiện khuôn mặt nào trong quá trình quét"
            status["error_type"] = "no_face_detected"
            # write status file and exit
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{args.student_id}_{ts}.json"), "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
            return
        
        if len(embeddings) < 10:
            logger.warning("Insufficient frames captured: %d", len(embeddings))
            status["message"] = f"Chỉ thu thập được {len(embeddings)} khung hình, cần tối thiểu 10"
            status["error_type"] = "insufficient_frames"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{args.student_id}_{ts}.json"), "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
            return

        avg_embedding = average_vectors(embeddings)

        # Save to DB
        if args.save_db:
            try:
                # Use app context for db
                from app import create_app
                app = create_app()
                with app.app_context():
                    student = Student.query.get(args.student_id)
                    if not student:
                        logger.error("Student not found: %s", args.student_id)
                        status["message"] = "Không tìm thấy sinh viên trong hệ thống"
                        status["error_type"] = "student_not_found"
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{args.student_id}_{ts}.json"), "w", encoding="utf-8") as f:
                            json.dump(status, f, ensure_ascii=False, indent=2)
                        return

                    # Prevent duplicate face across different accounts
                    from ai.face_attendance import euclidean_distance  # reuse
                    dup_owner_id = None
                    best_dist = 1e9
                    all_distances = []  # Track all distances for debugging
                    
                    for other in Student.query.all():
                        # Skip the current student being registered
                        if other.id == student.id:
                            continue
                        vec = other.get_embedding()
                        if not vec:
                            continue
                        d = euclidean_distance(avg_embedding, vec)
                        all_distances.append((other.id, d))
                        if d < best_dist:
                            best_dist = d
                            dup_owner_id = other.id

                    # Log all distances for debugging
                    if all_distances:
                        logger.info("Duplicate check for student %s - Distances to other students: %s", 
                                   args.student_id, all_distances)
                        logger.info("Best match: student_id=%s, distance=%.4f", dup_owner_id, best_dist)

                    # If closest match is another user and very close, block
                    # Using threshold of 0.25 - only block when faces are extremely similar
                    # (normalized euclidean distance < 0.25 indicates very high similarity)
                    if dup_owner_id is not None and best_dist < 0.25:
                        logger.warning("Duplicate face detected for student %s: matches student %s with distance %.4f", 
                                      args.student_id, dup_owner_id, best_dist)
                        status["message"] = f"Khuôn mặt này đã được đăng ký cho sinh viên khác (ID: {dup_owner_id}, độ tương đồng: {best_dist:.4f})"
                        status["error_type"] = "duplicate_face"
                        status["duplicate_student_id"] = dup_owner_id
                        status["distance"] = best_dist
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{args.student_id}_{ts}.json"), "w", encoding="utf-8") as f:
                            json.dump(status, f, ensure_ascii=False, indent=2)
                        return
                    else:
                        logger.info("No duplicate detected for student %s (best_dist=%.4f)", args.student_id, best_dist)

                    # Overwrite or set own embedding
                    student.set_embedding(avg_embedding)
                    db.session.commit()
                    logger.info("Saved embedding to DB for student %s", student.id)
                    status["saved"] = True
            except Exception as e:
                logger.exception("Database error while saving embedding: %s", e)
                status["message"] = f"Lỗi lưu dữ liệu: {str(e)}"
                status["error_type"] = "database_error"
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{args.student_id}_{ts}.json"), "w", encoding="utf-8") as f:
                    json.dump(status, f, ensure_ascii=False, indent=2)
                return

        # Save metadata CSV
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(Config.UPLOAD_FOLDER, f"register_meta_{args.student_id}_{ts}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["student_id", "timestamp", "num_frames", "embedding_dim"])
            writer.writerow([args.student_id, ts, len(embeddings), len(avg_embedding)])
        logger.info("Saved metadata: %s", csv_path)

        # Write final status JSON
        status["ok"] = True
        status["message"] = f"Đăng ký thành công với {len(embeddings)} khung hình"
        with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{args.student_id}_{ts}.json"), "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.exception("Registration error: %s", e)
        if not status["message"]:
            status["message"] = f"Lỗi không xác định: {str(e)}"
            status["error_type"] = "unknown_error"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{args.student_id}_{ts}.json"), "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
