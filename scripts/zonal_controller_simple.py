#!/usr/bin/env python3
"""
Zonal Controller Simple Version: Nhận CAN messages và map sang VSS
"""

import zmq
import json
import time
import logging
from typing import Dict, Any, List

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ZonalControllerSimple")

class SimpleZonalController:
    """Zonal Controller đơn giản"""
    
    def __init__(self, zmq_host: str = "localhost", zmq_port: int = 5555):
        self.zmq_host = zmq_host
        self.zmq_port = zmq_port
        
        # ZMQ setup
        self.context = zmq.Context()
        self.subscriber = self.context.socket(zmq.SUB)
        
        # VSS mapping cho CAN ID 0x100
        self.vss_mapping = {
            0x100: {
                'bit0': 'Vehicle.Body.Lights.IsHighBeamOn',      # headLamp
                'bit1': 'Vehicle.Body.Lights.IsTailLightOn',     # tailLamp
                'bit2': 'Vehicle.Body.Lights.IsBrakeLightOn',    # brakeLamp
                'byte1': 'Vehicle.Body.Lights.AmbientLight',     # light_level
                'byte2': 'Vehicle.Speed',                        # vehicle_speed
            }
        }
        
        # Statistics
        self.stats = {
            'messages_received': 0,
            'errors': 0,
            'start_time': time.time()
        }
        
        self.running = False
    
    def connect_to_zmq(self):
        """Kết nối đến ZMQ publisher"""
        try:
            zmq_address = f"tcp://{self.zmq_host}:{self.zmq_port}"
            self.subscriber.connect(zmq_address)
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "")
            logger.info(f"Connected to ZMQ publisher at {zmq_address}")
            return True
        except Exception as e:
            logger.error(f"Cannot connect to ZMQ: {e}")
            return False
    
    def decode_can_data(self, can_id: int, can_data: List[int]) -> Dict[str, Any]:
        """Decode CAN data đơn giản"""
        decoded = {}
        
        if can_id == 0x100:
            # Decode byte 0 (boolean signals)
            if len(can_data) > 0:
                byte0 = can_data[0]
                decoded['headLamp'] = bool(byte0 & 0x01)
                decoded['tailLamp'] = bool(byte0 & 0x02)
                decoded['brakeLamp'] = bool(byte0 & 0x04)
                decoded['indicatorLeft'] = bool(byte0 & 0x08)
                decoded['indicatorRight'] = bool(byte0 & 0x10)
            
            # Decode byte 1 (light level)
            if len(can_data) > 1:
                decoded['lightLevel'] = can_data[1]
            
            # Decode byte 2 (vehicle speed)
            if len(can_data) > 2:
                decoded['vehicleSpeed'] = can_data[2]
            
            # Decode ambient (bytes 3-4)
            if len(can_data) > 4:
                ambient = (can_data[3] << 8) | can_data[4]
                decoded['ambient'] = ambient
        
        return decoded
    
    def map_to_vss(self, can_id: int, decoded_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Map decoded data sang VSS"""
        vss_signals = []
        
        if can_id == 0x100:
            # Map từng signal
            if 'headLamp' in decoded_data:
                vss_signals.append({
                    'path': 'Vehicle.Body.Lights.IsHighBeamOn',
                    'value': decoded_data['headLamp'],
                    'type': 'boolean',
                    'timestamp': time.time()
                })
            
            if 'tailLamp' in decoded_data:
                vss_signals.append({
                    'path': 'Vehicle.Body.Lights.IsTailLightOn',
                    'value': decoded_data['tailLamp'],
                    'type': 'boolean',
                    'timestamp': time.time()
                })
            
            if 'lightLevel' in decoded_data:
                vss_signals.append({
                    'path': 'Vehicle.Body.Lights.AmbientLight',
                    'value': decoded_data['lightLevel'],
                    'type': 'uint8',
                    'timestamp': time.time()
                })
            
            if 'vehicleSpeed' in decoded_data:
                vss_signals.append({
                    'path': 'Vehicle.Speed',
                    'value': decoded_data['vehicleSpeed'],
                    'type': 'uint8',
                    'timestamp': time.time()
                })
        
        return vss_signals
    
    def process_message(self, data: Dict[str, Any]):
        """Xử lý một message"""
        try:
            self.stats['messages_received'] += 1
            
            can_id = data.get('can_id')
            can_data = data.get('can_data', [])
            
            if not can_id or not can_data:
                return
            
            # Decode CAN data
            decoded = self.decode_can_data(can_id, can_data)
            
            # Map sang VSS
            vss_signals = self.map_to_vss(can_id, decoded)
            
            # Log thông tin
            if self.stats['messages_received'] % 10 == 0:
                headlamp_status = "ON" if decoded.get('headLamp', False) else "OFF"
                light_level = decoded.get('lightLevel', 0)
                
                logger.info(f"CAN 0x{can_id:03X}: Headlamp={headlamp_status}, Light={light_level}%")
                
                for vss_signal in vss_signals:
                    logger.info(f"  → {vss_signal['path']}: {vss_signal['value']}")
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            self.stats['errors'] += 1
    
    def run(self):
        """Chạy controller"""
        logger.info("🚗 Starting Simple Zonal Controller...")
        
        if not self.connect_to_zmq():
            logger.error("❌ Cannot connect to ZMQ")
            return
        
        self.running = True
        logger.info("✅ Ready. Waiting for CAN messages...")
        
        try:
            while self.running:
                try:
                    # Nhận message từ ZMQ
                    message = self.subscriber.recv_json()
                    self.process_message(message)
                    
                except zmq.ZMQError as e:
                    logger.error(f"ZMQ error: {e}")
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"Error: {e}")
                    time.sleep(1)
                    
        except KeyboardInterrupt:
            logger.info("⏹️ Stopped by user")
        finally:
            self.stop()
    
    def stop(self):
        """Dừng controller"""
        self.running = False
        
        if hasattr(self, 'subscriber'):
            self.subscriber.close()
        
        if hasattr(self, 'context'):
            self.context.term()
        
        # Print statistics
        elapsed = time.time() - self.stats['start_time']
        logger.info(f"\n📊 Statistics: {self.stats['messages_received']} messages, "
                  f"{self.stats['errors']} errors, "
                  f"{elapsed:.1f} seconds")

def main():
    """Hàm chính"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Simple Zonal Controller')
    parser.add_argument('--zmq-host', type=str, default="localhost",
                       help='ZMQ publisher host')
    parser.add_argument('--zmq-port', type=int, default=5555,
                       help='ZMQ publisher port')
    
    args = parser.parse_args()
    
    controller = SimpleZonalController(
        zmq_host=args.zmq_host,
        zmq_port=args.zmq_port
    )
    
    try:
        controller.run()
    except Exception as e:
        logger.error(f"Controller error: {e}")

if __name__ == "__main__":
    main()