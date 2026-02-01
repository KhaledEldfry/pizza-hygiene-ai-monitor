import React, { useState, useEffect, useRef } from 'react';
import './App.css';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

function App() {
  const [violationCount, setViolationCount] = useState(0);
  const [violations, setViolations] = useState([]);
  const [connected, setConnected] = useState(false);
  const [stats, setStats] = useState(null);
  const [streamMode, setStreamMode] = useState('websocket'); // 'websocket' or 'mjpeg'
  
  const wsRef = useRef(null);
  const canvasRef = useRef(null);
  const imgRef = useRef(null);

  // WebSocket connection
  useEffect(() => {
    if (streamMode !== 'websocket') return;

    const ws = new WebSocket(`ws://localhost:8000/ws/stream`);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('✅ WebSocket connected');
      setConnected(true);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.type === 'frame') {
        // Update violation count
        setViolationCount(data.violation_count);
        
        // Draw frame on canvas
        const canvas = canvasRef.current;
        if (canvas) {
          const ctx = canvas.getContext('2d');
          const img = new Image();
          img.onload = () => {
            canvas.width = img.width;
            canvas.height = img.height;
            ctx.drawImage(img, 0, 0);
            
            // Add violation indicator
            if (data.violation_detected) {
              ctx.fillStyle = 'rgba(255, 0, 0, 0.3)';
              ctx.fillRect(0, 0, canvas.width, canvas.height);
              ctx.font = '48px Arial';
              ctx.fillStyle = 'red';
              ctx.fillText('VIOLATION!', 50, 100);
            }
          };
          img.src = `data:image/jpeg;base64,${data.frame_data}`;
        }
      }
    };

    ws.onclose = () => {
      console.log('❌ WebSocket disconnected');
      setConnected(false);
    };

    ws.onerror = (error) => {
      console.error('❌ WebSocket error:', error);
      setConnected(false);
    };

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, [streamMode]);

  // Fetch violations periodically
  useEffect(() => {
    const fetchViolations = async () => {
      try {
        const response = await fetch(`${API_URL}/api/violations`);
        const data = await response.json();
        setViolations(data.violations || []);
      } catch (error) {
        console.error('Error fetching violations:', error);
      }
    };

    fetchViolations();
    const interval = setInterval(fetchViolations, 5000);

    return () => clearInterval(interval);
  }, []);

  // Fetch stats periodically
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const response = await fetch(`${API_URL}/api/stats`);
        const data = await response.json();
        setStats(data);
      } catch (error) {
        console.error('Error fetching stats:', error);
      }
    };

    fetchStats();
    const interval = setInterval(fetchStats, 10000);

    return () => clearInterval(interval);
  }, []);

  return (
    <div className="App">
      <header className="App-header">
        <h1>🍕 Pizza Hygiene Monitoring System</h1>
        <div className="status-bar">
          <div className={`status-indicator ${connected ? 'connected' : 'disconnected'}`}>
            {connected ? '🟢 Connected' : '🔴 Disconnected'}
          </div>
          <div className="violation-counter">
            <span className="counter-label">Total Violations:</span>
            <span className="counter-value">{violationCount}</span>
          </div>
        </div>
      </header>

      <main className="main-content">
        <div className="video-section">
          <h2>📹 Live Video Stream</h2>
          <div className="stream-controls">
            <button 
              className={streamMode === 'websocket' ? 'active' : ''}
              onClick={() => setStreamMode('websocket')}
            >
              WebSocket
            </button>
            <button 
              className={streamMode === 'mjpeg' ? 'active' : ''}
              onClick={() => setStreamMode('mjpeg')}
            >
              MJPEG
            </button>
          </div>
          
          <div className="video-container">
            {streamMode === 'websocket' ? (
              <canvas ref={canvasRef} className="video-canvas"></canvas>
            ) : (
              <img 
                ref={imgRef}
                src={`${API_URL}/api/stream/mjpeg`}
                alt="Video stream"
                className="video-stream"
              />
            )}
          </div>
        </div>

        <div className="info-section">
          <div className="stats-card">
            <h3>📊 Statistics</h3>
            {stats ? (
              <div className="stats-content">
                <div className="stat-item">
                  <span className="stat-label">Total Violations:</span>
                  <span className="stat-value">{stats.total_violations}</span>
                </div>
                <div className="stat-item">
                  <span className="stat-label">Recent (Last Hour):</span>
                  <span className="stat-value">{stats.recent_violations}</span>
                </div>
                <div className="violations-by-type">
                  <h4>By Type:</h4>
                  {stats.violations_by_type.map((item, index) => (
                    <div key={index} className="type-item">
                      <span>{item.violation_type}:</span>
                      <span>{item.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p>Loading stats...</p>
            )}
          </div>

          <div className="violations-card">
            <h3>🚨 Recent Violations</h3>
            <div className="violations-list">
              {violations.length > 0 ? (
                violations.slice(0, 10).map((violation) => (
                  <div key={violation.id} className="violation-item">
                    <div className="violation-header">
                      <span className="violation-type">{violation.violation_type}</span>
                      <span className="violation-frame">Frame #{violation.frame_number}</span>
                    </div>
                    <div className="violation-details">
                      <span className="violation-time">
                        {new Date(violation.created_at).toLocaleString()}
                      </span>
                      <span className="violation-confidence">
                        Confidence: {(violation.confidence * 100).toFixed(1)}%
                      </span>
                    </div>
                  </div>
                ))
              ) : (
                <p className="no-violations">No violations detected yet</p>
              )}
            </div>
          </div>
        </div>
      </main>

      <footer className="App-footer">
        <p>Pizza Violation Detection System - Microservices Architecture</p>
      </footer>
    </div>
  );
}

export default App;
