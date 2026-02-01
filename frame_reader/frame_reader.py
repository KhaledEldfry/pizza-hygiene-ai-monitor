import cv2
import pika
import json
import time
import os
import base64
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FrameReader:
    def __init__(self):
        self.rabbitmq_host = os.getenv('RABBITMQ_HOST', 'localhost')
        self.rabbitmq_port = int(os.getenv('RABBITMQ_PORT', 5672))
        self.rabbitmq_user = os.getenv('RABBITMQ_USER', 'admin')
        self.rabbitmq_pass = os.getenv('RABBITMQ_PASS', 'admin123')
        self.video_path = os.getenv('VIDEO_PATH', '/app/videos/test_video.mp4')
        
        self.connection = None
        self.channel = None
        self.frame_count = 0
        
    def connect_rabbitmq(self):
        """Connect to RabbitMQ with retry logic"""
        max_retries = 10
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                credentials = pika.PlainCredentials(self.rabbitmq_user, self.rabbitmq_pass)
                parameters = pika.ConnectionParameters(
                    host=self.rabbitmq_host,
                    port=self.rabbitmq_port,
                    credentials=credentials,
                    heartbeat=600,
                    blocked_connection_timeout=300
                )
                
                self.connection = pika.BlockingConnection(parameters)
                self.channel = self.connection.channel()
                
                # Declare queue
                self.channel.queue_declare(queue='frame_queue', durable=True)
                
                logger.info("✅ Connected to RabbitMQ successfully!")
                return True
                
            except Exception as e:
                logger.error(f"❌ Failed to connect to RabbitMQ (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    
        return False
    
    def publish_frame(self, frame, frame_number, timestamp):
        """Publish frame to RabbitMQ"""
        try:
            # Encode frame to base64
            _, buffer = cv2.imencode('.jpg', frame)
            frame_base64 = base64.b64encode(buffer).decode('utf-8')
            
            # Prepare message
            message = {
                'frame_number': frame_number,
                'timestamp': timestamp,
                'frame_data': frame_base64,
                'shape': frame.shape
            }
            
            # Publish to queue
            self.channel.basic_publish(
                exchange='',
                routing_key='frame_queue',
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Make message persistent
                )
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error publishing frame: {e}")
            return False
    
    def read_and_stream(self):
        """Read video and stream frames"""
        
        if not self.connect_rabbitmq():
            logger.error("❌ Could not connect to RabbitMQ. Exiting...")
            return
        
        # Check if video exists
        if not os.path.exists(self.video_path):
            logger.error(f"❌ Video file not found: {self.video_path}")
            return
        
        logger.info(f"📹 Opening video: {self.video_path}")
        cap = cv2.VideoCapture(self.video_path)
        
        if not cap.isOpened():
            logger.error("❌ Failed to open video file")
            return
        
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        logger.info(f"📊 Video Info: {total_frames} frames @ {fps} FPS")
        logger.info(f"🚀 Starting to stream frames...")
        
        frame_number = 0
        start_time = time.time()
        
        try:
            while True:
                ret, frame = cap.read()
                
                if not ret:
                    logger.info("✅ Finished reading video")
                    break
                
                # Create timestamp
                timestamp = datetime.now().isoformat()
                
                # Publish frame
                if self.publish_frame(frame, frame_number, timestamp):
                    frame_number += 1
                    
                    if frame_number % 30 == 0:  # Log every 30 frames
                        elapsed = time.time() - start_time
                        fps_actual = frame_number / elapsed if elapsed > 0 else 0
                        logger.info(f"📤 Published {frame_number}/{total_frames} frames (Speed: {fps_actual:.1f} FPS)")
                
                # Control frame rate (optional - remove to go full speed)
                # time.sleep(1.0 / fps)
                
        except KeyboardInterrupt:
            logger.info("⚠️ Interrupted by user")
            
        finally:
            cap.release()
            if self.connection:
                self.connection.close()
            logger.info(f"✅ Total frames published: {frame_number}")


if __name__ == "__main__":
    reader = FrameReader()
    reader.read_and_stream()
