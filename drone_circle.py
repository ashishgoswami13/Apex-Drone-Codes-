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
def create_wifi_command(throttle=128, yaw=128, pitch=128, roll=128, func1=0, func_byte3=0):
    """Creates the JSON command structure for WiFi control."""
    payload = [throttle, yaw, func_byte3, pitch, roll, 64, 64, func1, 0, 0, 0]
    checksum = 255 - (sum(payload) & 255)
    
    command = {
        "op": "PUT",
        "param": {
            "D0": "204", "D1": str(throttle), "D2": str(yaw), "D3": str(func_byte3),
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
CMD_YAW_LEFT_JSON = create_wifi_command(yaw=0)
CMD_YAW_RIGHT_JSON = create_wifi_command(yaw=255)
CMD_GYRO_CAL_JSON = create_wifi_command(func_byte3=1)
# NEW: Circle command combines forward pitch and right yaw
CMD_CIRCLE_JSON = create_wifi_command(pitch=255, yaw=255)


PACKET_STOP = create_packet("GENERIC_CMD", CMD_STOP_JSON)
PACKET_TAKEOFF = create_packet("GENERIC_CMD", CMD_TAKEOFF_JSON)
PACKET_LAND = create_packet("GENERIC_CMD", CMD_LAND_JSON)
PACKET_ASCEND = create_packet("GENERIC_CMD", CMD_ASCEND_JSON)
PACKET_DESCEND = create_packet("GENERIC_CMD", CMD_DESCEND_JSON)
PACKET_FORWARD = create_packet("GENERIC_CMD", CMD_FORWARD_JSON)
PACKET_BACKWARD = create_packet("GENERIC_CMD", CMD_BACKWARD_JSON)
PACKET_LEFT = create_packet("GENERIC_CMD", CMD_LEFT_JSON)
PACKET_RIGHT = create_packet("GENERIC_CMD", CMD_RIGHT_JSON)
PACKET_YAW_LEFT = create_packet("GENERIC_CMD", CMD_YAW_LEFT_JSON)
PACKET_YAW_RIGHT = create_packet("GENERIC_CMD", CMD_YAW_RIGHT_JSON)
PACKET_GYRO_CAL = create_packet("GENERIC_CMD", CMD_GYRO_CAL_JSON)
# NEW: Circle packet
PACKET_CIRCLE = create_packet("GENERIC_CMD", CMD_CIRCLE_JSON)


# --- Network Communication ---
def send_packet(packet):
    """Sends a packet to the drone."""
    global client_socket
    if client_socket and is_running.is_set():
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
        except (socket.error, IndexError, json.JSONDecodeError, struct.error):
            buffer = bytearray()
            time.sleep(1)

# --- Safe Shutdown Function ---
def safe_land_and_exit():
    """Stops all movement, lands the drone ONLY IF it's flying, and signals threads to close."""
    print("🛑 Initiating safe shutdown...")
    command_queue.put(PACKET_STOP)
    time.sleep(0.5) 

    if latest_altitude_cm > 5.0:
        print("    Drone is airborne. Sending land command...")
        for _ in range(5):
            send_packet(PACKET_LAND)
            time.sleep(0.1)
    else:
        print("    Drone is on the ground. Skipping land command.")
        
    is_running.clear()

# --- Automated Sequences ---
def run_rectangle_sequence():
    """Performs an automated flight path in the shape of a rectangle."""
    print("\n" + "="*40)
    print("🚀 STARTING RECTANGLE SEQUENCE")
    print("="*40)
    
    print("Step 1: Taking Off...");
    for _ in range(5): send_packet(PACKET_TAKEOFF); time.sleep(0.1)
    command_queue.put(PACKET_STOP); print("Stabilizing for 3 seconds..."); time.sleep(3.0)
    
    print("Step 2: Flying forward for 2.0 seconds...");
    command_queue.put(PACKET_FORWARD); time.sleep(2.0); command_queue.put(PACKET_STOP); time.sleep(1.0)
    
    print("Step 3: Flying right for 1.5 seconds...");
    command_queue.put(PACKET_RIGHT); time.sleep(1.5); command_queue.put(PACKET_STOP); time.sleep(1.0)

    print("Step 4: Flying backward for 2.0 seconds...");
    command_queue.put(PACKET_BACKWARD); time.sleep(2.0); command_queue.put(PACKET_STOP); time.sleep(1.0)

    print("Step 5: Flying left for 1.5 seconds...");
    command_queue.put(PACKET_LEFT); time.sleep(1.5); command_queue.put(PACKET_STOP); time.sleep(1.0)

    print("Step 6: Sequence complete. Landing...");
    for _ in range(5): send_packet(PACKET_LAND); time.sleep(0.1)
    
    print("\n--- ✅ RECTANGLE SEQUENCE COMPLETE ---")

# --- NEW: Automated Circle Sequence ---
def run_circle_sequence():
    """Performs an automated flight path in the shape of a circle."""
    print("\n" + "="*40)
    print("🚀 STARTING CIRCLE SEQUENCE")
    print("="*40)

    # 1. Take Off
    print("Step 1: Taking Off...");
    for _ in range(5): send_packet(PACKET_TAKEOFF); time.sleep(0.1)
    command_queue.put(PACKET_STOP); print("Stabilizing for 3 seconds..."); time.sleep(3.0)

    # 2. Fly in a circle
    # The duration determines the size of the circle. 8 seconds is a good starting point.
    print("Step 2: Flying in a circle for 8.0 seconds...");
    command_queue.put(PACKET_CIRCLE)
    time.sleep(8.0) # <-- You can change this value to make the circle larger or smaller
    command_queue.put(PACKET_STOP)
    time.sleep(1.0)


    # 3. Land
    print("Step 3: Sequence complete. Landing...");
    for _ in range(5): send_packet(PACKET_LAND); time.sleep(0.1)

    print("\n--- ✅ CIRCLE SEQUENCE COMPLETE ---")

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

        print("\n" + "="*60)
        print("⚙️  Performing automatic Gyro Calibration...")
        print("    Please ensure the drone is on a flat, level surface.")
        for _ in range(5): send_packet(PACKET_GYRO_CAL); time.sleep(0.1)
        command_queue.put(PACKET_STOP)
        time.sleep(2)
        print("✅ Calibration complete.")
        print("="*60)

        try:
            while is_running.is_set():
                print("\n" + "="*60)
                print(f" Altitude: {latest_altitude_cm:6.1f} cm | Battery: {latest_battery_percent}%")
                print(f" Raw Data: {latest_raw_data}")
                print("="*60)
                print("--- Drone Control Menu ---")
                print(f"{'1. Take Off':<25} | {'2. Land':<25}")
                print(f"{'r. Run Rectangle Sequence':<25} | {'o. Run Circle Sequence':<25}")
                print("-" * 60)
                print("--- Movement (Hold Keys) ---")
                print(f"{'[w] Forward':<25} | {'[u] Ascend':<25}")
                print(f"{'[s] Backward':<25} | {'[j] Descend':<25}")
                print(f"{'[a] Left':<25} | {'[d] Right':<25}")
                print(f"{'[q] Yaw Left':<25} | {'[e] Yaw Right':<25}")
                print("-" * 60)
                print(f"{'x. Stop (Hover)':<25} | {'exit. Quit':<25}")
                print("="*60)
                
                choice = input("Enter command: ").strip().lower()

                if choice == 'r':
                    run_rectangle_sequence()
                elif choice == 'o':
                    run_circle_sequence()
                elif choice == '1':
                    print("📤 Sending Take Off command...")
                    for _ in range(5): send_packet(PACKET_TAKEOFF); time.sleep(0.1)
                    command_queue.put(PACKET_STOP)
                elif choice == '2':
                    print("📤 Sending Land command...")
                    for _ in range(5): send_packet(PACKET_LAND); time.sleep(0.1)
                    command_queue.put(PACKET_STOP)
                elif choice == 'u': command_queue.put(PACKET_ASCEND)
                elif choice == 'j': command_queue.put(PACKET_DESCEND)
                elif choice == 'w': command_queue.put(PACKET_FORWARD)
                elif choice == 's': command_queue.put(PACKET_BACKWARD)
                elif choice == 'a': command_queue.put(PACKET_LEFT)
                elif choice == 'd': command_queue.put(PACKET_RIGHT)
                elif choice == 'q': command_queue.put(PACKET_YAW_LEFT)
                elif choice == 'e': command_queue.put(PACKET_YAW_RIGHT)
                elif choice == 'x': command_queue.put(PACKET_STOP)
                elif choice == 'exit':
                    safe_land_and_exit()
                    break
                else:
                    print("❌ Invalid command.")

        except KeyboardInterrupt:
            safe_land_and_exit()
        finally:
            is_running.clear()
            if client_socket:
                client_socket.close()
            print("🔌 Disconnected.")
