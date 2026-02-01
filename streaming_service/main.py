from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
import asyncio
import pika
import base64
from datetime import datetime
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Pizza Violation Streaming Service")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# PostgreSQL config
PG_HOST = os.getenv('POSTGRES_HOST', 'localhost')
PG_PORT = int(os.getenv('POSTGRES_PORT', 5432))
PG_USER = os.getenv('POSTGRES_USER', 'pizza_user')
PG_PASS = os.getenv('POSTGRES_PASSWORD', 'pizza_pass')
PG_DB = os.getenv('POSTGRES_DB', 'pizza_violations')

# RabbitMQ config
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', 5672))
RABBITMQ_USER = os.getenv('RABBITMQ_USER', 'admin')
RABBITMQ_PASS = os.getenv('RABBITMQ_PASS', 'admin123')


def get_db_connection():
    """Get PostgreSQL connection"""
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASS,
        database=PG_DB
    )


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "service": "Pizza Violation Streaming Service",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/violations")
async def get_violations():
    """Get all violations"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            SELECT id, frame_number, timestamp, violation_type, 
                   frame_path, confidence, created_at
            FROM violations
            ORDER BY created_at DESC
        """)
        
        violations = cursor.fetchall()
        
        # Convert datetime objects to strings
        for v in violations:
            if v['timestamp']:
                v['timestamp'] = v['timestamp'].isoformat()
            if v['created_at']:
                v['created_at'] = v['created_at'].isoformat()
        
        cursor.close()
        conn.close()
        
        return {
            "total": len(violations),
            "violations": violations
        }
        
    except Exception as e:
        logger.error(f"❌ Error fetching violations: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.get("/api/violations/count")
async def get_violation_count():
    """Get total violation count"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM violations")
        count = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        return {
            "count": count,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error fetching count: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.get("/api/violations/{violation_id}")
async def get_violation(violation_id: int):
    """Get specific violation details"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            SELECT * FROM violations WHERE id = %s
        """, (violation_id,))
        
        violation = cursor.fetchone()
        
        if violation:
            if violation['timestamp']:
                violation['timestamp'] = violation['timestamp'].isoformat()
            if violation['created_at']:
                violation['created_at'] = violation['created_at'].isoformat()
        
        cursor.close()
        conn.close()
        
        if not violation:
            return JSONResponse(
                status_code=404,
                content={"error": "Violation not found"}
            )
        
        return violation
        
    except Exception as e:
        logger.error(f"❌ Error fetching violation: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """WebSocket endpoint for real-time video streaming"""
    await websocket.accept()
    logger.info("🔌 WebSocket client connected")
    
    try:
        # Connect to RabbitMQ
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
        parameters = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            port=RABBITMQ_PORT,
            credentials=credentials
        )
        
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        channel.queue_declare(queue='results_queue', durable=True)
        
        # Consume messages
        for method, properties, body in channel.consume('results_queue', inactivity_timeout=1):
            if method is None:
                # Check if websocket is still connected
                try:
                    await websocket.send_json({"type": "ping"})
                except:
                    break
                continue
            
            try:
                message = json.loads(body)
                
                # Send frame to client
                await websocket.send_json({
                    "type": "frame",
                    "frame_number": message['frame_number'],
                    "timestamp": message['timestamp'],
                    "frame_data": message['frame_data'],
                    "violation_detected": message.get('violation_detected', False),
                    "violation_count": message.get('violation_count', 0)
                })
                
                # Acknowledge message
                channel.basic_ack(delivery_tag=method.delivery_tag)
                
            except Exception as e:
                logger.error(f"❌ Error processing message: {e}")
                channel.basic_ack(delivery_tag=method.delivery_tag)
        
        connection.close()
        
    except WebSocketDisconnect:
        logger.info("🔌 WebSocket client disconnected")
    except Exception as e:
        logger.error(f"❌ WebSocket error: {e}")
    finally:
        try:
            await websocket.close()
        except:
            pass


@app.get("/api/stream/mjpeg")
async def mjpeg_stream():
    """MJPEG stream endpoint (alternative to WebSocket)"""
    
    def generate_frames():
        try:
            # Connect to RabbitMQ
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            parameters = pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials
            )
            
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.queue_declare(queue='results_queue', durable=True)
            
            # Consume messages
            for method, properties, body in channel.consume('results_queue'):
                if method is None:
                    continue
                
                try:
                    message = json.loads(body)
                    frame_base64 = message['frame_data']
                    frame_bytes = base64.b64decode(frame_base64)
                    
                    # Yield frame in MJPEG format
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                    
                    channel.basic_ack(delivery_tag=method.delivery_tag)
                    
                except Exception as e:
                    logger.error(f"❌ Error in MJPEG stream: {e}")
                    channel.basic_ack(delivery_tag=method.delivery_tag)
            
            connection.close()
            
        except Exception as e:
            logger.error(f"❌ MJPEG stream error: {e}")
    
    return StreamingResponse(
        generate_frames(),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )


@app.get("/api/stats")
async def get_stats():
    """Get system statistics"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Total violations
        cursor.execute("SELECT COUNT(*) as total FROM violations")
        total = cursor.fetchone()['total']
        
        # Violations by type
        cursor.execute("""
            SELECT violation_type, COUNT(*) as count
            FROM violations
            GROUP BY violation_type
        """)
        by_type = cursor.fetchall()
        
        # Recent violations (last hour)
        cursor.execute("""
            SELECT COUNT(*) as recent
            FROM violations
            WHERE created_at >= NOW() - INTERVAL '1 hour'
        """)
        recent = cursor.fetchone()['recent']
        
        cursor.close()
        conn.close()
        
        return {
            "total_violations": total,
            "violations_by_type": by_type,
            "recent_violations": recent,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error fetching stats: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
