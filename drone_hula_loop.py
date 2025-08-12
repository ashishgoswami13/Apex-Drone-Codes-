import socket
import json
import struct
import time
import threading
import queue

# --- Configuration ---
HOST = '192.168.1.1'
PORT = 3333

# --- Global State ---
client_socket = None
is_running = threading.Event()
is_running.set()
latest_altitude_cm = 0.0
latest_battery_percent = 0
active_command_packet = None
latest_raw_data = "Waiting for data..."

# --- Command Creation (based on official 14-byte protocol) ---
def create_wifi_command(throttle=128, yaw=128, pitch=128, roll=128, func1=0):
    """Creates the JSON command structure for WiFi control."""
    payload = [throttle, yaw, 0, pitch, roll, 64, 64, func1, 0, 0, 0]
    checksum = 255 - (sum(payload) & 255)
    
    command = {
        "op": "PUT",
        "param": {
            "D0": "204", "D1": str(throttle), "D2": str(yaw), "D3": "0",
            "D4": str(pitch), "D5": str(roll), "D6": "64", "D7": "64",
            "D8": str(func1), "D9": "0", "D10": "0", "D11": "0",
            "D12": str(checksum), "D13": "51"
        }
    }
    return json.dumps(command)

def create_packet(topic, content):
    """Creates the final CTP packet to be sent over the socket."""
    header = b"CTP:"
    topic_bytes = topic.encode('utf-8')
    content_bytes = content.encode('utf-8')
    packet = bytearray()
    packet.extend(header)
    packet.extend(struct.pack('<h', len(topic_bytes)))
    packet.extend(topic_bytes)
    packet.extend(struct.pack('<i', len(content_bytes)))
    packet.extend(content_bytes)
    return bytes(packet)

# --- Command Definitions ---
CMD_STOP_JSON = create_wifi_command()
CMD_TAKEOFF_JSON = create_wifi_command(func1=8)
CMD_LAND_JSON = create_wifi_command(func1=8)
CMD_ASCEND_JSON = create_wifi_command(throttle=160)
CMD_DESCEND_JSON = create_wifi_command(throttle=112)
CMD_FORWARD_JSON = create_wifi_command(pitch=255)
CMD_BACKWARD_JSON = create_wifi_command(pitch=0)
CMD_LEFT_JSON = create_wifi_command(roll=0)
CMD_RIGHT_JSON = create_wifi_command(roll=255)

PACKET_STOP = create_packet("GENERIC_CMD", CMD_STOP_JSON)
PACKET_TAKEOFF = create_packet("GENERIC_CMD", CMD_TAKEOFF_JSON)
PACKET_LAND = create_packet("GENERIC_CMD", CMD_LAND_JSON)
PACKET_ASCEND = create_packet("GENERIC_CMD", CMD_ASCEND_JSON)
PACKET_DESCEND = create_packet("GENERIC_CMD", CMD_DESCEND_JSON)
PACKET_FORWARD = create_packet("GENERIC_CMD", CMD_FORWARD_JSON)
PACKET_BACKWARD = create_packet("GENERIC_CMD", CMD_BACKWARD_JSON)
PACKET_LEFT = create_packet("GENERIC_CMD", CMD_LEFT_JSON)
PACKET_RIGHT = create_packet("GENERIC_CMD", CMD_RIGHT_JSON)

# --- Network Communication ---
def send_packet(packet):
    """Sends a packet to the drone."""
    global client_socket
    if client_socket:
        try:
            client_socket.sendall(packet)
            return True
        except (socket.error, BrokenPipeError):
            print("Connection lost. Please restart the script.")
            is_running.clear()
            return False
    return False

def connect():
    """Connects to the drone."""
    global client_socket
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(1.0) 
        client_socket.connect((HOST, PORT))
        print("✅ Drone connected successfully via WiFi.")
        return True
    except socket.error as e:
        print(f"❌ Failed to connect to drone: {e}")
        client_socket = None
        return False

# --- Background Threads ---
def command_loop_thread():
    """Sends a continuous stream of commands to keep the connection alive."""
    global active_command_packet
    active_command_packet = PACKET_STOP
    while is_running.is_set():
        if not command_queue.empty():
            active_command_packet = command_queue.get()
        send_packet(active_command_packet)
        time.sleep(0.1)

def data_receive_thread():
    """Listens for and parses incoming CTP/JSON data packets from the drone."""
    global latest_altitude_cm, latest_battery_percent, latest_raw_data
    buffer = bytearray()
    while is_running.is_set():
        if not client_socket:
            time.sleep(1)
            continue
        try:
            data = client_socket.recv(1024)
            if not data:
                time.sleep(0.5)
                continue

            buffer.extend(data)

            while len(buffer) >= 4 and buffer.startswith(b'CTP:'):
                if len(buffer) < 6: break
                topic_len = struct.unpack('<h', buffer[4:6])[0]
                
                if len(buffer) < 10 + topic_len: break
                json_len = struct.unpack('<i', buffer[6 + topic_len : 10 + topic_len])[0]

                if len(buffer) < 10 + topic_len + json_len: break

                packet_end = 10 + topic_len + json_len
                packet_data = buffer[:packet_end]
                buffer = buffer[packet_end:]

                json_string = packet_data[10 + topic_len:].decode('utf-8', errors='ignore')
                latest_raw_data = json_string
                
                msg = json.loads(json_string)

                if msg.get("op") == "NOTIFY" and "param" in msg:
                    params = msg["param"]
                    
                    if "D8" in params and "D9" in params and "D10" in params:
                        low_byte = int(params.get("D8", 0))
                        high_byte = int(params.get("D9", 0))
                        
                        altitude_mm = struct.unpack('<h', bytes([low_byte, high_byte]))[0]
                        latest_altitude_cm = altitude_mm / 10.0
                        
                        latest_battery_percent = int(params.get("D10", 0))

        except socket.timeout:
            continue
        except (socket.error, IndexError, json.JSONDecodeError, struct.error) as e:
            print(f"Data receive error: {e}. Resetting buffer.")
            buffer = bytearray()
            time.sleep(1)

# --- NEW: Automated Hula Hoop Sequence ---
def run_hula_hoop_sequence():
    """Performs the automated flight path for the two hula hoops."""
    print("\n" + "="*40)
    print("🚀 STARTING HULA HOOP SEQUENCE")
    print("="*40)
    
    # 1. Take Off
    print("Step 1: Taking Off...")
    for _ in range(5): send_packet(PACKET_TAKEOFF); time.sleep(0.1)
    command_queue.put(PACKET_STOP)
    print("Stabilizing for 3 seconds...")
    time.sleep(3.0)
    
    # 2. Ascend for 3s
    print("Step 2: Ascending for 3 seconds...")
    command_queue.put(PACKET_ASCEND)
    time.sleep(3.0)
    command_queue.put(PACKET_STOP)
    time.sleep(1.0)
    
    # 3. Fly forward for 2s
    print("Step 3: Flying forward for 2 seconds...")
    command_queue.put(PACKET_FORWARD)
    time.sleep(5.0)
    command_queue.put(PACKET_STOP)
    time.sleep(1.0)

    # 4. Descend for 1.5s
    print("Step 4: Descending for 1.5 seconds...")
    command_queue.put(PACKET_DESCEND)
    time.sleep(1.0)
    command_queue.put(PACKET_STOP)
    time.sleep(1.0)

    # 5. Go forward for 2s
    print("Step 5: Flying forward again for 2 seconds...")
    command_queue.put(PACKET_FORWARD)
    time.sleep(1.0)
    command_queue.put(PACKET_STOP)
    time.sleep(1.0)
    
    # 6. Hover for 2s
    print("Step 6: Hovering for 2 seconds...")
    time.sleep(2.0)

    # 7. Land
    print("Step 7: Sequence complete. Landing...")
    for _ in range(5): send_packet(PACKET_LAND); time.sleep(0.1)
    
    print("\n--- ✅ HULA HOOP SEQUENCE COMPLETE ---")

# --- Main Control Logic ---
if __name__ == "__main__":
    command_queue = queue.Queue()

    print("--- Drone WiFi Controller ---")
    print("Please ensure you are connected to the drone's WiFi network.")
    
    if connect():
        is_running.set()
        command_thread = threading.Thread(target=command_loop_thread, daemon=True)
        receiver_thread = threading.Thread(target=data_receive_thread, daemon=True)
        command_thread.start()
        receiver_thread.start()

        try:
            while True:
                print("\n" + "="*60)
                print(f" Altitude: {latest_altitude_cm:6.1f} cm | Battery: {latest_battery_percent}%")
                print(f" Raw Data: {latest_raw_data}")
                print("="*60)
                print("--- Drone Control Menu ---")
                print("h. Run Automated Hula Hoop Sequence")
                print("--- Manual Controls ---")
                print("1. Take Off | 2. Land")
                print("w. Ascend   | s. Descend")
                print("f. Forward  | b. Backward")
                print("a. Left     | d. Right")
                print("x. Stop (Hover)")
                print("q. Quit")
                
                choice = input("Enter command: ").strip().lower()

                if choice == 'h':
                    run_hula_hoop_sequence()
                elif choice == '1':
                    print("📤 Sending Take Off command...")
                    for _ in range(5): send_packet(PACKET_TAKEOFF); time.sleep(0.1)
                    command_queue.put(PACKET_STOP)
                elif choice == '2':
                    print("📤 Sending Land command...")
                    for _ in range(5): send_packet(PACKET_LAND); time.sleep(0.1)
                    command_queue.put(PACKET_STOP)
                elif choice == 'w': command_queue.put(PACKET_ASCEND)
                elif choice == 's': command_queue.put(PACKET_DESCEND)
                elif choice == 'f': command_queue.put(PACKET_FORWARD)
                elif choice == 'b': command_queue.put(PACKET_BACKWARD)
                elif choice == 'a': command_queue.put(PACKET_LEFT)
                elif choice == 'd': command_queue.put(PACKET_RIGHT)
                elif choice == 'x': command_queue.put(PACKET_STOP)
                elif choice == 'q':
                    print("🛑 Landing and quitting...")
                    for _ in range(5): send_packet(PACKET_LAND); time.sleep(0.1)
                    break
                else:
                    print("❌ Invalid command.")

        except KeyboardInterrupt:
            print("\n🛑 Program interrupted. Landing...")
            for _ in range(5): send_packet(PACKET_LAND); time.sleep(0.1)
        finally:
            is_running.clear()
            if client_socket:
                client_socket.close()
            print("🔌 Disconnected.")