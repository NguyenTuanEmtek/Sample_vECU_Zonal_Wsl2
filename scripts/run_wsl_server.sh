#!/bin/bash
echo "Starting WSL2 Socket Server..."

# Thiết lập VCAN
sudo modprobe can 2>/dev/null
sudo modprobe can_raw 2>/dev/null
sudo modprobe vcan 2>/dev/null

# Tạo VCAN nếu chưa có
if ! ip link show vcan0 2>/dev/null; then
    sudo ip link add dev vcan0 type vcan
    sudo ip link set vcan0 mtu 72
    sudo ip link set up vcan0
    echo "Created vcan0 interface"
fi

# Kiểm tra VCAN
echo "VCAN Status:"
ip link show vcan0

# Khởi động socket server
echo ""
echo "Starting Socket Server..."
python3 wsl2_socket_server.py --host 0.0.0.0 --port 8888

echo "Server stopped."