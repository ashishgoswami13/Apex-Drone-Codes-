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
sequence_running = threading.Event()

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
# Using more distinct values for more noticeable movements
CMD_STOP_JSON = create_wifi_command()
CMD_TAKEOFF_JSON = create_wifi_command(func1=8)
CMD_LAND_JSON = create_wifi_command(func1=8)
CMD_ASCEND_JSON = create_wifi_command(throttle=200) # Strong ascend
CMD_DESCEND_JSON = create_wifi_command(throttle=80)  # Strong descend
CMD_FORWARD_JSON = create_wifi_command(pitch=200) 
CMD_BACKWARD_JSON = create_wifi_command(pitch=50) 
CMD_LEFT_JSON = create_wifi_command(roll=0)
CMD_RIGHT_JSON = create_wifi_command(roll=255)
CMD_YAW_LEFT_JSON = create_wifi_command(yaw=0)
CMD_YAW_RIGHT_JSON = create_wifi_command(yaw=255)
CMD_GYRO_CAL_JSON = create_wifi_command(func_byte3=1)


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


# --- Network Communication (unchanged) ---
def send_packet(packet):
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

# --- Background Threads (unchanged) ---
def command_loop_thread():
    global active_command_packet
    active_command_packet = PACKET_STOP
    while is_running.is_set():
        if sequence_running.is_set():
            time.sleep(0.02)
            continue
        if not command_queue.empty():
            active_command_packet = command_queue.get()
        send_packet(active_command_packet)
        time.sleep(0.05)

def data_receive_thread():
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
                if len(buffer) < 10: break
                topic_len = struct.unpack('<h', buffer[4:6])[0]
                if len(buffer) < 10 + topic_len: break
                json_len_offset = 6 + topic_len
                json_len = struct.unpack('<i', buffer[json_len_offset : json_len_offset + 4])[0]
                packet_end = json_len_offset + 4 + json_len
                if len(buffer) < packet_end: break
                packet_data = buffer[:packet_end]
                buffer = buffer[packet_end:]
                json_string = packet_data[json_len_offset + 4:].decode('utf-8', errors='ignore')
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

# --- Safe Shutdown Function (unchanged) ---
def safe_land_and_exit():
    print("🛑 Initiating safe shutdown...")
    sequence_running.clear()
    command_queue.queue.clear()
    command_queue.put(PACKET_STOP)
    time.sleep(0.2) 
    if latest_altitude_cm > 5.0:
        print("   Drone is airborne. Sending land command...")
        for _ in range(10): 
            send_packet(PACKET_LAND)
            time.sleep(0.1)
    else:
        print("   Drone is on the ground. Skipping land command.")
    is_running.clear()

# ===== APEX SEQUENCE FUNCTION =====
def run_apex_sequence():
    """Executes the 'Apex' flight plan provided by the user."""
    print("\n" + "="*50)
    print("🚀 STARTING APEX FLIGHT SEQUENCE")
    print("="*50)

    sequence_running.set() # Block the manual command loop
    try:
        # Step 1: Take Off and Stabilize
        print("STEP 1: Initiating Takeoff and stabilizing for 4 seconds...")
        send_packet(PACKET_TAKEOFF); time.sleep(0.05)
        send_packet(PACKET_TAKEOFF); time.sleep(0.05)
        for _ in range(40): # 4 seconds of hover
            send_packet(PACKET_STOP)
            time.sleep(0.1)

        # Step 2: Forward for 1 sec
        print("STEP 2: Moving FORWARD for 1 second...")
        for _ in range(10): # 1 second
            send_packet(PACKET_FORWARD)
            time.sleep(0.1)

        # Step 3: Ascend for 0.5 sec
        print("STEP 3: ASCENDING for 0.5 seconds...")
        for _ in range(5): # 0.5 seconds
            send_packet(PACKET_ASCEND)
            time.sleep(0.1)

        # Step 4: Forward for 1 sec
        print("STEP 4: Moving FORWARD for 1 second...")
        for _ in range(10): # 1 second
            send_packet(PACKET_FORWARD)
            time.sleep(0.1)

        # Step 5: Ascend for 0.5 sec
        print("STEP 5: ASCENDING for 0.5 seconds...")
        for _ in range(5): # 0.5 seconds
            send_packet(PACKET_ASCEND)
            time.sleep(0.1)

        # Step 6: Forward for 0.5 sec
        print("STEP 6: Moving FORWARD for 0.5 seconds...")
        for _ in range(5): # 0.5 seconds
            send_packet(PACKET_FORWARD)
            time.sleep(0.1)
        
        # Step 7: Descend for 0.5 sec
        print("STEP 7: DESCENDING for 0.5 seconds...")
        for _ in range(5): # 0.5 seconds
            send_packet(PACKET_DESCEND)
            time.sleep(0.1)

        # Step 8: Forward for 0.5 sec
        print("STEP 8: Moving FORWARD for 0.5 seconds...")
        for _ in range(5): # 0.5 seconds
            send_packet(PACKET_FORWARD)
            time.sleep(0.1)
            
        # Step 9: Descend for 0.5 sec
        print("STEP 9: DESCENDING for 0.5 seconds...")
        for _ in range(5): # 0.5 seconds
            send_packet(PACKET_DESCEND)
            time.sleep(0.1)

        # Step 10: Forward for 10 sec
        print("STEP 10: Moving FORWARD for 0.5 seconds...")
        for _ in range(10): # 10 seconds
            send_packet(PACKET_FORWARD)
            time.sleep(0.1)

        # Step 11: Land
        print("STEP 11: Sequence complete. Landing...")
        for _ in range(10): send_packet(PACKET_LAND); time.sleep(0.1)
        time.sleep(3.0)

    finally:
        print("\n--- ✅ APEX SEQUENCE COMPLETE ---")
        sequence_running.clear()
        command_queue.put(PACKET_STOP)


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
        print("   Please ensure the drone is on a flat, level surface.")
        for _ in range(5): send_packet(PACKET_GYRO_CAL); time.sleep(0.1)
        command_queue.put(PACKET_STOP)
        time.sleep(2)
        print("✅ Calibration complete.")
        print("="*60)
        try:
            while is_running.is_set():
                print("\n" + "="*60)
                print(f" Altitude: {latest_altitude_cm:6.1f} cm | Battery: {latest_battery_percent}%")
                print("="*60)
                print("--- Drone Control Menu ---")
                print(f"{'1. Take Off':<30} | {'2. Land':<30}")
                print(f"{'v. Run Apex Sequence':<30}")
                print("-" * 60)
                print("--- Movement (Press Enter after key) ---")
                print(f"{'[w] Forward':<30} | {'[u] Ascend':<30}")
                print(f"{'[s] Backward':<30} | {'[j] Descend':<30}")
                print(f"{'[a] Yaw Left':<30} | {'[d] Yaw Right':<30}")
                print(f"{'[q] Strafe Left':<30} | {'[e] Strafe Right':<30}")
                print("-" * 60)
                print(f"{'x. Stop (Hover)':<30} | {'exit. Quit Safely':<30}")
                print("="*60)
                choice = input("Enter command: ").strip().lower()

                if choice == 'v':
                    run_apex_sequence()
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
                elif choice == 'q': command_queue.put(PACKET_LEFT)
                elif choice == 'e': command_queue.put(PACKET_RIGHT)
                elif choice == 'a': command_queue.put(PACKET_YAW_LEFT)
                elif choice == 'd': command_queue.put(PACKET_YAW_RIGHT)
                elif choice == 'x': command_queue.put(PACKET_STOP)
                elif choice == 'exit':
                    safe_land_and_exit()
                    break
                else:
                    print("❌ Invalid command.")
                    command_queue.put(PACKET_STOP)
        except KeyboardInterrupt:
            safe_land_and_exit()
        finally:
            is_running.clear()
            if client_socket:
                client_socket.close()
            print("🔌 Disconnected.")
