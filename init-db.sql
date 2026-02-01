-- Create violations table with all necessary columns
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
);

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_violations_timestamp ON violations(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_violations_type ON violations(violation_type);
CREATE INDEX IF NOT EXISTS idx_violations_created ON violations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_violations_frame ON violations(frame_number);

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO pizza_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO pizza_user;

-- Insert a test record to verify the table works
INSERT INTO violations (frame_number, violation_type, confidence, frame_path, metadata) 
VALUES (0, 'test_initialization', 1.0, '/test/path', '{"status": "initialized", "test": true}')
ON CONFLICT DO NOTHING;

-- Verify the table structure
SELECT 
    'Database initialized successfully!' as status,
    count(*) as test_records
FROM violations;

-- Display column information for verification
SELECT 
    column_name, 
    data_type, 
    is_nullable
FROM information_schema.columns 
WHERE table_name = 'violations'
ORDER BY ordinal_position;