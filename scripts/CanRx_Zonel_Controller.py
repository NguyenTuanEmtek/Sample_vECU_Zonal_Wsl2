import sys
import json
import time
import threading
import queue
import can  # Thêm thư viện python-can

class ZonalControllerCAN:
    def __init__(self, zone_id, can_interface='vcan0'):
        self.zone_id = zone_id
        self.running = True
        
        # Khởi tạo CAN bus
        self.can_interface = can_interface
        self.can_bus = None
        self.init_can_bus()
        
        # Queue để lưu các CAN message
        self.can_rx_queue = queue.Queue()
        self.can_tx_queue = queue.Queue()
        
        # Dữ liệu zone
        self.zone_data = {
            "zone_id": zone_id,
            "sensors": {},
            "actuators": {},
            "status": "initializing"
        }
        
        # Khởi động các thread
        self.can_rx_thread = threading.Thread(target=self.can_receiver_thread, daemon=True)
        self.can_tx_thread = threading.Thread(target=self.can_transmitter_thread, daemon=True)
        self.zone_processor_thread = threading.Thread(target=self.zone_processor, daemon=True)
        
        print(f"Zonal Controller {zone_id} đang khởi động với CAN interface: {can_interface}")
    
    def init_can_bus(self):
        """Khởi tạo kết nối CAN bus"""
        try:
            self.can_bus = can.interface.Bus(
                channel=self.can_interface,
                bustype='socketcan'
            )
            print(f"Đã kết nối thành công đến CAN bus: {self.can_interface}")
        except Exception as e:
            print(f"Lỗi khi khởi tạo CAN bus: {e}")
            sys.exit(1)
    
    def can_receiver_thread(self):
        """Thread đọc CAN message từ bus"""
        print(f"CAN Receiver thread đang chạy trên {self.can_interface}...")
        
        while self.running:
            try:
                # Đọc message từ CAN bus với timeout
                message = self.can_bus.recv(timeout=1.0)
                
                if message is not None:
                    # Xử lý và đưa vào queue
                    can_msg = {
                        'timestamp': time.time(),
                        'id': message.arbitration_id,
                        'data': message.data.hex(),
                        'dlc': message.dlc,
                        'is_extended': message.is_extended_id,
                        'is_remote': message.is_remote_frame
                    }
                    
                    # Đưa vào queue để xử lý
                    self.can_rx_queue.put(can_msg)
                    
                    # Log message nhận được
                    print(f"[CAN RX] ID: {hex(message.arbitration_id)}, "
                          f"Data: {message.data.hex()}, DLC: {message.dlc}")
                    
            except can.CanError as e:
                print(f"CAN Error: {e}")
            except Exception as e:
                print(f"Lỗi trong CAN receiver: {e}")
                time.sleep(0.1)
    
    def can_transmitter_thread(self):
        """Thread gửi CAN message ra bus"""
        print("CAN Transmitter thread đang chạy...")
        
        while self.running:
            try:
                # Lấy message từ queue để gửi (blocking với timeout)
                message = self.can_tx_queue.get(timeout=1.0)
                
                # Tạo CAN message
                can_msg = can.Message(
                    arbitration_id=message['id'],
                    data=bytes.fromhex(message['data']),
                    is_extended_id=message.get('is_extended', False),
                    is_remote_frame=message.get('is_remote', False)
                )
                
                # Gửi message
                self.can_bus.send(can_msg)
                
                print(f"[CAN TX] Đã gửi: ID={hex(message['id'])}, "
                      f"Data={message['data']}")
                
            except queue.Empty:
                # Không có message để gửi
                pass
            except can.CanError as e:
                print(f"CAN TX Error: {e}")
            except Exception as e:
                print(f"Lỗi trong CAN transmitter: {e}")
                time.sleep(0.1)
    
    def zone_processor(self):
        """Xử lý CAN message và điều khiển zone"""
        print("Zone Processor thread đang chạy...")
        
        # Ví dụ về ID CAN cho zone này
        ZONE_SENSOR_BASE_ID = 0x100 + self.zone_id
        ZONE_ACTUATOR_BASE_ID = 0x200 + self.zone_id
        
        while self.running:
            try:
                # Xử lý các message nhận được
                while not self.can_rx_queue.empty():
                    msg = self.can_rx_queue.get_nowait()
                    
                    # Phân tích CAN message
                    self.process_can_message(msg)
                    
                    # Cập nhật dữ liệu zone
                    self.update_zone_data(msg)
                
                # Ví dụ: Gửi periodic status message
                self.send_zone_status()
                
                # Xử lý logic điều khiển zone
                self.control_logic()
                
                time.sleep(0.01)  # 10ms cycle
                
            except Exception as e:
                print(f"Lỗi trong zone processor: {e}")
                time.sleep(0.1)
    
    def process_can_message(self, msg):
        """Phân tích và xử lý CAN message"""
        can_id = msg['id']
        data = msg['data']
        
        # Ví dụ: Xử lý các loại message khác nhau
        if can_id == 0x100:  # System status message
            self.handle_system_status(data)
        elif 0x200 <= can_id <= 0x2FF:  # Sensor data messages
            self.handle_sensor_data(can_id, data)
        elif 0x300 <= can_id <= 0x3FF:  # Actuator control messages
            self.handle_actuator_command(can_id, data)
    
    def handle_system_status(self, data):
        """Xử lý system status message"""
        status_byte = int(data[:2], 16)
        self.zone_data['system_status'] = status_byte
        # Thêm logic xử lý ở đây
    
    def handle_sensor_data(self, can_id, data):
        """Xử lý sensor data"""
        sensor_id = can_id & 0xFF  # Lấy 8 bit thấp
        self.zone_data['sensors'][sensor_id] = {
            'data': data,
            'timestamp': time.time()
        }
    
    def handle_actuator_command(self, can_id, data):
        """Xử lý actuator command"""
        actuator_id = can_id & 0xFF
        self.zone_data['actuators'][actuator_id] = {
            'command': data,
            'timestamp': time.time()
        }
        
        # Gửi feedback
        feedback_msg = {
            'id': 0x400 + actuator_id,
            'data': data,  # Echo command để confirm
            'is_extended': False
        }
        self.can_tx_queue.put(feedback_msg)
    
    def send_zone_status(self):
        """Gửi periodic zone status message"""
        status_msg = {
            'id': 0x500 + self.zone_id,
            'data': f'{self.zone_id:02X}01{int(time.time()) % 256:02X}',  # Ví dụ
            'is_extended': False
        }
        self.can_tx_queue.put(status_msg)
    
    def update_zone_data(self, msg):
        """Cập nhật dữ liệu zone từ CAN message"""
        # Thêm logic cập nhật ở đây
        pass
    
    def control_logic(self):
        """Logic điều khiển zone"""
        # Thêm logic điều khiển cụ thể ở đây
        pass
    
    def send_can_message(self, can_id, data, is_extended=False):
        """API để gửi CAN message từ bên ngoài"""
        message = {
            'id': can_id,
            'data': data,
            'is_extended': is_extended
        }
        self.can_tx_queue.put(message)
    
    def get_zone_data(self):
        """Lấy dữ liệu zone hiện tại"""
        return self.zone_data.copy()
    
    def start(self):
        """Khởi động zonal controller"""
        print(f"Zonal Controller {self.zone_id} đang khởi động...")
        
        # Khởi động các thread
        self.can_rx_thread.start()
        # self.can_tx_thread.start()
        self.zone_processor_thread.start()
        
        print(f"Zonal Controller {self.zone_id} đã khởi động")
    
    def stop(self):
        """Dừng zonal controller"""
        print(f"Zonal Controller {self.zone_id} đang dừng...")
        self.running = False
        
        # Đợi các thread kết thúc
        self.can_rx_thread.join(timeout=2)
        # self.can_tx_thread.join(timeout=2)
        self.zone_processor_thread.join(timeout=2)
        
        # Đóng CAN bus
        if self.can_bus:
            self.can_bus.shutdown()
        
        print(f"Zonal Controller {self.zone_id} đã dừng")

# Hàm main để test
def main():
    if len(sys.argv) > 1:
        zone_id = int(sys.argv[1])
    else:
        zone_id = 1
    
    # Tạo zonal controller với CAN interface cụ thể
    # Có thể thay đổi 'vcan0' thành 'can0' nếu dùng CAN bus thật
    zc = ZonalControllerCAN(zone_id=zone_id, can_interface='vcan0')
    
    try:
        # Khởi động
        zc.start()
        
        # Chạy trong 60 giây
        time.sleep(60)
        
    except KeyboardInterrupt:
        print("\nNhận tín hiệu dừng từ người dùng...")
    
    finally:
        # Dừng controller
        zc.stop()

if __name__ == "__main__":
    main()