#!/usr/bin/env python3
"""
Zonal Controller: Nhận CAN messages, decode bằng DBC, map sang VSS
"""

import zmq
import can
import cantools
import json
import time
import logging
import threading
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field
from datetime import datetime
import sqlite3
import yaml
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ZonalController")

@dataclass
class CANSignal:
    """Biểu diễn một CAN signal"""
    name: str
    value: Any
    unit: str = ""
    min_value: float = 0.0
    max_value: float = 0.0
    description: str = ""

@dataclass
class CANMessage:
    """Biểu diễn một CAN message đã decode"""
    timestamp: float
    can_id: int
    can_id_hex: str
    signals: List[CANSignal]
    raw_data: bytes
    dlc: int
    can_id_description: str = ""
    direction: str = "RX"
    source: str = ""

@dataclass
class VSSSignal:
    """Biểu diễn một VSS signal"""
    path: str
    value: Any
    timestamp: float
    data_type: str = "bool"
    min_value: float = 0.0
    max_value: float = 0.0
    unit: str = ""
    description: str = ""

class DBCManager:
    """Quản lý DBC database"""
    
    def __init__(self, dbc_path: str):
        self.dbc_path = dbc_path
        self.db = None
        self.load_database()
        
        # Cache cho message definitions
        self.message_cache = {}
        
    def load_database(self):
        """Load DBC file"""
        try:
            self.db = cantools.database.load_file(self.dbc_path)
            logger.info(f"Loaded DBC database from {self.dbc_path}")
            logger.info(f"Messages: {len(self.db.messages)}")
            
            # Log thông tin về messages
            for msg in self.db.messages:
                logger.debug(f"  Message: 0x{msg.frame_id:03X} {msg.name} - {len(msg.signals)} signals")
                
        except Exception as e:
            logger.error(f"Failed to load DBC: {e}")
            # Tạo database ảo nếu file không tồn tại
            self.create_virtual_database()
    
    def create_virtual_database(self):
        """Tạo virtual database cho CAN ID 0x100 nếu không có DBC file"""
        logger.warning(f"DBC file not found at {self.dbc_path}, creating virtual database")
        
        # Tạo database trống
        self.db = cantools.database.Database()
        
        # Thêm message cho light control (0x100)
        light_control_msg = cantools.database.can.Message(
            frame_id=0x100,
            name='LIGHT_CONTROL',
            length=8,
            signals=[
                cantools.database.can.Signal(
                    name='headLamp',
                    start=0,
                    length=1,
                    is_signed=False,
                    scale=1,
                    offset=0,
                    minimum=0,
                    maximum=1,
                    unit='',
                    receivers=['ZC']
                ),
                cantools.database.can.Signal(
                    name='tailLamp',
                    start=1,
                    length=1,
                    is_signed=False,
                    scale=1,
                    offset=0,
                    minimum=0,
                    maximum=1,
                    unit='',
                    receivers=['ZC']
                ),
                cantools.database.can.Signal(
                    name='brakeLamp',
                    start=2,
                    length=1,
                    is_signed=False,
                    scale=1,
                    offset=0,
                    minimum=0,
                    maximum=1,
                    unit='',
                    receivers=['ZC']
                ),
                cantools.database.can.Signal(
                    name='indicatorLeft',
                    start=3,
                    length=1,
                    is_signed=False,
                    scale=1,
                    offset=0,
                    minimum=0,
                    maximum=1,
                    unit='',
                    receivers=['ZC']
                ),
                cantools.database.can.Signal(
                    name='indicatorRight',
                    start=4,
                    length=1,
                    is_signed=False,
                    scale=1,
                    offset=0,
                    minimum=0,
                    maximum=1,
                    unit='',
                    receivers=['ZC']
                ),
                cantools.database.can.Signal(
                    name='lightLevel',
                    start=8,
                    length=8,
                    is_signed=False,
                    scale=1,
                    offset=0,
                    minimum=0,
                    maximum=255,
                    unit='%',
                    receivers=['ZC']
                ),
                cantools.database.can.Signal(
                    name='vehicleSpeed',
                    start=16,
                    length=8,
                    is_signed=False,
                    scale=1,
                    offset=0,
                    minimum=0,
                    maximum=255,
                    unit='km/h',
                    receivers=['ZC']
                ),
            ]
        )
        
        self.db.messages.append(light_control_msg)
        logger.info("Created virtual database with LIGHT_CONTROL message (0x100)")
    
    def decode_message(self, can_id: int, data: bytes) -> Optional[CANMessage]:
        """Decode CAN message sử dụng DBC"""
        try:
            # Đảm bảo data đủ 8 bytes
            if len(data) < 8:
                data = data + bytes(8 - len(data))
            
            # Tìm message definition
            if can_id not in self.message_cache:
                for msg in self.db.messages:
                    if msg.frame_id == can_id:
                        self.message_cache[can_id] = msg
                        break
            
            if can_id not in self.message_cache:
                logger.warning(f"No DBC definition for CAN ID 0x{can_id:03X}")
                return None
            
            msg_def = self.message_cache[can_id]
            
            try:
                # Decode signals
                decoded_signals = self.db.decode_message(can_id, data)
            except Exception as decode_error:
                logger.warning(f"Standard decode failed for 0x{can_id:03X}: {decode_error}")
                # Thử decode thủ công
                decoded_signals = self.manual_decode(can_id, data, msg_def)
            
            # Tạo CANSignal objects
            signals = []
            for signal_name, signal_value in decoded_signals.items():
                # Tìm signal definition
                signal_def = None
                for s in msg_def.signals:
                    if s.name == signal_name:
                        signal_def = s
                        break
                
                if signal_def:
                    signal = CANSignal(
                        name=signal_name,
                        value=signal_value,
                        unit=signal_def.unit or "",
                        min_value=signal_def.minimum or 0.0,
                        max_value=signal_def.maximum or 0.0,
                        description=getattr(signal_def, 'comment', '') or ""
                    )
                    signals.append(signal)
            
            # Tạo CANMessage
            can_msg = CANMessage(
                timestamp=time.time(),
                can_id=can_id,
                can_id_hex=f"0x{can_id:03X}",
                signals=signals,
                raw_data=data,
                dlc=len(data),
                can_id_description=msg_def.name,
                source="CAN_Bus"
            )
            
            return can_msg
            
        except Exception as e:
            logger.error(f"Failed to decode message 0x{can_id:03X}: {e}")
            return None
    
    def manual_decode(self, can_id: int, data: bytes, msg_def) -> Dict[str, Any]:
        """Decode thủ công nếu cantools không decode được"""
        decoded = {}
        
        if can_id == 0x100:
            # Manual decode cho light control message
            if len(data) >= 1:
                byte0 = data[0]
                decoded['headLamp'] = bool(byte0 & 0x01)
                decoded['tailLamp'] = bool(byte0 & 0x02)
                decoded['brakeLamp'] = bool(byte0 & 0x04)
                decoded['indicatorLeft'] = bool(byte0 & 0x08)
                decoded['indicatorRight'] = bool(byte0 & 0x10)
            
            if len(data) >= 2:
                decoded['lightLevel'] = data[1]
            
            if len(data) >= 3:
                decoded['vehicleSpeed'] = data[2]
        
        return decoded

class VSSMapper:
    """Map CAN signals sang VSS paths"""
    
    def __init__(self, mapping_config: str = None):
        self.mapping_config = mapping_config
        self.mappings = self.load_mappings()
        
    def load_mappings(self) -> Dict[str, Dict[str, Any]]:
        """Load VSS mapping configuration"""
        # Default mappings - đặc biệt cho CAN ID 0x100
        default_mappings = {
            0x100: {
                'headLamp': {
                    'vss_path': 'Vehicle.Body.Lights.IsHighBeamOn',
                    'data_type': 'boolean',
                    'description': 'High beam headlight status',
                    'conversion': None
                },
                'tailLamp': {
                    'vss_path': 'Vehicle.Body.Lights.IsTailLightOn',
                    'data_type': 'boolean',
                    'description': 'Tail light status'
                },
                'brakeLamp': {
                    'vss_path': 'Vehicle.Body.Lights.IsBrakeLightOn',
                    'data_type': 'boolean',
                    'description': 'Brake light status'
                },
                'indicatorLeft': {
                    'vss_path': 'Vehicle.Body.Lights.IsLeftIndicatorOn',
                    'data_type': 'boolean',
                    'description': 'Left turn indicator status'
                },
                'indicatorRight': {
                    'vss_path': 'Vehicle.Body.Lights.IsRightIndicatorOn',
                    'data_type': 'boolean',
                    'description': 'Right turn indicator status'
                },
                'lightLevel': {
                    'vss_path': 'Vehicle.Body.Lights.AmbientLight',
                    'data_type': 'uint8',
                    'description': 'Ambient light level',
                    'conversion': {'scale': 1.0, 'offset': 0.0}
                },
                'vehicleSpeed': {
                    'vss_path': 'Vehicle.Speed',
                    'data_type': 'uint8',
                    'description': 'Vehicle speed',
                    'conversion': {'scale': 1.0, 'offset': 0.0},
                    'unit': 'km/h'
                }
            }
        }
        
        # Thử load từ file config nếu có
        if self.mapping_config and Path(self.mapping_config).exists():
            try:
                with open(self.mapping_config, 'r') as f:
                    file_mappings = yaml.safe_load(f)
                    if file_mappings:
                        default_mappings.update(file_mappings)
                        logger.info(f"Loaded VSS mappings from {self.mapping_config}")
            except Exception as e:
                logger.warning(f"Cannot load mapping config: {e}")
        
        logger.info(f"Loaded {len(default_mappings)} VSS mappings")
        return default_mappings
    
    def map_can_to_vss(self, can_msg: CANMessage) -> List[VSSSignal]:
        """Map CAN message sang VSS signals"""
        vss_signals = []
        can_id = can_msg.can_id
        
        # Kiểm tra nếu có mapping cho CAN ID này
        if can_id not in self.mappings:
            logger.debug(f"No VSS mapping for CAN ID 0x{can_id:03X}")
            return vss_signals
        
        mapping = self.mappings[can_id]
        
        # Map từng signal
        for can_signal in can_msg.signals:
            signal_name = can_signal.name
            
            if signal_name in mapping:
                vss_config = mapping[signal_name]
                
                # Áp dụng conversion nếu có
                value = can_signal.value
                if vss_config.get('conversion'):
                    conv = vss_config['conversion']
                    value = value * conv.get('scale', 1.0) + conv.get('offset', 0.0)
                
                # Tạo VSS signal
                vss_signal = VSSSignal(
                    path=vss_config['vss_path'],
                    value=value,
                    timestamp=can_msg.timestamp,
                    data_type=vss_config.get('data_type', 'unknown'),
                    min_value=can_signal.min_value,
                    max_value=can_signal.max_value,
                    unit=can_signal.unit or vss_config.get('unit', ''),
                    description=vss_config.get('description', '')
                )
                
                vss_signals.append(vss_signal)
                
                logger.debug(f"Mapped {signal_name} -> {vss_signal.path} = {value}")
        
        return vss_signals

class DatabaseManager:
    """Quản lý lưu trữ database"""
    
    def __init__(self, db_path: str = "sdv_can_data.db"):
        self.db_path = db_path
        self.connection = None
        self.setup_database()
    
    def setup_database(self):
        """Thiết lập database schema"""
        try:
            self.connection = sqlite3.connect(self.db_path)
            cursor = self.connection.cursor()
            
            # Tạo bảng CAN messages
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS can_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    can_id INTEGER,
                    can_id_hex TEXT,
                    can_id_description TEXT,
                    raw_data BLOB,
                    dlc INTEGER,
                    direction TEXT,
                    source TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tạo bảng CAN signals
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS can_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER,
                    signal_name TEXT,
                    signal_value REAL,
                    signal_unit TEXT,
                    min_value REAL,
                    max_value REAL,
                    description TEXT,
                    FOREIGN KEY (message_id) REFERENCES can_messages (id)
                )
            ''')
            
            # Tạo bảng VSS signals
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vss_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    vss_path TEXT,
                    vss_value REAL,
                    data_type TEXT,
                    unit TEXT,
                    description TEXT,
                    source_can_id INTEGER,
                    source_signal_name TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tạo indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_can_messages_timestamp ON can_messages(timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_can_messages_can_id ON can_messages(can_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_vss_signals_path ON vss_signals(vss_path)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_vss_signals_timestamp ON vss_signals(timestamp)')
            
            self.connection.commit()
            logger.info(f"Database setup complete: {self.db_path}")
            
        except Exception as e:
            logger.error(f"Database setup failed: {e}")
            raise
    
    def save_can_message(self, can_msg: CANMessage) -> Optional[int]:
        """Lưu CAN message vào database"""
        try:
            cursor = self.connection.cursor()
            
            # Lưu CAN message
            cursor.execute('''
                INSERT INTO can_messages 
                (timestamp, can_id, can_id_hex, can_id_description, raw_data, dlc, direction, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                can_msg.timestamp,
                can_msg.can_id,
                can_msg.can_id_hex,
                can_msg.can_id_description,
                can_msg.raw_data,
                can_msg.dlc,
                can_msg.direction,
                can_msg.source
            ))
            
            message_id = cursor.lastrowid
            
            # Lưu các signals
            for signal in can_msg.signals:
                cursor.execute('''
                    INSERT INTO can_signals 
                    (message_id, signal_name, signal_value, signal_unit, min_value, max_value, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    message_id,
                    signal.name,
                    signal.value,
                    signal.unit,
                    signal.min_value,
                    signal.max_value,
                    signal.description
                ))
            
            self.connection.commit()
            return message_id
            
        except Exception as e:
            logger.error(f"Failed to save CAN message: {e}")
            self.connection.rollback()
            return None
    
    def save_vss_signals(self, vss_signals: List[VSSSignal], can_id: int, source_signal: str = ""):
        """Lưu VSS signals vào database"""
        try:
            cursor = self.connection.cursor()
            
            for vss_signal in vss_signals:
                cursor.execute('''
                    INSERT INTO vss_signals 
                    (timestamp, vss_path, vss_value, data_type, unit, description, source_can_id, source_signal_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    vss_signal.timestamp,
                    vss_signal.path,
                    vss_signal.value,
                    vss_signal.data_type,
                    vss_signal.unit,
                    vss_signal.description,
                    can_id,
                    source_signal
                ))
            
            self.connection.commit()
            
        except Exception as e:
            logger.error(f"Failed to save VSS signals: {e}")
            self.connection.rollback()
    
    def get_message_count(self) -> int:
        """Lấy số lượng messages trong database"""
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT COUNT(*) FROM can_messages')
            return cursor.fetchone()[0]
        except:
            return 0
    
    def close(self):
        """Đóng database connection"""
        if self.connection:
            self.connection.close()

class ZonalController:
    """Main Zonal Controller class"""
    
    def __init__(self, dbc_path: str = "lights.dbc", zmq_host: str = "localhost", zmq_port: int = 5555,
                 can_interface: str = "vcan0", vss_mapping: str = None):
        self.dbc_path = dbc_path
        self.zmq_host = zmq_host
        self.zmq_port = zmq_port
        self.can_interface = can_interface
        
        # Initialize components
        self.dbc_manager = DBCManager(dbc_path)
        self.vss_mapper = VSSMapper(vss_mapping)
        self.db_manager = DatabaseManager()
        
        # ZMQ setup
        self.context = zmq.Context()
        self.subscriber = self.context.socket(zmq.SUB)
        
        # CAN bus setup
        self.can_bus = None
        self.setup_can_bus()
        
        # State
        self.running = False
        
        # Statistics
        self.stats = {
            'messages_received': 0,
            'messages_decoded': 0,
            'vss_signals_mapped': 0,
            'errors': 0,
            'start_time': time.time()
        }
        
    def setup_can_bus(self):
        """Thiết lập CAN bus connection"""
        try:
            self.can_bus = can.interface.Bus(
                channel=self.can_interface,
                bustype='socketcan',
                receive_own_messages=False
            )
            logger.info(f"Connected to CAN interface: {self.can_interface}")
        except Exception as e:
            logger.warning(f"Cannot connect to CAN bus: {e}. ZMQ only mode.")
            self.can_bus = None
    
    def connect_to_zmq(self):
        """Kết nối đến ZMQ publisher"""
        try:
            zmq_address = f"tcp://{self.zmq_host}:{self.zmq_port}"
            self.subscriber.connect(zmq_address)
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, "")  # Subscribe to all
            logger.info(f"Connected to ZMQ publisher at {zmq_address}")
            return True
        except Exception as e:
            logger.error(f"Cannot connect to ZMQ: {e}")
            return False
    
    def process_zmq_message(self, data: Dict[str, Any]):
        """Xử lý message từ ZMQ"""
        try:
            self.stats['messages_received'] += 1
            
            # Extract CAN data
            can_id = data.get('can_id')
            can_data = data.get('can_data')
            
            if can_id is None or can_data is None:
                logger.warning("Invalid message format")
                return
            
            # Chuyển đổi data thành bytes
            if isinstance(can_data, list):
                can_data_bytes = bytes(can_data)
            else:
                logger.error(f"Invalid can_data format: {type(can_data)}")
                return
            
            # Decode sử dụng DBC
            can_msg = self.dbc_manager.decode_message(can_id, can_data_bytes)
            if not can_msg:
                logger.debug(f"Could not decode CAN ID 0x{can_id:03X}")
                return
            
            self.stats['messages_decoded'] += 1
            
            # Lưu CAN message vào database
            message_id = self.db_manager.save_can_message(can_msg)
            
            # Map sang VSS
            vss_signals = self.vss_mapper.map_can_to_vss(can_msg)
            
            if vss_signals:
                self.stats['vss_signals_mapped'] += len(vss_signals)
                
                # Lưu VSS signals vào database
                self.db_manager.save_vss_signals(
                    vss_signals, 
                    can_id, 
                    ", ".join([s.name for s in can_msg.signals])
                )
                
                # Hiển thị thông tin (mỗi 10 messages)
                if self.stats['messages_decoded'] % 10 == 0:
                    self.log_processing_info(can_msg, vss_signals)
            
        except Exception as e:
            logger.error(f"Error processing ZMQ message: {e}")
            self.stats['errors'] += 1
    
    def process_can_message(self, can_msg: can.Message):
        """Xử lý CAN message trực tiếp từ bus (nếu cần)"""
        try:
            # Decode sử dụng DBC
            decoded_msg = self.dbc_manager.decode_message(can_msg.arbitration_id, can_msg.data)
            if decoded_msg:
                # Cập nhật source
                decoded_msg.source = "CAN_Bus_Direct"
                decoded_msg.direction = "RX"
                
                # Lưu vào database
                self.db_manager.save_can_message(decoded_msg)
                
                # Map sang VSS
                vss_signals = self.vss_mapper.map_can_to_vss(decoded_msg)
                if vss_signals:
                    self.db_manager.save_vss_signals(vss_signals, can_msg.arbitration_id)
                    
        except Exception as e:
            logger.error(f"Error processing CAN message: {e}")
    
    def log_processing_info(self, can_msg: CANMessage, vss_signals: List[VSSSignal]):
        """Log thông tin xử lý"""
        try:
            logger.info("-" * 60)
            logger.info(f"Decoded: CAN ID {can_msg.can_id_hex} ({can_msg.can_id_description})")
            
            # Tìm headLamp signal
            headlamp_value = None
            for signal in can_msg.signals:
                if signal.name == 'headLamp':
                    headlamp_value = signal.value
                    logger.info(f"  {signal.name}: {'ON' if signal.value else 'OFF'}")
                # else:
                #     logger.info(f"  {signal.name}: {signal.value} {signal.unit}")
            
            for vss_signal in vss_signals:
                if 'IsHighBeamOn' in vss_signal.path:
                    status = "ON" if vss_signal.value else "OFF"
                    logger.info(f"  → {vss_signal.path}: {status}")
                # else:
                #     logger.info(f"  → {vss_signal.path}: {vss_signal.value}")
            
        except Exception as e:
            logger.error(f"Error logging info: {e}")
    
    def monitor_loop(self):
        """Vòng lặp giám sát và hiển thị statistics"""
        while self.running:
            time.sleep(30)  # Cập nhật mỗi 30 giây
            
            elapsed = time.time() - self.stats['start_time']
            if elapsed > 0:
                msg_rate = self.stats['messages_received'] / elapsed
                decode_rate = self.stats['messages_decoded'] / elapsed
                
                # logger.info(f"\nSTATS: Received: {self.stats['messages_received']}, "
                #           f"Decoded: {self.stats['messages_decoded']}, "
                #           f"VSS Signals: {self.stats['vss_signals_mapped']}, "
                #           f"Rate: {msg_rate:.1f}/s, "
                #           f"Errors: {self.stats['errors']}")
                
                # Database stats
                db_count = self.db_manager.get_message_count()
                # logger.info(f"Database: {db_count} messages stored")
    
    def run(self):
        """Chạy zonal controller"""
        logger.info("Starting Zonal Controller...")
        logger.info(f"DBC File: {self.dbc_path}")
        logger.info(f"ZMQ: {self.zmq_host}:{self.zmq_port}")
        logger.info(f"CAN Interface: {self.can_interface}")
        
        # Kết nối đến ZMQ
        if not self.connect_to_zmq():
            logger.error("❌ Cannot connect to ZMQ, exiting...")
            return
        
        self.running = True
        
        # Start monitoring thread
        monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        monitor_thread.start()
        
        # Poller cho cả ZMQ và CAN
        # poller = zmq.Poller()
        # poller.register(self.subscriber, zmq.POLLIN)
        
        logger.info("Zonal Controller ready. Processing messages...")
        
        try:
            while self.running:
                try:
                    # Poll với timeout
                    # socks = dict(poller.poll(timeout=1000))  # 1 second timeout
                    
                    # if self.subscriber in socks:
                    #     # Nhận message từ ZMQ
                    #     message = self.subscriber.recv_json()
                    #     self.process_zmq_message(message)
                    
                    # Cũng có thể đọc trực tiếp từ CAN bus nếu cần
                    if self.can_bus:
                        can_msg = self.can_bus.recv(timeout=0.1)  # Non-blocking
                        if can_msg:
                            self.process_can_message(can_msg)
                    
                except zmq.ZMQError as e:
                    logger.error(f"ZMQ error: {e}")
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"Processing error: {e}")
                    time.sleep(1)
                    
        except KeyboardInterrupt:
            logger.info("Zonal Controller stopped by user")
        except Exception as e:
            logger.error(f"Zonal Controller error: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """Dừng controller"""
        self.running = False
        
        # Cleanup
        if hasattr(self, 'subscriber') and self.subscriber:
            self.subscriber.close()
        
        if hasattr(self, 'context') and self.context:
            self.context.term()
        
        if hasattr(self, 'can_bus') and self.can_bus:
            self.can_bus.shutdown()
        
        if hasattr(self, 'db_manager') and self.db_manager:
            self.db_manager.close()
        
        # Print final statistics
        self.print_statistics()
    
    def print_statistics(self):
        """In thống kê cuối cùng"""
        elapsed = time.time() - self.stats['start_time']
        
        logger.info("\n" + "="*60)
        logger.info("ZONAL CONTROLLER FINAL STATISTICS")
        logger.info("="*60)
        logger.info(f"Runtime:              {elapsed:.1f} seconds")
        logger.info(f"Messages received:    {self.stats['messages_received']}")
        logger.info(f"Messages decoded:     {self.stats['messages_decoded']}")
        logger.info(f"VSS signals mapped:   {self.stats['vss_signals_mapped']}")
        logger.info(f"Errors:               {self.stats['errors']}")
        
        if elapsed > 0:
            logger.info(f"Average rate:         {self.stats['messages_received']/elapsed:.1f} msg/s")
        
        logger.info("="*60)

def create_sample_files():
    """Tạo các file cấu hình mẫu nếu chưa có"""
    
    # Tạo thư mục config nếu chưa có
    config_dir = Path("config")
    config_dir.mkdir(exist_ok=True)
    
    # Tạo file VSS mapping mẫu
    vss_mapping_content = """
# VSS Mapping Configuration
# CAN ID -> Signal Name -> VSS Path mapping

# Light Control Messages (0x100)
0x100:
  headLamp:
    vss_path: "Vehicle.Body.Lights.IsHighBeamOn"
    data_type: "boolean"
    description: "High beam headlight status"
    
  tailLamp:
    vss_path: "Vehicle.Body.Lights.IsTailLightOn"
    data_type: "boolean"
    description: "Tail light status"
    
  brakeLamp:
    vss_path: "Vehicle.Body.Lights.IsBrakeLightOn"
    data_type: "boolean"
    description: "Brake light status"
    
  indicatorLeft:
    vss_path: "Vehicle.Body.Lights.IsLeftIndicatorOn"
    data_type: "boolean"
    description: "Left turn indicator status"
    
  indicatorRight:
    vss_path: "Vehicle.Body.Lights.IsRightIndicatorOn"
    data_type: "boolean"
    description: "Right turn indicator status"
    
  lightLevel:
    vss_path: "Vehicle.Body.Lights.AmbientLight"
    data_type: "uint8"
    description: "Ambient light level"
    conversion:
      scale: 1.0
      offset: 0.0
    
  vehicleSpeed:
    vss_path: "Vehicle.Speed"
    data_type: "uint8"
    description: "Vehicle speed"
    conversion:
      scale: 1.0
      offset: 0.0
"""
    
    vss_mapping_path = config_dir / "vss_mapping.yaml"
    if not vss_mapping_path.exists():
        with open(vss_mapping_path, 'w') as f:
            f.write(vss_mapping_content)
        logger.info(f"Created sample VSS mapping file: {vss_mapping_path}")
    
    # Tạo thư mục logs nếu chưa có
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    return str(vss_mapping_path)

def main():
    """Hàm chính"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Zonal Controller with DBC decoding and VSS mapping')
    parser.add_argument('--dbc', type=str, default="lights.dbc",
                       help='Path to DBC file')
    parser.add_argument('--zmq-host', type=str, default="localhost",
                       help='ZMQ publisher host')
    parser.add_argument('--zmq-port', type=int, default=5555,
                       help='ZMQ publisher port')
    parser.add_argument('--can-interface', type=str, default="vcan0",
                       help='CAN interface name')
    parser.add_argument('--vss-mapping', type=str, default=None,
                       help='Path to VSS mapping YAML file')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Cấu hình logging
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    # Tạo file cấu hình mẫu nếu cần
    if args.vss_mapping is None:
        args.vss_mapping = create_sample_files()
    
    # Khởi chạy controller
    try:
        controller = ZonalController(
            dbc_path=args.dbc,
            zmq_host=args.zmq_host,
            zmq_port=args.zmq_port,
            can_interface=args.can_interface,
            vss_mapping=args.vss_mapping
        )
        
        controller.run()
        
    except Exception as e:
        logger.error(f"Failed to start controller: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()