#!/bin/bash
# start_system.sh - Khởi động toàn bộ hệ thống SDV trong WSL2

set -e

echo "========================================="
echo "Starting SDV System in WSL2"
echo "========================================="

# 1. Thiết lập VCAN interface
echo "[1/3] Setting up VCAN interface..."
sudo modprobe can 2>/dev/null || true
sudo modprobe can_raw 2>/dev/null || true
sudo modprobe vcan 2>/dev/null || true

# Tạo vcan0 nếu chưa có
if ! ip link show vcan0 2>/dev/null; then
    sudo ip link add dev vcan0 type vcan
    sudo ip link set vcan0 mtu 72
    sudo ip link set up vcan0
    echo "  Created vcan0 interface"
fi

# Kiểm tra
echo "  VCAN status:"
ip link show vcan0 | grep -E "(state|mtu)"

# 2. Khởi động WSL2 Socket Server
echo "[2/3] Starting WSL2 Socket Server..."
python3 wsl2_socket_server.py \
    --host 0.0.0.0 \
    --port 8888 \
    --zmq-port 5555 \
    --can-interface vcan0 \
    --log-file logs/socket_server.log &
SERVER_PID=$!
echo "  Socket Server PID: $SERVER_PID"

# Đợi server khởi động
sleep 2

# 3. Khởi động Zonal Controller
echo "[3/3] Starting Zonal Controller..."
python3 zonal_controller.py \
    --dbc lights.dbc \
    --zmq-host localhost \
    --zmq-port 5555 \
    --can-interface vcan0 &
CONTROLLER_PID=$!
echo "  Zonal Controller PID: $CONTROLLER_PID"

# 4. Khởi động CAN Monitor (optional)
echo "[Optional] Starting CAN Monitor..."
candump vcan0 > logs/candump.log 2>&1 &
MONITOR_PID=$!
echo "  CAN Monitor PID: $MONITOR_PID"

echo ""
echo "========================================="
echo "SDV System Started Successfully!"
echo "========================================="
echo "Components:"
echo "  • WSL2 Socket Server: Running"
echo "  • Zonal Controller:   Running"
echo "  • CAN Monitor:        Running"
echo ""
echo "Logs:"
echo "  • Socket Server:  logs/socket_server.log"
echo "  • CAN Traffic:    logs/candump.log"
echo "  • Database:       sdv_can_data.db"
echo ""
echo "Press Ctrl+C to stop all components"
echo "========================================="

# Hàm cleanup
cleanup() {
    echo ""
    echo "Shutting down SDV System..."
    kill $SERVER_PID 2>/dev/null || true
    kill $CONTROLLER_PID 2>/dev/null || true
    kill $MONITOR_PID 2>/dev/null || true
    echo "System stopped."
    exit 0
}

trap cleanup INT TERM

# Giữ script chạy
wait