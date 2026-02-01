# 🍕 Pizza Hygiene Monitoring System - Microservices Architecture

## 📋 Overview

A real-time computer vision system to monitor hygiene protocol compliance in pizza stores. The system detects whether workers use a scooper when handling ingredients from designated ROIs (Regions of Interest) and flags violations.
![Uploading Screenshot 2026-02-01 211611.png…]()

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────┐
│                Pizza Hygiene System                       │
├──────────────────────────────────────────────────────────┤
│                                                           │
│  📹 Frame Reader ──→ RabbitMQ ──→ 🔍 Detection Service  │
│                                           ↓               │
│                                      PostgreSQL           │
│                                           ↓               │
│  💻 Frontend ←────── 🌐 Streaming Service (FastAPI)     │
│                                                           │
└──────────────────────────────────────────────────────────┘
```

### Microservices:

1. **Frame Reader Service** - Reads video frames and publishes to message broker
2. **RabbitMQ** - Message broker for inter-service communication
3. **Detection Service** - Performs object detection and violation detection logic
4. **PostgreSQL** - Stores violation records
5. **Streaming Service** - REST API + WebSocket for frontend communication
6. **Frontend** - React-based UI for real-time visualization

## 🎯 Features

- ✅ Real-time video processing
- ✅ YOLO-based object detection (Hand, Person, Pizza, Scooper)
- ✅ ROI-based violation detection
- ✅ Microservices architecture (scalable & maintainable)
- ✅ WebSocket & MJPEG streaming
- ✅ RESTful API for violation records
- ✅ Docker & Docker Compose deployment
- ✅ PostgreSQL database for persistent storage
- ✅ Real-time violation counting and alerts

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- At least 8GB RAM
- NVIDIA GPU (optional, for faster inference)

### Installation

1. **Clone the repository:**
```bash
git clone <repository-url>
cd pizza_violation_system
```

2. **Create required directories:**
```bash
mkdir -p videos models violations shared
```

3. **Download the pretrained model:**
   - Download `best.pt` from: https://drive.google.com/drive/folders/1S_WeBU-o3QRRAbn9HCFHSt-3uuPtsQ8K
   - Place it in: `./models/best.pt`

4. **Add test videos:**
   - Download sample videos from the provided Google Drive links
   - Place them in: `./videos/`

5. **Update docker-compose.yml:**
   - Set the correct video path in the `frame_reader` service:
   ```yaml
   environment:
     VIDEO_PATH: /app/videos/your_video.mp4
   ```

### Running the System

**Start all services:**
```bash
docker-compose up --build
```

**Services will be available at:**
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- RabbitMQ Management: http://localhost:15672 (admin/admin123)
- PostgreSQL: localhost:5432

**Stop all services:**
```bash
docker-compose down
```

**Stop and remove all data:**
```bash
docker-compose down -v
```

## 📦 Project Structure

```
pizza_violation_system/
├── docker-compose.yml          # Main orchestration file
├── README.md                   # This file
│
├── frame_reader/               # Service 1: Frame Reader
│   ├── Dockerfile
│   ├── requirements.txt
│   └── frame_reader.py
│
├── detection_service/          # Service 2: Detection Service
│   ├── Dockerfile
│   ├── requirements.txt
│   └── detector.py
│
├── streaming_service/          # Service 3: Streaming API
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── frontend/                   # Service 4: React Frontend
│   ├── Dockerfile
│   ├── package.json
│   ├── public/
│   │   └── index.html
│   └── src/
│       ├── App.js
│       ├── App.css
│       ├── index.js
│       └── index.css
│
├── videos/                     # Video files (not in repo)
├── models/                     # YOLO models (not in repo)
├── violations/                 # Saved violation frames
└── shared/                     # Shared data between services
```

## 🔍 Violation Detection Logic

The system detects violations using the following logic:

1. **Define ROI:** User-defined regions of interest (e.g., protein container)
2. **Track Hands:** Track hand movements across frames
3. **Detect Scooper:** Check if hand is holding/near a scooper
4. **Check Pizza Interaction:** Detect when hand moves from ROI to pizza
5. **Flag Violation:** If hand went from ROI to pizza WITHOUT scooper → VIOLATION

### Violation Criteria:
- ✅ Hand enters ROI
- ✅ Hand picks up ingredient
- ❌ No scooper detected
- ✅ Hand places ingredient on pizza
- 🚨 **VIOLATION FLAGGED**

## 🛠️ API Endpoints

### Streaming Service (Port 8000)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/api/violations` | GET | Get all violations |
| `/api/violations/count` | GET | Get total violation count |
| `/api/violations/{id}` | GET | Get specific violation |
| `/api/stats` | GET | Get system statistics |
| `/ws/stream` | WebSocket | Real-time video stream |
| `/api/stream/mjpeg` | GET | MJPEG video stream |

### Example API Calls:

**Get violation count:**
```bash
curl http://localhost:8000/api/violations/count
```

**Get all violations:**
```bash
curl http://localhost:8000/api/violations
```

**Get statistics:**
```bash
curl http://localhost:8000/api/stats
```

## 🎨 Frontend Features

- **Live Video Stream:** Real-time display with detections
- **Violation Counter:** Shows total violations detected
- **Recent Violations List:** Displays recent violations with details
- **Statistics Dashboard:** Shows violation trends and stats
- **Stream Mode Toggle:** Switch between WebSocket and MJPEG
- **Connection Status:** Shows real-time connection status

## 🐳 Docker Services Configuration

### RabbitMQ
- **Image:** rabbitmq:3.12-management
- **Ports:** 5672 (AMQP), 15672 (Management UI)
- **Credentials:** admin/admin123

### PostgreSQL
- **Image:** postgres:15-alpine
- **Port:** 5432
- **Database:** pizza_violations
- **Credentials:** pizza_user/pizza_pass

### Frame Reader
- Reads video frames
- Publishes to `frame_queue`

### Detection Service
- Subscribes to `frame_queue`
- Runs YOLO inference
- Detects violations
- Saves to PostgreSQL
- Publishes to `results_queue`

### Streaming Service
- FastAPI backend
- WebSocket server
- REST API
- Subscribes to `results_queue`

### Frontend
- React application
- Real-time visualization
- WebSocket client

## 📊 Database Schema

### Violations Table
```sql
CREATE TABLE violations (
    id SERIAL PRIMARY KEY,
    frame_number INTEGER,
    timestamp TIMESTAMP,
    violation_type VARCHAR(100),
    frame_path VARCHAR(500),
    bounding_boxes JSONB,
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Video Sessions Table
```sql
CREATE TABLE video_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) UNIQUE,
    total_violations INTEGER DEFAULT 0,
    start_time TIMESTAMP DEFAULT NOW(),
    end_time TIMESTAMP
);
```

## 🧪 Testing

### Test Videos

Three sample videos are provided with known violation counts:

1. **Video 1:** 1 real violation
   - Link: https://drive.google.com/file/d/1P3e5HosfSGi1RwITGExwzBECVoA1cwKZ/view

2. **Video 2:** 2 real violations
   - Link: https://drive.google.com/file/d/16wxYIj8BpYhImBG8_0go5N_O_kagqDus/view

3. **Video 3:** 1 real violation
   - Link: https://drive.google.com/file/d/1Zd7wMMcX-Rqsg-4lt_08f6-0sZ-uQIUh/view

### Running Tests

1. Download test videos to `./videos/`
2. Update `VIDEO_PATH` in docker-compose.yml
3. Run the system
4. Verify violation count matches expected

## 🔧 Configuration

### Environment Variables

#### Frame Reader
```bash
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672
RABBITMQ_USER=admin
RABBITMQ_PASS=admin123
VIDEO_PATH=/app/videos/test_video.mp4
```

#### Detection Service
```bash
RABBITMQ_HOST=rabbitmq
POSTGRES_HOST=postgres
POSTGRES_DB=pizza_violations
```

#### Streaming Service
```bash
POSTGRES_HOST=postgres
POSTGRES_DB=pizza_violations
```

### Adjusting ROI

Edit `detection_service/detector.py` → `set_roi()` method:

```python
def set_roi(self, frame_shape):
    h, w = frame_shape[:2]
    self.roi = {
        'x1': int(w * 0.1),  # Adjust these values
        'y1': int(h * 0.3),
        'x2': int(w * 0.4),
        'y2': int(h * 0.7)
    }
```

## 🐛 Troubleshooting

### Services not starting
```bash
# Check service logs
docker-compose logs frame_reader
docker-compose logs detection_service
docker-compose logs streaming_service
```

### RabbitMQ connection issues
```bash
# Restart RabbitMQ
docker-compose restart rabbitmq

# Check RabbitMQ logs
docker-compose logs rabbitmq
```

### Database connection issues
```bash
# Restart PostgreSQL
docker-compose restart postgres

# Access database
docker exec -it postgres_db psql -U pizza_user -d pizza_violations
```

### Model not loading
- Ensure `best.pt` is in `./models/`
- Check file permissions
- Verify model path in docker-compose.yml

### No video stream
- Verify video file exists in `./videos/`
- Check video path in docker-compose.yml
- Check frame_reader logs

## 📈 Performance Optimization

### For better performance:

1. **Use GPU:**
   - Install nvidia-docker
   - Uncomment GPU sections in Dockerfiles
   - Add `runtime: nvidia` to detection_service in docker-compose.yml

2. **Adjust frame rate:**
   - In `frame_reader.py`, uncomment:
   ```python
   time.sleep(1.0 / fps)
   ```

3. **Batch processing:**
   - Increase `prefetch_count` in detection_service

4. **Database optimization:**
   - Add indexes on frequently queried columns

## 🎯 Future Enhancements

- [ ] Multi-camera support
- [ ] Real-time alerts (Email/SMS)
- [ ] Historical analytics dashboard
- [ ] Machine learning model retraining pipeline
- [ ] User authentication & authorization
- [ ] Cloud deployment (AWS/GCP/Azure)
- [ ] Mobile app
- [ ] Advanced reporting features

## 📝 Notes

- The system processes videos frame-by-frame
- Violation detection uses trajectory-based logic
- ROI can be customized per camera/location
- All violations are saved to database with timestamps
- System supports multiple workers simultaneously

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 📄 License

This project is for educational/commercial use.

## 👥 Authors

- Khaled Ahmed Eldefry

## 🙏 Acknowledgments

- YOLO team for the object detection framework
- Anthropic for assistance
- Pizza industry for hygiene standards

---

**For issues or questions, please open an issue on GitHub or contact the development team.**

## 🔗 Resources

- YOLO Documentation: https://docs.ultralytics.com/
- FastAPI Documentation: https://fastapi.tiangolo.com/
- React Documentation: https://react.dev/
- RabbitMQ Documentation: https://www.rabbitmq.com/documentation.html
- PostgreSQL Documentation: https://www.postgresql.org/docs/

---

**Last Updated:** January 2026
