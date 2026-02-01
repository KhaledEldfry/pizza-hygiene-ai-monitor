"""
detector.py - Detection Service
================================
يستقبل الـ frames من RabbitMQ عبر frame_reader
يشغل YOLO detection
يستخدم نفس لوجيك الـ HandTracker من main.py بدقة
يحفظ المخالفات في PostgreSQL
يبعت الـ frames المـ annotated في results_queue
"""

import cv2
import pika
import json
import os
import base64
import logging
import time
import psycopg2
from datetime import datetime
from ultralytics import YOLO
import numpy as np
from collections import defaultdict
import torch
import gc

# ---------------- Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------- Configuration ----------------
CONF_THRESHOLD = 0.3
IOU_THRESHOLD = 0.2
HAND_TIMEOUT_FRAMES = 60
SCOOPER_MEMORY_FRAMES = 15
VIOLATION_DELAY_FRAMES = 10

# Memory optimization - نقلل الـ JPEG quality عشان نوفر RAM
JPEG_QUALITY = 70


def box_iou(box1, box2):
    x1, y1, x2, y2 = box1
    x1_, y1_, x2_, y2_ = box2
    xi1 = max(x1, x1_)
    yi1 = max(y1, y1_)
    xi2 = min(x2, x2_)
    yi2 = min(y2, y2_)
    inter_area = max(0, xi2 - xi1) * max(0, yi2 - yi1)
    box1_area = (x2 - x1) * (y2 - y1)
    box2_area = (x2_ - x1_) * (y2_ - y1_)
    union_area = box1_area + box2_area - inter_area
    return inter_area / union_area if union_area > 0 else 0


def box_center(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def is_in_roi(box, roi):
    cx, cy = box_center(box)
    x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
    return x <= cx <= x + w and y <= cy <= y + h


def is_near_pizza(hand_box, pizza_box, margin=0.5):
    hx, hy = box_center(hand_box)
    px1, py1, px2, py2 = pizza_box
    pw, ph = px2 - px1, py2 - py1
    exp_x1 = px1 - pw * margin
    exp_y1 = py1 - ph * margin
    exp_x2 = px2 + pw * margin
    exp_y2 = py2 + ph * margin
    return exp_x1 <= hx <= exp_x2 and exp_y1 <= hy <= exp_y2


def is_scooper_at_pizza(scooper_boxes, pizza_box, margin=0.4):
    for s_box in scooper_boxes:
        sx, sy = box_center(s_box)
        px1, py1, px2, py2 = pizza_box
        pw, ph = px2 - px1, py2 - py1
        exp_x1 = px1 - pw * margin
        exp_y1 = py1 - ph * margin
        exp_x2 = px2 + pw * margin
        exp_y2 = py2 + ph * margin
        if exp_x1 <= sx <= exp_x2 and exp_y1 <= sy <= exp_y2:
            return True
    return False


class HandTracker:
    def __init__(self, rois):
        self.next_id = 0
        self.hands = {}
        self.violations = []
        self.rois = rois
        self.scooper_history = []
        self.pending_violations = {}

    def update(self, hand_boxes, scooper_boxes, pizza_boxes, frame_num):
        # Update scooper history
        self.scooper_history.append(scooper_boxes)
        if len(self.scooper_history) > SCOOPER_MEMORY_FRAMES:
            self.scooper_history.pop(0)

        all_recent_scoopers = []
        for hist_scoopers in self.scooper_history:
            all_recent_scoopers.extend(hist_scoopers)

        matched_hands = set()
        new_hands = []

        # Match existing hands
        for hand_box in hand_boxes:
            matched = False
            for hand_id, hand_data in self.hands.items():
                if hand_id in matched_hands:
                    continue
                if box_iou(hand_box, hand_data["box"]) > IOU_THRESHOLD:
                    old_state = hand_data["state"]
                    hand_data["box"] = hand_box
                    hand_data["frames_since_update"] = 0

                    # Check ROI
                    current_roi = None
                    for roi in self.rois:
                        if is_in_roi(hand_box, roi):
                            current_roi = roi
                            break

                    # State machine
                    if current_roi:
                        if old_state != "in_roi":
                            logger.info(
                                f"  Hand {hand_id} ENTERED ROI: {current_roi['name']}"
                            )
                        hand_data["state"] = "in_roi"
                        hand_data["roi_name"] = current_roi["name"]

                    elif old_state == "in_roi":
                        logger.info(
                            f"  Hand {hand_id} LEFT ROI '{hand_data['roi_name']}' - tracking..."
                        )
                        hand_data["state"] = "tracking_to_pizza"

                    elif hand_data["state"] == "tracking_to_pizza":
                        for pizza_box in pizza_boxes:
                            if is_near_pizza(hand_box, pizza_box):
                                logger.info(
                                    f"  Hand {hand_id} at pizza - checking for scooper..."
                                )
                                self.pending_violations[hand_id] = {
                                    "frame": frame_num,
                                    "pizza_box": pizza_box,
                                    "roi_name": hand_data["roi_name"],
                                    "delay_counter": 0,
                                }
                                hand_data["state"] = "waiting_at_pizza"
                                break

                    elif hand_data["state"] == "waiting_at_pizza":
                        pass

                    matched_hands.add(hand_id)
                    matched = True
                    break

            if not matched:
                new_hands.append(hand_box)

        # Add new hands (only from ROI)
        for hand_box in new_hands:
            in_roi = False
            roi_name = None
            for roi in self.rois:
                if is_in_roi(hand_box, roi):
                    in_roi = True
                    roi_name = roi["name"]
                    break
            if in_roi:
                self.hands[self.next_id] = {
                    "box": hand_box,
                    "state": "in_roi",
                    "frames_since_update": 0,
                    "roi_name": roi_name,
                }
                logger.info(f"  New Hand {self.next_id} in ROI '{roi_name}'")
                self.next_id += 1

        # Check pending violations
        # الفرق الوحيد: نجمع كل المخالفات في list مش بس الأخيرة
        resolved_pending = []
        new_violations = []  # list مش single value

        for hand_id, pending in self.pending_violations.items():
            pending["delay_counter"] += 1

            if is_scooper_at_pizza(all_recent_scoopers, pending["pizza_box"]):
                logger.info(f"  Scooper detected at pizza for Hand {hand_id} - OK!")
                resolved_pending.append(hand_id)
                if hand_id in self.hands:
                    self.hands[hand_id]["state"] = "done"

            elif pending["delay_counter"] >= VIOLATION_DELAY_FRAMES:
                violation = {
                    "frame": frame_num,
                    "hand_id": hand_id,
                    "roi_name": pending["roi_name"],
                }
                self.violations.append(violation)
                new_violations.append(violation)  # نحفظ كل المخالفات
                logger.warning(
                    f"VIOLATION #{len(self.violations)} - No scooper from '{pending['roi_name']}'!"
                )
                resolved_pending.append(hand_id)
                if hand_id in self.hands:
                    self.hands[hand_id]["state"] = "done"

        for hand_id in resolved_pending:
            del self.pending_violations[hand_id]

        # Remove lost hands
        lost_hands = []
        for hand_id in list(self.hands.keys()):
            if hand_id not in matched_hands:
                self.hands[hand_id]["frames_since_update"] += 1
                timeout = HAND_TIMEOUT_FRAMES
                if self.hands[hand_id]["state"] in [
                    "tracking_to_pizza",
                    "waiting_at_pizza",
                ]:
                    timeout = HAND_TIMEOUT_FRAMES * 2
                if self.hands[hand_id]["frames_since_update"] > timeout:
                    lost_hands.append(hand_id)
                    if hand_id in self.pending_violations:
                        del self.pending_violations[hand_id]

        for hand_id in lost_hands:
            del self.hands[hand_id]

        # رجع list المخالفات الجديدة (ممكن يكون فيها أكتر من واحدة في نفس الـ frame)
        return new_violations

    def get_violation_count(self):
        return len(self.violations)


# ---------------- Detection Service ----------------
class ViolationDetector:
    def __init__(self):
        # RabbitMQ config
        self.rabbitmq_host = os.getenv("RABBITMQ_HOST", "localhost")
        self.rabbitmq_port = int(os.getenv("RABBITMQ_PORT", 5672))
        self.rabbitmq_user = os.getenv("RABBITMQ_USER", "admin")
        self.rabbitmq_pass = os.getenv("RABBITMQ_PASS", "admin123")

        # PostgreSQL config
        self.pg_host = os.getenv("POSTGRES_HOST", "localhost")
        self.pg_port = int(os.getenv("POSTGRES_PORT", 5432))
        self.pg_user = os.getenv("POSTGRES_USER", "pizza_user")
        self.pg_pass = os.getenv("POSTGRES_PASSWORD", "pizza_pass")
        self.pg_db = os.getenv("POSTGRES_DB", "pizza_violations")

        # Model path
        self.model_path = "/app/models/best.pt"

        # Connections
        self.rabbitmq_connection = None
        self.rabbitmq_channel = None
        self.pg_connection = None
        self.model = None

        # ROIs و Tracker
        self.rois = self.load_rois()
        self.tracker = HandTracker(self.rois)

    # ---------------- ROI Loading ----------------
    def load_rois(self):
        """
        يحمل الـ ROIs - نحاول كل الـ paths الممكنة
        الـ docker-compose يـ mount الـ shared/ فلوجيكاليي الـ config.json ممكن يكون فيها
        """
        possible_paths = [
            "/app/shared/config.json",  # لو كان في shared/
            "/app/config.json",  # لو كان في الـ root
            "/app/config/config.json",  # fallback قديم
        ]

        for path in possible_paths:
            if os.path.exists(path):
                with open(path, "r") as f:
                    config = json.load(f)
                logger.info(f"Loaded {len(config['rois'])} ROIs from: {path}")
                return config["rois"]

        # لو مفيش config خليه يـ warn ومتيجيش يـ crash
        logger.warning("config.json not found in any expected path, using default ROI")
        return [{"id": 1, "name": "Cheese", "x": 454, "y": 350, "w": 64, "h": 37}]

    # ---------------- RabbitMQ Connection ----------------
    def connect_rabbitmq(self):
        """يتصل بـ RabbitMQ مع retry logic"""
        max_retries = 15
        for attempt in range(max_retries):
            try:
                credentials = pika.PlainCredentials(
                    self.rabbitmq_user, self.rabbitmq_pass
                )
                parameters = pika.ConnectionParameters(
                    host=self.rabbitmq_host,
                    port=self.rabbitmq_port,
                    credentials=credentials,
                    heartbeat=600,
                    blocked_connection_timeout=300,
                )
                self.rabbitmq_connection = pika.BlockingConnection(parameters)
                self.rabbitmq_channel = self.rabbitmq_connection.channel()
                self.rabbitmq_channel.queue_declare(queue="frame_queue", durable=True)
                self.rabbitmq_channel.queue_declare(queue="results_queue", durable=True)
                logger.info("Connected to RabbitMQ")
                return True
            except Exception as e:
                logger.error(
                    f"RabbitMQ connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    time.sleep(5)
        logger.error("RabbitMQ: all retry attempts failed")
        return False

    def ensure_rabbitmq(self):
        """يتأكد إن الـ connection شغال، لو مش شغال يـ reconnect"""
        if self.rabbitmq_connection and self.rabbitmq_connection.is_open:
            return True
        logger.warning("RabbitMQ connection lost, reconnecting...")
        return self.connect_rabbitmq()

    # ---------------- PostgreSQL Connection ----------------
    def connect_postgres(self):
        """يتصل بـ PostgreSQL مع retry logic"""
        max_retries = 15
        for attempt in range(max_retries):
            try:
                self.pg_connection = psycopg2.connect(
                    host=self.pg_host,
                    port=self.pg_port,
                    user=self.pg_user,
                    password=self.pg_pass,
                    database=self.pg_db,
                )
                self.pg_connection.autocommit = False
                self.create_tables()
                logger.info("Connected to PostgreSQL")
                return True
            except Exception as e:
                logger.error(
                    f"PostgreSQL connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    time.sleep(5)
        logger.error("PostgreSQL: all retry attempts failed")
        return False

    def ensure_postgres(self):
        """يتأكد إن الـ DB connection شغال"""
        try:
            # بيـ ping الـ connection عشان يتأكد شغال
            self.pg_connection.cursor().execute("SELECT 1")
            return True
        except Exception:
            logger.warning("PostgreSQL connection lost, reconnecting...")
            try:
                if self.pg_connection:
                    self.pg_connection.close()
            except Exception:
                pass
            return self.connect_postgres()

    def create_tables(self):
        with self.pg_connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS violations (
                    id SERIAL PRIMARY KEY,
                    frame_number INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    violation_type VARCHAR(100) NOT NULL,
                    frame_path TEXT,
                    confidence FLOAT NOT NULL,
                    frame_data TEXT,
                    metadata JSONB,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            self.pg_connection.commit()

    # ---------------- Model Loading ----------------
    def load_model(self):
        """يحمل الـ YOLO model - يستخدم GPU لو متاحة"""
        try:
            if not os.path.exists(self.model_path):
                logger.error(f"Model not found: {self.model_path}")
                return False

            self.model = YOLO(self.model_path)

            # استخدم GPU لو متاحة، وإلا CPU
            if torch.cuda.is_available():
                self.model.to("cuda")
                logger.info(f"Model loaded on GPU: {torch.cuda.get_device_name(0)}")
            else:
                self.model.to("cpu")
                logger.info("Model loaded on CPU")

            return True
        except Exception as e:
            logger.error(f"Model load failed: {e}")
            return False

    # ---------------- Frame Drawing ----------------
    def draw_frame(
        self,
        frame,
        hand_boxes,
        scooper_boxes,
        pizza_boxes,
        person_boxes,
        has_violation=False,
    ):
        """يـ draw كل الـ detections على الـ frame - بدون emojis عشان cv2 مش يـ render ها"""
        # ROIs
        for roi in self.rois:
            x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
            cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 255, 0), 3)
            cv2.putText(
                frame,
                roi["name"],
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 0),
                2,
            )

        # Hands
        for box in hand_boxes:
            x1, y1, x2, y2 = map(int, box)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                frame,
                "Hand",
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                2,
            )

        # Scoopers
        for box in scooper_boxes:
            x1, y1, x2, y2 = map(int, box)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
            cv2.putText(
                frame,
                "Scooper",
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 0, 255),
                2,
            )

        # Pizzas
        for box in pizza_boxes:
            x1, y1, x2, y2 = map(int, box)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 165, 255), 2)
            cv2.putText(
                frame,
                "Pizza",
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 165, 255),
                2,
            )

        # Persons
        for box in person_boxes:
            x1, y1, x2, y2 = map(int, box)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 1)
            cv2.putText(
                frame,
                "Person",
                (x1, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (0, 255, 0),
                1,
            )

        # Violation count overlay
        v_count = self.tracker.get_violation_count()
        cv2.rectangle(frame, (10, 10), (450, 100), (0, 0, 0), -1)
        cv2.putText(
            frame,
            f"Violations: {v_count}",
            (20, 45),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (0, 0, 255),
            3,
        )

        # Violation alert - نكتب نص عادي بدل الـ emoji
        if has_violation:
            cv2.rectangle(frame, (10, 105), (450, 150), (0, 0, 180), -1)
            cv2.putText(
                frame,
                "!! VIOLATION DETECTED !!",
                (20, 138),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 0, 255),
                3,
            )

        return frame

    # ---------------- Frame Processing ----------------
    def process_frame(self, ch, method, properties, body):
        """يـ process كل frame قادم من RabbitMQ"""
        frame = None
        try:
            message = json.loads(body)
            frame_number = message["frame_number"]
            timestamp = message["timestamp"]
            frame_base64 = message["frame_data"]

            # Decode frame
            frame_bytes = base64.b64decode(frame_base64)
            frame_array = np.frombuffer(frame_bytes, dtype=np.uint8)
            frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)

            # حذف الـ intermediate arrays فوراً عشان نوفر RAM
            del frame_bytes, frame_array

            if frame is None:
                logger.error(f"Failed to decode frame {frame_number}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            # Run YOLO
            results = self.model(frame, conf=CONF_THRESHOLD, verbose=False)[0]

            # Parse detections
            hand_boxes = []
            scooper_boxes = []
            pizza_boxes = []
            person_boxes = []

            for box in results.boxes:
                cls = int(box.cls[0])
                label = self.model.names[cls].lower()
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                bbox = (x1, y1, x2, y2)

                if label == "hand":
                    hand_boxes.append(bbox)
                elif label == "scooper":
                    scooper_boxes.append(bbox)
                elif label == "pizza":
                    pizza_boxes.append(bbox)
                elif label == "person":
                    person_boxes.append(bbox)

            # حذف نتائج YOLO الخام فوراً
            del results

            # Run HandTracker
            new_violations = self.tracker.update(
                hand_boxes, scooper_boxes, pizza_boxes, frame_number
            )

            has_violation = len(new_violations) > 0

            # Draw على نفس الـ frame بدل ما نـ copy (توفير RAM)
            self.draw_frame(
                frame,
                hand_boxes,
                scooper_boxes,
                pizza_boxes,
                person_boxes,
                has_violation,
            )

            # حفظ كل المخالفات الجديدة (مش بس الأخيرة)
            if has_violation:
                self.ensure_postgres()
                for violation in new_violations:
                    self.save_violation(frame, frame_number, timestamp, violation)

            # Encode الـ frame المـ annotated
            _, buffer = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
            )
            annotated_base64 = base64.b64encode(buffer).decode("utf-8")
            del buffer  # حذف فوراً

            # Publish results
            result_message = {
                "frame_number": frame_number,
                "timestamp": timestamp,
                "frame_data": annotated_base64,
                "violation_detected": has_violation,
                "violation_count": self.tracker.get_violation_count(),
            }
            del annotated_base64  # حذف فوراً بعد ما دخل في الـ message

            # تأكد إن RabbitMQ connection شغال قبل الـ publish
            self.ensure_rabbitmq()
            self.rabbitmq_channel.basic_publish(
                exchange="",
                routing_key="results_queue",
                body=json.dumps(result_message),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            del result_message

            ch.basic_ack(delivery_tag=method.delivery_tag)

            if frame_number % 30 == 0:
                logger.info(
                    f"Frame {frame_number} | Violations: {self.tracker.get_violation_count()}"
                )

        except Exception as e:
            logger.error(f"Error processing frame: {e}")
            # حتى لو في خطأ نـ ack الـ message عشان مش يـ loop forever
            try:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception:
                pass
        finally:
            # حذف الـ frame في كل الأحوال
            del frame
            # Garbage collect فوراً لو الـ memory تقيل
            gc.collect()

    # ---------------- Violation Saving ----------------
    def save_violation(self, frame, frame_number, timestamp, violation_data):
        """يحفظ صورة المخالفة في الـ disk والـ record في الـ DB"""
        try:
            output_dir = "/app/violations"
            os.makedirs(output_dir, exist_ok=True)

            # حفظ الـ frame
            frame_filename = f"violation_{frame_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            frame_path = os.path.join(output_dir, frame_filename)
            cv2.imwrite(frame_path, frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

            # حفظ في DB
            with self.pg_connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO violations 
                    (frame_number, timestamp, violation_type, frame_path, confidence, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        frame_number,
                        timestamp,
                        "no_scooper",
                        frame_path,
                        1.0,
                        json.dumps(violation_data),
                    ),
                )
                self.pg_connection.commit()

            logger.info(
                f"Violation saved: {frame_path} | ROI: {violation_data['roi_name']}"
            )

        except Exception as e:
            logger.error(f"Save violation error: {e}")
            # لو في خطأ في الـ DB حاول rollback
            try:
                if self.pg_connection:
                    self.pg_connection.rollback()
            except Exception:
                pass

    # ---------------- Main Start ----------------
    def start_consuming(self):
        """يـ start الخدمة"""
        logger.info("=" * 50)
        logger.info("Pizza Violation Detection Service")
        logger.info("=" * 50)

        # Connect كل الخدمات
        if not self.connect_rabbitmq():
            logger.error("Cannot start: RabbitMQ connection failed")
            return
        if not self.connect_postgres():
            logger.error("Cannot start: PostgreSQL connection failed")
            return
        if not self.load_model():
            logger.error("Cannot start: Model load failed")
            return

        logger.info(f"ROIs loaded: {[r['name'] for r in self.rois]}")
        logger.info(f"Violation delay: {VIOLATION_DELAY_FRAMES} frames")
        logger.info("Waiting for frames...")
        logger.info("=" * 50)

        # prefetch_count=1 عشان الـ detection ثقيل ومحتاج يـ finish قبل ما يـ start تاني
        self.rabbitmq_channel.basic_qos(prefetch_count=1)
        self.rabbitmq_channel.basic_consume(
            queue="frame_queue", on_message_callback=self.process_frame
        )

        self.rabbitmq_channel.start_consuming()


# ---------------- Entry Point ----------------
if __name__ == "__main__":
    detector = ViolationDetector()
    detector.start_consuming()
