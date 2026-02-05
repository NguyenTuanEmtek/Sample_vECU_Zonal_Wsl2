#!/usr/bin/env python3
"""
Enhanced WSL2 Socket Server: Nhận CAN frame, gửi lên VCAN và forward đến Zonal Controller
"""

import socket
import json
import can
import time
import logging
import threading
import queue
from typing import Optional, Dict, Any, List
import zmq  # ZeroMQ cho inter-process communication

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("WSL2SocketServer")

class CANForwarder:
    """Forward CAN messages đến các subscribers (Zonal Controllers)"""
    
    def __init__(self, zmq_port: int = 5555):
        self.zmq_port = zmq_port
        self.context = zmq.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.bind(f"tcp://*:{zmq_port}")
        
        # Queue để lưu tin nhắn
        self.message_queue = queue.Queue(maxsize=1000)
        
        # Subscribers tracking
        self.subscribers = set()
        
        logger.info(f"ZMQ Publisher started on port {zmq_port}")
    
    def forward_message(self, can_data: Dict[str, Any]):
        """Forward CAN message đến Zonal Controllers"""
        try:
            # Gửi qua ZMQ
            self.publisher.send_json(can_data)
            
            # Cũng đưa vào queue cho backup
            try:
                self.message_queue.put_nowait(can_data)
            except queue.Full:
                # Nếu queue đầy, bỏ tin cũ nhất
                try:
                    self.message_queue.get_nowait()
                    self.message_queue.put_nowait(can_data)
                except:
                    pass
            
        except Exception as e:
            logger.error(f"Forward error: {e}")
    
    def get_subscriber_count(self) -> int:
        """Lấy số lượng subscribers"""
        return len(self.subscribers)

class CANMessageProcessor:
    """Xử lý và validate CAN messages"""
    
    def __init__(self):
        # Valid CAN IDs cho hệ thống
        self.valid_ids = {
            0x100: "LIGHT_CONTROL",  # Light control message
            0x101: "DOOR_STATUS",    # Door status
            0x102: "WINDOW_STATUS",  # Window status
            0x200: "DIAGNOSTIC",     # Diagnostic message
            0x7E0: "UDS_TX",         # UDS transmit
            0x7E8: "UDS_RX",         # UDS receive
        }
        
        # Message statistics
        self.stats = {
            'total_received': 0,
            'total_sent': 0,
            'invalid_ids': 0,
            'invalid_data': 0,
            'by_id': {}
        }
    
    def validate_message(self, data: Dict[str, Any]) -> bool:
        """Validate CAN message"""
        # Kiểm tra required fields
        required_fields = ['can_id', 'can_data', 'dlc']
        for field in required_fields:
            if field not in data:
                logger.error(f"Missing required field: {field}")
                return False
        
        # Validate CAN ID
        can_id = data['can_id']
        if not (0x000 <= can_id <= 0x7FF):  # Standard CAN ID range
            logger.error(f"Invalid CAN ID: 0x{can_id:X}")
            self.stats['invalid_ids'] += 1
            return False
        
        # Validate DLC
        dlc = data['dlc']
        if not (0 <= dlc <= 8):
            logger.error(f"Invalid DLC: {dlc}")
            return False
        
        # Validate data length
        can_data = data['can_data']
        if len(can_data) != 8:
            logger.error(f"Invalid data length: {len(can_data)}")
            self.stats['invalid_data'] += 1
            return False
        
        return True
    
    def process_message(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Xử lý và enrich CAN message"""
        self.stats['total_received'] += 1
        
        # Validate
        if not self.validate_message(data):
            return None
        
        # Thêm metadata
        enriched_data = {
            **data,
            'timestamp_ns': time.time_ns(),
            'timestamp_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'direction': 'RX',  # Received from external
            'source': 'vECU',
            'destination': 'ZonalController',
            'validated': True,
        }
        
        # Thêm CAN ID description nếu có
        can_id = data['can_id']
        if can_id in self.valid_ids:
            enriched_data['can_id_description'] = self.valid_ids[can_id]
        
        # Update statistics
        if can_id not in self.stats['by_id']:
            self.stats['by_id'][can_id] = 0
        self.stats['by_id'][can_id] += 1
        
        return enriched_data

class EnhancedWSL2SocketServer:
    """Enhanced socket server với CAN forwarding"""
    
    def __init__(self, host: str = '0.0.0.0', port: int = 8888, 
                 zmq_port: int = 5555, can_interface: str = 'vcan0'):
        self.host = host
        self.port = port
        self.zmq_port = zmq_port
        self.can_interface = can_interface
        
        # Core components
        self.processor = CANMessageProcessor()
        self.forwarder = CANForwarder(zmq_port)
        
        # Network components
        self.server_socket = None
        self.can_bus = None
        
        # State
        self.running = False
        self.client_connected = False
        
        # Initialize
        self.setup_can()
        
        # Statistics
        self.stats = {
            'connections': 0,
            'messages_received': 0,
            'messages_forwarded': 0,
            'messages_sent_to_can': 0,
            'errors': 0,
            'start_time': time.time(),
            'bytes_received': 0
        }
        
        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        
    def setup_can(self):
        """Thiết lập kết nối CAN"""
        try:
            # Kiểm tra VCAN interface
            import subprocess
            result = subprocess.run(['ip', 'link', 'show', self.can_interface],
                                  capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.info(f"Creating VCAN interface: {self.can_interface}")
                subprocess.run(['sudo', 'ip', 'link', 'add', 'dev', self.can_interface, 
                              'type', 'vcan'], capture_output=True)
                subprocess.run(['sudo', 'ip', 'link', 'set', 'up', self.can_interface],
                             capture_output=True)
            
            # Kết nối đến CAN bus
            self.can_bus = can.interface.Bus(
                channel=self.can_interface,
                bustype='socketcan'
            )
            logger.info(f"Connected to CAN interface: {self.can_interface}")
            
        except Exception as e:
            logger.error(f"Cannot setup CAN: {e}")
            raise
    
    def start(self):
        """Bắt đầu server"""
        # Tạo socket server
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)  # Tăng backlog để nhiều client
        self.server_socket.settimeout(1.0)
        
        logger.info(f"Socket server listening on {self.host}:{self.port}")
        logger.info(f"ZMQ Publisher on port {self.zmq_port}")
        logger.info(f"CAN Interface: {self.can_interface}")
        
        self.running = True
        self.monitor_thread.start()
        
        try:
            while self.running:
                try:
                    client_socket, client_address = self.server_socket.accept()
                    self.stats['connections'] += 1
                    logger.info(f"New connection from {client_address}")
                    
                    # Xử lý client trong thread riêng
                    client_thread = threading.Thread(
                        target=self.handle_client,
                        args=(client_socket, client_address),
                        daemon=True
                    )
                    client_thread.start()
                    
                except socket.timeout:
                    continue
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    logger.error(f"Accept error: {e}")
                    time.sleep(1)
                    
        finally:
            self.stop()
    
    def handle_client(self, client_socket: socket.socket, client_address: tuple):
        """Xử lý client connection"""
        client_socket.settimeout(1.0)
        buffer = ""
        self.client_connected = True
        
        try:
            while self.running and self.client_connected:
                try:
                    # Nhận dữ liệu
                    data = client_socket.recv(65536)  # Tăng buffer size
                    if not data:
                        logger.info(f"Client {client_address} disconnected")
                        break
                    
                    self.stats['bytes_received'] += len(data)
                    buffer += data.decode('utf-8', errors='ignore')
                    
                    # Xử lý các messages
                    processed = self.process_buffer(buffer)
                    buffer = processed['remaining_buffer']
                    
                    # Xử lý từng message
                    for message_str in processed['messages']:
                        self.process_incoming_message(message_str, client_address)
                        
                except socket.timeout:
                    continue
                except ConnectionResetError:
                    logger.warning(f"Connection reset by {client_address}")
                    break
                except Exception as e:
                    logger.error(f"Client handling error: {e}")
                    self.stats['errors'] += 1
                    break
                    
        finally:
            client_socket.close()
            self.client_connected = False
            logger.info(f"Client {client_address} connection closed")
    
    def process_buffer(self, buffer: str) -> Dict[str, Any]:
        """Xử lý buffer và trích xuất các messages hoàn chỉnh"""
        messages = []
        
        # Phân tách bằng newline
        while '\n' in buffer:
            message, buffer = buffer.split('\n', 1)
            message = message.strip()
            if message:
                messages.append(message)
        
        return {
            'messages': messages,
            'remaining_buffer': buffer
        }
    
    def process_incoming_message(self, message_str: str, source: tuple):
        """Xử lý một incoming message"""
        try:
            # Parse JSON
            data = json.loads(message_str)
            self.stats['messages_received'] += 1
            
            # Process message
            processed_data = self.processor.process_message(data)
            if not processed_data:
                return
            
            # 1. Gửi lên CAN bus
            self.send_to_can_bus(processed_data)
            
            # 2. Forward đến Zonal Controllers
            self.forwarder.forward_message(processed_data)
            self.stats['messages_forwarded'] += 1
            
            # Log thỉnh thoảng
            if self.stats['messages_received'] % 100 == 0:
                logger.info(f"Processed {self.stats['messages_received']} messages from {source}")
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error from {source}: {e}")
            logger.debug(f"Raw message: {message_str[:100]}...")
            self.stats['errors'] += 1
        except Exception as e:
            logger.error(f"Message processing error: {e}")
            self.stats['errors'] += 1
    
    def send_to_can_bus(self, data: Dict[str, Any]):
        """Gửi CAN message lên bus"""
        try:
            can_id = data['can_id']
            can_data = data['can_data']
            dlc = data['dlc']
            
            # Đảm bảo data là bytes
            if isinstance(can_data, list):
                can_data_bytes = bytes(can_data[:dlc])
            else:
                logger.error(f"Invalid can_data format: {type(can_data)}")
                return
            
            # Tạo CAN message
            message = can.Message(
                arbitration_id=can_id,
                data=can_data_bytes,
                is_extended_id=False,
                dlc=dlc
            )
            
            # Gửi
            self.can_bus.send(message)
            self.stats['messages_sent_to_can'] += 1
            
            # Log chi tiết (thỉnh thoảng)
            if self.stats['messages_sent_to_can'] % 50 == 0:
                # Decode headlamp từ data
                headlamp_bit = (can_data_bytes[0] & 0x01) != 0
                light_level = can_data_bytes[1] if len(can_data_bytes) > 1 else 0
                
                logger.info(f"CAN Sent: ID=0x{can_id:03X}, "
                          f"Headlamp={'ON' if headlamp_bit else 'OFF'}, "
                          f"Light={light_level}%")
                
        except Exception as e:
            logger.error(f"Cannot send to CAN bus: {e}")
            self.stats['errors'] += 1
    
    def monitor_loop(self):
        """Vòng lặp giám sát"""
        while self.running:
            time.sleep(10)  # Cập nhật mỗi 10 giây
            
            # Hiển thị statistics
            elapsed = time.time() - self.stats['start_time']
            if elapsed > 0:
                msg_rate = self.stats['messages_received'] / elapsed
                forward_rate = self.stats['messages_forwarded'] / elapsed
                
                # logger.info(f"STATS: {self.stats['messages_received']} msgs received, "
                #           f"{self.stats['messages_forwarded']} forwarded, "
                #           f"Rate: {msg_rate:.1f}/s, "
                #           f"Subscribers: {self.forwarder.get_subscriber_count()}")
                
                # Processor statistics
                proc_stats = self.processor.stats
                logger.debug(f"PROC STATS: Valid: {proc_stats['total_received']}, "
                           f"Invalid IDs: {proc_stats['invalid_ids']}")
    
    def stop(self):
        """Dừng server"""
        self.running = False
        self.client_connected = False
        
        # Đóng sockets
        if self.server_socket:
            self.server_socket.close()
        
        if self.can_bus:
            self.can_bus.shutdown()
        
        # Đóng ZMQ
        if hasattr(self, 'forwarder'):
            self.forwarder.publisher.close()
            self.forwarder.context.term()
        
        # Hiển thị final statistics
        self.print_final_statistics()
    
    def print_final_statistics(self):
        """In thống kê cuối cùng"""
        elapsed = time.time() - self.stats['start_time']
        
        logger.info("\n" + "="*60)
        logger.info("SERVER FINAL STATISTICS")
        logger.info("="*60)
        logger.info(f"Uptime:               {elapsed:.1f} seconds")
        logger.info(f"Connections:          {self.stats['connections']}")
        logger.info(f"Messages received:    {self.stats['messages_received']}")
        logger.info(f"Messages forwarded:   {self.stats['messages_forwarded']}")
        logger.info(f"Messages sent to CAN: {self.stats['messages_sent_to_can']}")
        logger.info(f"Bytes received:       {self.stats['bytes_received']:,}")
        logger.info(f"Errors:               {self.stats['errors']}")
        
        if elapsed > 0:
            logger.info(f"Avg. message rate:    {self.stats['messages_received']/elapsed:.1f} msg/s")
            logger.info(f"Data rate:            {self.stats['bytes_received']/elapsed/1024:.1f} KB/s")
        
        # Processor statistics
        logger.info("\nProcessor Statistics:")
        proc_stats = self.processor.stats
        for can_id, count in proc_stats['by_id'].items():
            desc = self.processor.valid_ids.get(can_id, "Unknown")
            logger.info(f"  0x{can_id:03X} ({desc}): {count} messages")
        
        logger.info("="*60)

def main():
    """Hàm chính"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Enhanced WSL2 Socket Server for SDV')
    parser.add_argument('--host', type=str, default='0.0.0.0',
                       help='Socket server host')
    parser.add_argument('--port', type=int, default=8888,
                       help='Socket server port')
    parser.add_argument('--zmq-port', type=int, default=5555,
                       help='ZMQ publisher port')
    parser.add_argument('--can-interface', type=str, default='vcan0',
                       help='CAN interface name')
    parser.add_argument('--log-file', type=str,
                       help='Log file path')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Cấu hình logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    
    if args.log_file:
        file_handler = logging.FileHandler(args.log_file)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        file_handler.setLevel(log_level)
        logger.addHandler(file_handler)
    
    logger.setLevel(log_level)
    
    # Khởi chạy server
    server = EnhancedWSL2SocketServer(
        host=args.host,
        port=args.port,
        zmq_port=args.zmq_port,
        can_interface=args.can_interface
    )
    
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        server.stop()

if __name__ == "__main__":
    main()