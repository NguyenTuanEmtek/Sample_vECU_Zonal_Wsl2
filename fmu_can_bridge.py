import numpy as np
import time
import socket
import json
import logging
import asyncio
from datetime import datetime
from fmpy import read_model_description, extract
from fmpy.fmi2 import FMU2Slave
import threading
from typing import Dict, Any, Optional

# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FMUCANBridge")


class FMUSimulator:
    """Lớp mô phỏng FMU autoLamp (dựa trên code của bạn)"""
    
    def __init__(self, fmu_path: str):
        self.fmu_path = fmu_path
        self.fmu = None
        self.md = None
        self.unzipdir = None
        
        # Biến trạng thái
        self.t = 0.0
        self.dt = 0.05
        self.running = False
        
        # Giá trị hiện tại
        self.current_values = {
            'ambient': 0.0,
            'headlamp': False,
            'timestamp': 0.0
        }
        
    def load(self):
        """Tải FMU"""
        logger.info(f"Loading FMU: {self.fmu_path}")
        self.md = read_model_description(self.fmu_path)
        self.unzipdir = extract(self.fmu_path)
        
        # Tìm các biến trong FMU
        self.log_fmu_variables()
        
        # Tạo instance FMU
        self.fmu = FMU2Slave(
            guid=self.md.guid,
            unzipDirectory=self.unzipdir,
            modelIdentifier=self.md.coSimulation.modelIdentifier,
            instanceName="autoLampInstance"
        )
        
        return self
        
    def log_fmu_variables(self):
        """Log thông tin các biến trong FMU"""
        logger.info("FMU Variables:")
        for var in self.md.modelVariables:
            logger.info(f"  {var.name}: type={var.type}, causality={var.causality}, "
                       f"vr={var.valueReference}, desc={var.description}")
    
    def get_variable_reference(self, name: str):
        """Lấy value reference theo tên biến"""
        for var in self.md.modelVariables:
            if var.name == name:
                return var.valueReference
        return None
    
    def setup(self):
        """Thiết lập FMU"""
        if not self.fmu:
            raise RuntimeError("FMU not loaded")
            
        logger.info("Setting up FMU simulation...")
        
        # Instantiate
        self.fmu.instantiate()
        
        # Setup experiment
        self.fmu.setupExperiment(startTime=0)
        self.fmu.enterInitializationMode()
        
        # Set initial values nếu cần
        # self.fmu.setReal([vr], [value])
        
        self.fmu.exitInitializationMode()
        
        logger.info("FMU setup completed")
    
    def run_step(self) -> Dict[str, Any]:
        """Chạy một bước mô phỏng"""
        if not self.fmu:
            raise RuntimeError("FMU not initialized")
        
        # Tạo tín hiệu ambient (giữ nguyên logic của bạn)
        ambient = 250 + 100 * np.sin(self.t)
        
        # Đặt input ambient_light (giả sử vr=0 cho ambient)
        # Bạn cần kiểm tra value reference thực tế từ FMU
        ambient_vr = self.get_variable_reference('ambient_light') or 0
        self.fmu.setReal([ambient_vr], [ambient])
        
        # Thực hiện bước mô phỏng
        self.fmu.doStep(self.t, self.dt)
        
        # Lấy output headlamp (giả sử vr=0 cho headlamp boolean)
        # headlamp_vr = self.get_variable_reference('headlamp') or 0
        headlamp_names = ['headlamp', 'headLamp', 'head_lamp', 'HeadLamp', 'highBeam']
        for var in self.md.modelVariables:
                if any(name in var.name.lower() for name in headlamp_names):
                    self.headlamp_vr = var.valueReference
                    break
        headlamp = self.fmu.getBoolean([self.headlamp_vr])[0]
        # try:
        #     headlamp_bool = self.fmu.getBoolean([self.headlamp_vr])[0]  # Thử boolean
        # except:
        #     try:
        #         headlamp_real = self.fmu.getReal([self.headlamp_vr])[0]  # Thử real
        #         headlamp_bool = headlamp_real > 0.5  # Convert
        #     except:
        #         try:
        #             headlamp_int = self.fmu.getInteger([self.headlamp_vr])[0]  # Thử integer
        #             headlamp_bool = bool(headlamp_int)
        #         except:
        #             headlamp_bool = False  # Mặc định
        # Lấy các output khác nếu có
        # tail_lamp_vr = self.get_variable_reference('taillamp') or 1
        # taillamp = self.fmu.getBoolean([tail_lamp_vr])[0] if tail_lamp_vr != 1 else False
        
        # Cập nhật trạng thái
        self.current_values = {
            'ambient': ambient,
            'headlamp': headlamp,
            'tailLamp': False,  # Giả định, thay bằng biến thực tế nếu có
            'brakeLamp': False, # Giả định
            'vehicle_speed': 50 + 10 * np.sin(self.t),  # Mô phỏng
            'light_level': int((ambient / 400) * 100),  # Chuyển thành %
            'timestamp': time.time(),
            'simulation_time': self.t
        }
        
        # Tăng thời gian
        self.t += self.dt

        # logger.debug(f"FMU Step: t={self.t}, ambient={ambient}, headlamp={headlamp}")
        
        return self.current_values.copy()
    
    def get_current_values(self) -> Dict[str, Any]:
        """Lấy giá trị hiện tại"""
        return self.current_values.copy()
    
    def cleanup(self):
        """Dọn dẹp tài nguyên"""
        if self.fmu:
            self.fmu.terminate()
            self.fmu.freeInstance()
        logger.info("FMU cleanup completed")

class CANMessageEncoder:
    """Mã hóa dữ liệu FMU thành CAN frame"""
    
    def __init__(self, config_path: str = None):
        # Cấu hình mặc định
        self.config = {
            'light_control': {
                'id': 0x100,
                'dlc': 8,
                'signals': {
                    'headlamp': {'byte': 0, 'bit': 0, 'type': 'bool'},
                    'tailLamp': {'byte': 0, 'bit': 1, 'type': 'bool'},
                    'brakeLamp': {'byte': 0, 'bit': 2, 'type': 'bool'},
                    'indicator_left': {'byte': 0, 'bit': 3, 'type': 'bool'},
                    'indicator_right': {'byte': 0, 'bit': 4, 'type': 'bool'},
                    'light_level': {'byte': 1, 'type': 'uint8', 'scale': 2.55},
                    'vehicle_speed': {'byte': 2, 'type': 'uint8'}
                }
            }
        }
        
        if config_path:
            import yaml
            with open(config_path, 'r') as f:
                self.config.update(yaml.safe_load(f))
    
    def encode(self, fmu_data: Dict[str, Any]) -> Dict[str, Any]:
        """Mã hóa dữ liệu FMU thành CAN frame"""
        config = self.config['light_control']
        
        # logger.debug(f"Encoding FMU data: {fmu_data}")

        # Tạo data buffer 8 bytes
        data = bytearray(8)
        
        # Đặt các tín hiệu boolean
        for signal_name, signal_config in config['signals'].items():
            if signal_name in fmu_data and signal_config.get('type') == 'bool':
                byte_pos = signal_config['byte']
                bit_pos = signal_config['bit']
                
                if fmu_data[signal_name]:
                    data[byte_pos] |= (1 << bit_pos)
        
        # Đặt light_level
        if 'light_level' in fmu_data:
            light_val = int(fmu_data['light_level'])
            light_val = max(0, min(255, light_val))
            data[1] = light_val
        
        # Đặt vehicle_speed
        if 'vehicle_speed' in fmu_data:
            speed_val = int(fmu_data['vehicle_speed'])
            speed_val = max(0, min(255, speed_val))
            data[2] = speed_val
        
        # Thêm ambient vào byte 3-4 (dạng uint16)
        if 'ambient' in fmu_data:
            ambient_val = int(fmu_data['ambient'])
            data[3] = (ambient_val >> 8) & 0xFF  # High byte
            data[4] = ambient_val & 0xFF         # Low byte

        logger.debug(f"Encoded CAN frame: ID=0x{config['id']:X}, Data: {list(data)}")
        
        return {
            'can_id': config['id'],
            'can_data': list(data),
            'dlc': config['dlc'],
            'timestamp': fmu_data.get('timestamp', time.time()),
            'sim_time': fmu_data.get('simulation_time', 0),
            'source': 'autoLamp_fmu'
        }

class WSL2SocketBridge:
    """Bridge kết nối Windows với WSL2 qua socket"""
    
    def __init__(self, host: str = "localhost", port: int = 8888):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
    def connect(self) -> bool:
        """Kết nối đến WSL2 socket server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(5.0)
            self.socket.connect((self.host, self.port))
            self.connected = True
            self.reconnect_attempts = 0
            logger.info(f"Connected to WSL2 at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.connected = False
            return False
    
    def reconnect(self) -> bool:
        """Thử kết nối lại"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error("Max reconnection attempts reached")
            return False
        
        self.reconnect_attempts += 1
        logger.info(f"Reconnecting attempt {self.reconnect_attempts}...")
        time.sleep(1)
        return self.connect()
    
    def send_data(self, data: Dict[str, Any]) -> bool:
        """Gửi dữ liệu đến WSL2"""
        if not self.connected or not self.socket:
            if not self.reconnect():
                return False
        
        try:
            # Chuyển thành JSON và thêm newline delimiter
            json_data = json.dumps(data, default=str)
            message = json_data + '\n'
            # logger.debug(f"Sending to WSL2: {data}")
            self.socket.sendall(message.encode('utf-8'))
            return True
            
        except socket.timeout:
            logger.warning("Socket timeout")
            self.connected = False
            return False
        except ConnectionError as e:
            logger.error(f"Connection error: {e}")
            self.connected = False
            return False
        except Exception as e:
            logger.error(f"Send error: {e}")
            return False
    
    def close(self):
        """Đóng kết nối"""
        if self.socket:
            self.socket.close()
        self.connected = False
        logger.info("Socket connection closed")

class FMUCANBridge:
    """Bridge chính: FMU → CAN encoding → WSL2"""
    
    def __init__(self, fmu_path: str, wsl_host: str = "localhost", wsl_port: int = 8888):
        self.fmu_sim = FMUSimulator(fmu_path)
        self.encoder = CANMessageEncoder()
        self.socket_bridge = WSL2SocketBridge(wsl_host, wsl_port)
        
        self.running = False
        self.cycle_time = 0.1  # 100ms
        self.sent_messages = 0
        self.failed_messages = 0
        
        # Statistics
        self.stats = {
            'start_time': time.time(),
            'messages_sent': 0,
            'messages_failed': 0,
            'last_sent': None
        }
    
    def initialize(self):
        """Khởi tạo hệ thống"""
        logger.info("Initializing FMU CAN Bridge...")
        
        # Load FMU
        self.fmu_sim.load()
        self.fmu_sim.setup()
        
        # Connect to WSL2
        if not self.socket_bridge.connect():
            logger.warning("Failed to connect to WSL2, running in local mode...")
        
        logger.info("FMU CAN Bridge initialized")
    
    def run_simulation(self, duration: float = 30.0):
        """Chạy mô phỏng trong thời gian xác định"""
        logger.info(f"Starting simulation for {duration} seconds...")
        
        self.running = True
        start_time = time.time()
        
        try:
            while time.time() - start_time < duration and self.running:
                # Chạy một bước FMU
                fmu_data = self.fmu_sim.run_step()
                
                # Mã hóa thành CAN frame
                can_frame = self.encoder.encode(fmu_data)
                
                # Gửi qua socket đến WSL2
                if self.socket_bridge.send_data(can_frame):
                    self.stats['messages_sent'] += 1
                    self.stats['last_sent'] = time.time()
                    
                    # Log thỉnh thoảng
                    if self.stats['messages_sent'] % 10 == 0:
                        headlamp_status = "ON" if fmu_data['headlamp'] else "OFF"
                        logger.info(f"Sent: headLamp={headlamp_status}, "
                                  f"ambient={fmu_data['ambient']:.1f}, "
                                  f"total={self.stats['messages_sent']}")
                else:
                    self.stats['messages_failed'] += 1
                
                # Đợi đến chu kỳ tiếp theo
                elapsed = time.time() - (start_time + self.fmu_sim.t)
                sleep_time = max(0, self.cycle_time - elapsed)
                time.sleep(sleep_time)
                
        except KeyboardInterrupt:
            logger.info("Simulation interrupted by user")
        except Exception as e:
            logger.error(f"Simulation error: {e}")
        finally:
            self.stop()
    
    def run_continuous(self):
        """Chạy mô phỏng liên tục"""
        logger.info("Starting continuous simulation...")
        
        self.running = True
        last_stats_time = time.time()
        
        try:
            while self.running:
                # Chạy một bước FMU
                fmu_data = self.fmu_sim.run_step()
                
                # Mã hóa thành CAN frame
                can_frame = self.encoder.encode(fmu_data)
                
                # Gửi qua socket
                if self.socket_bridge.send_data(can_frame):
                    self.stats['messages_sent'] += 1
                    
                    # Hiển thị thống kê mỗi 5 giây
                    current_time = time.time()
                    if current_time - last_stats_time >= 5:
                        elapsed = current_time - self.stats['start_time']
                        rate = self.stats['messages_sent'] / elapsed if elapsed > 0 else 0
                        
                        headlamp_status = "ON" if fmu_data['headlamp'] else "OFF"
                        logger.info(f"Rate: {rate:.1f} msg/sec, "
                                  f"Total: {self.stats['messages_sent']}, "
                                  f"HeadLamp: {headlamp_status}, "
                                  f"Ambient: {fmu_data['ambient']:.1f}")
                        
                        last_stats_time = current_time
                else:
                    self.stats['messages_failed'] += 1
                
                # Điều chỉnh sleep time để giữ đúng tốc độ mô phỏng
                target_time = self.fmu_sim.t / self.fmu_sim.dt * self.cycle_time
                actual_time = time.time() - self.stats['start_time']
                sleep_time = max(0, target_time - actual_time)
                time.sleep(sleep_time)
                
        except KeyboardInterrupt:
            logger.info("Simulation stopped by user")
        except Exception as e:
            logger.error(f"Simulation error: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """Dừng hệ thống"""
        self.running = False
        self.fmu_sim.cleanup()
        self.socket_bridge.close()
        
        # Hiển thị thống kê cuối
        elapsed = time.time() - self.stats['start_time']
        logger.info("\n=== Simulation Statistics ===")
        logger.info(f"Total runtime: {elapsed:.1f} seconds")
        logger.info(f"Messages sent: {self.stats['messages_sent']}")
        logger.info(f"Messages failed: {self.stats['messages_failed']}")
        
        if elapsed > 0:
            rate = self.stats['messages_sent'] / elapsed
            logger.info(f"Average rate: {rate:.1f} messages/second")
        
        logger.info("FMU CAN Bridge stopped")

class KuksaCANBridge:
    """Bridge tích hợp Kuksa VSS (giữ nguyên chức năng của bạn)"""
    
    def __init__(self, fmu_path: str):
        self.fmu_sim = FMUSimulator(fmu_path)
        self.encoder = CANMessageEncoder()
        
    async def run_with_kuksa(self, kuksa_host: str = "localhost", kuksa_port: int = 55555):
        """Chạy với Kuksa VSS integration"""
        from kuksa_client.grpc import Datapoint
        from kuksa_client.grpc.aio import VSSClient
        
        # Khởi tạo FMU
        self.fmu_sim.load()
        self.fmu_sim.setup()
        
        # Kết nối Kuksa
        try:
            async with VSSClient(host=kuksa_host, port=kuksa_port) as client:
                logger.info(f"Connected to Kuksa VSS at {kuksa_host}:{kuksa_port}")
                
                while True:
                    # Chạy một bước FMU
                    fmu_data = self.fmu_sim.run_step()
                    
                    # Gửi đến Kuksa
                    await self.send_to_kuksa(client, fmu_data)
                    
                    # Mã hóa và log CAN frame (dùng cho debug)
                    can_frame = self.encoder.encode(fmu_data)
                    # logger.debug(f"CAN Frame: {can_frame}")
                    
                    # Đợi
                    await asyncio.sleep(self.fmu_sim.dt)
                    
        except Exception as e:
            logger.error(f"Kuksa error: {e}")
        finally:
            self.fmu_sim.cleanup()
    
    async def send_to_kuksa(self, client, fmu_data: Dict[str, Any]):
        """Gửi dữ liệu đến Kuksa VSS"""
        try:
            sample_data = {
                "Vehicle.Body.Lights.AmbientLight": float(fmu_data.get('ambient', 0)),
                "Vehicle.Body.Lighting.Threshold": 300.0,  # Giá trị mặc định
                "Vehicle.Body.Lighting.Hysteresis": 50.0,   # Giá trị mặc định
                "Vehicle.Body.Lights.IsHighBeamOn": bool(fmu_data.get('headlamp', False)),
                "Vehicle.Body.Lighting.Power": 0.0  # Giá trị mặc định
            }
            
            data_to_send = {}
            for key, value in sample_data.items():
                data_to_send[key] = Datapoint(value=value)
            
            await client.set_current_values(data_to_send)
            # logger.debug(f"Sent to Kuksa: headlamp={sample_data['Vehicle.Body.Lights.IsHighBeamOn']}")
            
        except Exception as e:
            logger.error(f"Failed to send to Kuksa: {e}")

def main():
    """Hàm chính với CLI interface"""
    import argparse
    
    parser = argparse.ArgumentParser(description='FMU CAN Bridge for SDV')
    parser.add_argument('--fmu', type=str, default=r"C:\Users\LOQ\Workspace\06_Emtek\01_Workspace\SDV_zonal\FMU\autoLamp.fmu",
                       help='Path to FMU file')
    parser.add_argument('--mode', type=str, choices=['can', 'kuksa', 'both'], default='can',
                       help='Operation mode')
    parser.add_argument('--duration', type=float, default=30.0,
                       help='Simulation duration in seconds (for can mode)')
    parser.add_argument('--wsl-host', type=str, default='localhost',
                       help='WSL2 host address')
    parser.add_argument('--wsl-port', type=int, default=8888,
                       help='WSL2 socket port')
    parser.add_argument('--kuksa-host', type=str, default='localhost',
                       help='Kuksa VSS host')
    parser.add_argument('--kuksa-port', type=int, default=55555,
                       help='Kuksa VSS port')
    parser.add_argument('--cycle-time', type=float, default=0.1,
                       help='Cycle time in seconds')
    
    args = parser.parse_args()
    
    if args.mode == 'can':
        # Chạy với CAN bridge
        bridge = FMUCANBridge(args.fmu, args.wsl_host, args.wsl_port)
        bridge.cycle_time = args.cycle_time
        bridge.initialize()
        bridge.run_simulation(args.duration)
        
    elif args.mode == 'kuksa':
        # Chạy với Kuksa VSS
        bridge = KuksaCANBridge(args.fmu)
        asyncio.run(bridge.run_with_kuksa(args.kuksa_host, args.kuksa_port))
        
    elif args.mode == 'both':
        # Chạy cả hai (cần xử lý async)
        async def run_both():
            can_bridge = FMUCANBridge(args.fmu, args.wsl_host, args.wsl_port)
            can_bridge.initialize()
            
            kuksa_bridge = KuksaCANBridge(args.fmu)
            
            # Tạo tasks cho cả hai
            import threading
            
            def run_can():
                can_bridge.run_simulation(args.duration)
            
            # Chạy CAN trong thread riêng
            can_thread = threading.Thread(target=run_can)
            can_thread.start()
            
            # Chạy Kuksa trong main thread (async)
            await kuksa_bridge.run_with_kuksa(args.kuksa_host, args.kuksa_port)
            
            can_thread.join()
        
        asyncio.run(run_both())

if __name__ == "__main__":
    main()