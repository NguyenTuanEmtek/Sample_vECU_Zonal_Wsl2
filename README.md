SDV System Documentation: FMU → CAN → Zonal Controller
📋 Tổng quan Hệ thống
Hệ thống này mô phỏng một phần của Software-Defined Vehicle (SDV) với kiến trúc phân tán:

text
Windows (vECU với FMU) → WSL2 (CAN Bus) → Zonal Controller (Xử lý + Database)
🎯 Mục tiêu Hệ thống
Mô phỏng vECU đọc FMU (autoLamp.fmu) trên Windows

Gửi tín hiệu CAN qua vCAN ảo trong WSL2

Xử lý CAN frame tại Zonal Controller

Mapping VSS và lưu trữ dữ liệu

🏗️ Kiến trúc Hệ thống
Sơ đồ Luồng Dữ liệu
text
┌─────────────────────────────────────────────────────────────┐
│                    WINDOWS (HOST OS)                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  FMU Simulator (fmu_can_bridge.py)                   │  │
│  │  • Load autoLamp.fmu (win64)                         │  │
│  │  • Simulate ambient light → headLamp logic           │  │
│  │  • Encode to CAN frame                               │  │
│  │  • Send via TCP socket to WSL2                       │  │
│  └──────────────────────┬───────────────────────────────┘  │
│                         │ TCP Socket (localhost:8888)      │
└─────────────────────────┼───────────────────────────────────┘
                          ▼
┌─────────────────────────┼───────────────────────────────────┐
│                    WSL2 (GUEST OS - Ubuntu)                 │
│  ┌──────────────────────▼───────────────────────────────┐  │
│  │  Socket Server (wsl2_socket_server.py)               │  │
│  │  • Receive CAN frame from Windows                    │  │
│  │  • Forward to vCAN interface (vcan0)                 │  │
│  │  • Publish via ZMQ to Zonal Controller               │  │
│  └──────────────────────┬───────────────────────────────┘  │
│                         │ ZMQ Pub/Sub (port 5555)          │
│  ┌──────────────────────▼───────────────────────────────┐  │
│  │  Zonal Controller (zonal_controller.py)              │  │
│  │  • Subscribe to ZMQ messages                         │  │
│  │  • Decode CAN using DBC file                         │  │
│  │  • Map to VSS signals                                │  │
│  │  • Store to SQLite database                          │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
Các Thành phần Chính
Thành phần	Vị trí	Chức năng	Công nghệ
FMU Simulator	Windows	Đọc FMU, mô phỏng đèn	FMPy, Python
CAN Bridge	Windows	Encode CAN frame, gửi socket	Socket TCP
Socket Server	WSL2	Nhận CAN frame, forward vCAN/ZMQ	Python, ZMQ
Zonal Controller	WSL2	Decode DBC, map VSS, lưu DB	Cantools, SQLite
vCAN Interface	WSL2 Kernel	Virtual CAN bus	Linux SocketCAN
⚙️ Cài đặt và Cấu hình
1. Yêu cầu Hệ thống
Windows:
Windows 10/11 với WSL2 enabled

Python 3.8+

Visual Studio Build Tools (cho FMPy)

WSL2 (Ubuntu):
Ubuntu 20.04/22.04

Python 3.8+

Linux kernel > 4.8 (hỗ trợ SocketCAN)

2. Cài đặt Dependencies
Trên WSL2 (Ubuntu):
bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install system dependencies
sudo apt install -y can-utils iproute2 net-tools

# Install Python packages
pip install python-can cantools pyzmq pyyaml
Trên Windows:
powershell
# Install Python packages
pip install fmpy numpy

# Install can-utils for Windows (optional)
# Download from https://github.com/linux-can/can-utils
3. Cấu trúc Thư mục Dự án
text
sdv_system/
├── windows/                      # Code chạy trên Windows
│   ├── fmu_can_bridge.py        # FMU simulator + CAN bridge
│   └── autoLamp.fmu             # FMU file (win64)
│
├── wsl2/                         # Code chạy trên WSL2
│   ├── wsl2_socket_server.py    # Socket server + vCAN forwarder
│   ├── zonal_controller.py      # Zonal controller
│   ├── lights.dbc               # DBC file for CAN decoding
│   ├── config/
│   │   └── vss_mapping.yaml     # CAN → VSS mapping
│   ├── scripts/
│   │   └── start_system.sh      # Startup script
│   ├── logs/                    # Log directory
│   └── sdv_can_data.db          # SQLite database (auto-generated)
│
└── docs/
    └── README.md                # This documentation
🚀 Hướng dẫn Chạy Hệ thống
Phương án 1: Chạy từng thành phần thủ công
Bước 1: Khởi động vCAN trong WSL2
bash
# Terminal 1 - WSL2 (Ubuntu)
# Load kernel modules và tạo vcan0
sudo modprobe can can_raw vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0

# Kiểm tra vcan0
ip link show vcan0
Bước 2: Khởi động Socket Server
bash
# Terminal 2 - WSL2 (Ubuntu)
cd ~/sdv_system/wsl2
python3 wsl2_socket_server.py \
    --host 0.0.0.0 \
    --port 8888 \
    --zmq-port 5555 \
    --can-interface vcan0 \
    --verbose
Bước 3: Khởi động Zonal Controller
bash
# Terminal 3 - WSL2 (Ubuntu)
cd ~/sdv_system/wsl2
python3 zonal_controller.py \
    --dbc lights.dbc \
    --zmq-port 5555 \
    --can-interface vcan0 \
    --verbose
Bước 4: Chạy FMU Simulator trên Windows
powershell
# Terminal 4 - Windows PowerShell
cd C:\sdv_system\windows
python fmu_can_bridge.py `
    --fmu "autoLamp.fmu" `
    --duration 60 `
    --wsl-host localhost `
    --wsl-port 8888
Phương án 2: Chạy bằng Script
Script khởi động WSL2 (start_system.sh):
bash
#!/bin/bash
cd ~/sdv_system/wsl2

# Setup vCAN
sudo modprobe can can_raw vcan 2>/dev/null
sudo ip link add dev vcan0 type vcan 2>/dev/null
sudo ip link set up vcan0

# Start Socket Server
python3 wsl2_socket_server.py \
    --port 8888 \
    --zmq-port 5555 \
    --log-file logs/server.log &

sleep 2

# Start Zonal Controller
python3 zonal_controller.py \
    --zmq-port 5555 \
    --log-file logs/controller.log &

echo "System started! Logs in logs/ directory"
echo "Press Ctrl+C to stop"

wait
Chạy Script:
bash
chmod +x start_system.sh
./start_system.sh
🔍 Giải thích Chi tiết Các Thành phần
1. FMU CAN Bridge (fmu_can_bridge.py)
Chức năng:
Đọc FMU file (autoLamp.fmu) sử dụng FMPy

Mô phỏng logic đèn dựa trên ambient light

Encode dữ liệu thành CAN frame format

Gửi qua TCP socket đến WSL2

CAN Frame Encoding:
python
# CAN frame structure cho message 0x100 (Light Control)
Byte 0: [bit0: headLamp, bit1: tailLamp, bit2: brakeLamp, ...]
Byte 1: light_level (0-255)
Byte 2: vehicle_speed (0-255 km/h)
Byte 3-4: ambient light value (uint16)
Byte 5-7: Reserved
Data Flow:
text
FMU Simulation → Python Dict → CAN Encoding → JSON → TCP Socket → WSL2
2. WSL2 Socket Server (wsl2_socket_server.py)
Chức năng chính:
Socket Server: Nhận TCP connections từ Windows (port 8888)

vCAN Forwarder: Gửi CAN frame lên virtual CAN bus

ZMQ Publisher: Publish messages cho các subscribers

Message Validation: Kiểm tra và enrich CAN messages

Kiến trúc ZMQ PUB/SUB:
python
# Publisher (Socket Server)
publisher = zmq.Context().socket(zmq.PUB)
publisher.bind("tcp://*:5555")
publisher.send_json(can_data)

# Subscriber (Zonal Controller)
subscriber = zmq.Context().socket(zmq.SUB)
subscriber.connect("tcp://localhost:5555")
subscriber.subscribe("")
message = subscriber.recv_json()
3. Zonal Controller (zonal_controller.py)
Chức năng chính:
DBC Decoding: Giải mã CAN messages sử dụng DBC file

VSS Mapping: Map CAN signals sang Vehicle Signal Specification

Database Storage: Lưu trữ vào SQLite database

Real-time Monitoring: Hiển thị statistics và logs

DBC File (lights.dbc):
text
BO_ 256 LIGHT_CONTROL: 8 VCU
 SG_ headLamp : 0|1@1+ (1,0) [0|1] "" ZC
 SG_ tailLamp : 1|1@1+ (1,0) [0|1] "" ZC
 SG_ lightLevel : 8|8@1+ (1,0) [0|255] "%" ZC
VSS Mapping (config/vss_mapping.yaml):
yaml
0x100:
  headLamp:
    vss_path: "Vehicle.Body.Lights.IsHighBeamOn"
    data_type: "boolean"
  lightLevel:
    vss_path: "Vehicle.Body.Lights.AmbientLight"
    data_type: "uint8"
Database Schema:
sql
-- CAN messages table
CREATE TABLE can_messages (
    id INTEGER PRIMARY KEY,
    timestamp REAL,
    can_id INTEGER,
    can_id_hex TEXT,
    raw_data BLOB,
    dlc INTEGER
);

-- VSS signals table
CREATE TABLE vss_signals (
    id INTEGER PRIMARY KEY,
    timestamp REAL,
    vss_path TEXT,
    vss_value REAL,
    source_can_id INTEGER
);
📊 Giám sát và Debug
1. Giám sát CAN Traffic
bash
# Terminal WSL2 - Xem CAN messages real-time
candump vcan0 -dex

# Hoặc với filtering
candump vcan0,100:7FF    # Chỉ xem CAN ID 0x100
candump vcan0 -l         # Log to file
2. Kiểm tra Network Connections
bash
# Kiểm tra socket server đang lắng nghe
netstat -tlnp | grep 8888

# Kiểm tra ZMQ publisher
netstat -tlnp | grep 5555

# Test connection từ Windows
Test-NetConnection -ComputerName localhost -Port 8888
3. Truy vấn Database
bash
# Truy cập SQLite database
sqlite3 sdv_can_data.db

-- Xem schema
.tables
.schema

-- Query dữ liệu
SELECT * FROM can_messages LIMIT 10;
SELECT vss_path, vss_value FROM vss_signals ORDER BY timestamp DESC;
4. Log Files
bash
# Xem logs real-time
tail -f logs/server.log
tail -f logs/controller.log

# Search for errors
grep -i "error\|exception" logs/*.log
🐛 Xử lý Sự cố Thường gặp
1. vCAN Interface Issues
bash
# Kiểm tra kernel modules
lsmod | grep can

# Tạo lại vcan0
sudo ip link delete vcan0
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0

# Kiểm tra status
ip -details link show vcan0
2. Socket Connection Failed
powershell
# Windows - Test WSL2 connection
ping $(wsl hostname -I)

# Check Windows Firewall
Get-NetFirewallRule | Where {$_.DisplayName -like "*WSL*"}

# Temporary disable firewall (for testing)
New-NetFirewallRule -DisplayName "WSL2" -Direction Inbound -InterfaceAlias "vEthernet (WSL)" -Action Allow
3. ZMQ Connection Issues
bash
# Test ZMQ publisher
python3 -c "import zmq; ctx=zmq.Context(); s=ctx.socket(zmq.PUB); s.bind('tcp://*:5555'); print('ZMQ OK')"

# Test ZMQ subscriber
python3 -c "import zmq; ctx=zmq.Context(); s=ctx.socket(zmq.SUB); s.connect('tcp://localhost:5555'); s.subscribe(''); print('Subscriber OK')"
4. FMU Loading Errors
python
# Debug FMU loading
import fmpy
md = fmpy.read_model_description('autoLamp.fmu')
print(f"Platform: {md.platform}")
print(f"Variables: {[v.name for v in md.modelVariables]}")
🔧 Tùy chỉnh và Mở rộng
1. Thêm CAN Messages mới
Cập nhật DBC file với message definition mới

Thêm encoding logic trong fmu_can_bridge.py

Cập nhật VSS mapping trong config/vss_mapping.yaml

2. Thêm vECU mới
Tạo FMU simulator mới

Dùng CAN ID khác để tránh conflict

Kết nối đến cùng WSL2 socket server

3. Tích hợp với Kuksa VSS
python
# Thêm vào zonal_controller.py
from kuksa_client.grpc import VSSClient

async def send_to_kuksa(vss_signals):
    async with VSSClient(host="localhost", port=55555) as client:
        for signal in vss_signals:
            await client.set_current_values({
                signal.path: signal.value
            })
4. Performance Optimization
python
# Batch database inserts
def save_messages_batch(messages):
    with db.atomic():
        for msg in messages:
            CANMessage.create(**msg)

# Binary protocol thay vì JSON
socket.send(pickle.dumps(data))  # Faster serialization
📈 Monitoring Dashboard (Optional)
Real-time Web Dashboard
python
# Sử dụng Flask + WebSockets
from flask import Flask, render_template
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app)

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

# Broadcast CAN messages to web clients
def broadcast_can_message(can_msg):
    socketio.emit('can_message', can_msg.to_dict())
🔐 Security Considerations
1. Network Security
bash
# Restrict socket server to localhost only
# Trong wsl2_socket_server.py
server.bind(('127.0.0.1', 8888))  # Chỉ localhost

# Hoặc dùng Unix domain socket (chỉ local)
server.bind('unix:///tmp/can_socket')
2. Data Validation
python
# Validate CAN data
def validate_can_message(data):
    if not (0x000 <= data['can_id'] <= 0x7FF):
        raise ValueError("Invalid CAN ID")
    if len(data['can_data']) != 8:
        raise ValueError("Invalid data length")
    return True
3. Rate Limiting
python
# Prevent DoS attacks
from ratelimit import limits, sleep_and_retry

@sleep_and_retry
@limits(calls=100, period=1)  # 100 messages/second
def process_message(message):
    # processing logic
📚 Tài liệu Tham khảo
Công nghệ sử dụng:
FMPy: https://fmi-standard.org/tools/

SocketCAN: https://www.kernel.org/doc/html/latest/networking/can.html

python-can: https://python-can.readthedocs.io/

cantools: https://cantools.readthedocs.io/

ZMQ: https://zeromq.org/

Tiêu chuẩn:
VSS: Vehicle Signal Specification (https://covesa.github.io/vehicle_signal_specification/)

DBC Format: CAN Database Format

FMI: Functional Mock-up Interface (https://fmi-standard.org/)