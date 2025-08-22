import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
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
sequence_running = threading.Event()
command_queue = queue.Queue()

# --- Command Creation ---
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
CMD_ASCEND_JSON = create_wifi_command(throttle=200)
CMD_DESCEND_JSON = create_wifi_command(throttle=80)
CMD_FORWARD_JSON = create_wifi_command(pitch=200)
CMD_BACKWARD_JSON = create_wifi_command(pitch=50)
CMD_LEFT_JSON = create_wifi_command(roll=0)
CMD_RIGHT_JSON = create_wifi_command(roll=255)
CMD_YAW_LEFT_JSON = create_wifi_command(yaw=0)
CMD_YAW_RIGHT_JSON = create_wifi_command(yaw=255)
CMD_GYRO_CAL_JSON = create_wifi_command(func_byte3=1)
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
            print("Connection lost.")
            is_running.clear()
            return False
    return False

# --- Background Threads ---
def command_loop_thread():
    """Sends a continuous stream of commands."""
    global active_command_packet
    active_command_packet = PACKET_STOP
    while is_running.is_set():
        if sequence_running.is_set():
            time.sleep(0.02)
            continue
        try:
            packet_to_send = command_queue.get_nowait()
            active_command_packet = packet_to_send
        except queue.Empty:
            pass
        send_packet(active_command_packet)
        time.sleep(0.02) # Increased frequency to 50Hz for smoother control

def data_receive_thread():
    """Listens for and parses incoming data packets."""
    global latest_altitude_cm, latest_battery_percent
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

def emergency_stop():
    """Stops all sequences and lands the drone immediately."""
    print("EMERGENCY STOP ACTIVATED")
    sequence_running.clear()
    while not command_queue.empty():
        try:
            command_queue.get_nowait()
        except queue.Empty:
            continue
    for _ in range(10):
        send_packet(PACKET_STOP)
        send_packet(PACKET_LAND)
        time.sleep(0.1)

# --- Single Action Commands ---
def execute_manual_override(packet):
    """Temporarily takes control to send a single, critical command like Land or Take Off."""
    if sequence_running.is_set(): # Don't interfere with other sequences
        return

    def command_override():
        sequence_running.set() # Pause the main command loop

        print(f"Executing manual override...")
        # Send the critical command a few times to ensure it's received
        for _ in range(3):
            send_packet(packet)
            time.sleep(0.05)

        # Brute-force stop to ensure stability afterward
        for _ in range(25):
            send_packet(PACKET_STOP)
            time.sleep(0.02)

        # Clear the queue and ensure the next command is STOP
        while not command_queue.empty():
            try: command_queue.get_nowait()
            except queue.Empty: continue
        command_queue.put(PACKET_STOP)

        sequence_running.clear() # Release control back to the main loop
        print("Override complete.")

    threading.Thread(target=command_override, daemon=True).start()

# --- Automated Sequences ---
def run_sequence_in_thread(sequence_func):
    """Runs an automated sequence in a separate thread to avoid freezing the GUI."""
    if not sequence_running.is_set():
        threading.Thread(target=sequence_func, daemon=True).start()

def execute_flight_sequence(steps):
    """A generic function to execute a list of flight commands."""
    if not is_running.is_set() or not client_socket:
        print("Cannot start sequence: Not connected.")
        messagebox.showwarning("Not Connected", "Please connect to the drone before starting a sequence.")
        return
    sequence_running.set()
    try:
        print("Taking off...")
        for _ in range(5): send_packet(PACKET_TAKEOFF); time.sleep(0.1)
        for _ in range(20): send_packet(PACKET_STOP); time.sleep(0.1)

        for command, duration, message in steps:
            if not sequence_running.is_set(): break
            print(message)
            end_time = time.time() + duration
            while time.time() < end_time:
                if not sequence_running.is_set(): break
                send_packet(command)
                time.sleep(0.02)
            send_packet(PACKET_STOP)
            time.sleep(0.5)
        
    finally:
        print("Landing...")
        for _ in range(10): send_packet(PACKET_LAND); time.sleep(0.1)
        time.sleep(2)
        print("Sequence complete.")

        # --- ULTIMATE BRUTE FORCE FIX ---
        # This dedicated loop will aggressively spam STOP commands for 1 second,
        # overriding any other command in the system and ensuring the drone
        # comes to a complete and final halt after the sequence.
        print("Applying final brute force stop...")
        end_time = time.time() + 1.0
        while time.time() < end_time:
            send_packet(PACKET_STOP)
            time.sleep(0.02)

        while not command_queue.empty():
            try:
                command_queue.get_nowait()
            except queue.Empty:
                continue
        
        command_queue.put(PACKET_STOP)
        sequence_running.clear()

def run_circle_sequence():
    steps = [(PACKET_CIRCLE, 6.5, "Flying in a circle...")]
    execute_flight_sequence(steps)

def run_rectangle_sequence():
    steps = [
        (PACKET_FORWARD, 2.0, "Flying forward..."),
        (PACKET_RIGHT, 1.5, "Flying right..."),
        (PACKET_BACKWARD, 2.0, "Flying backward..."),
        (PACKET_LEFT, 1.5, "Flying left..."),
    ]
    execute_flight_sequence(steps)

def run_step_sequence():
    """Replicates the 'Apex' sequence from drone_step.py."""
    steps = [
        (PACKET_FORWARD, 1.0, "Step: Forward"),
        (PACKET_ASCEND, 0.5, "Step: Ascend"),
        (PACKET_FORWARD, 1.0, "Step: Forward"),
        (PACKET_ASCEND, 0.5, "Step: Ascend"),
        (PACKET_FORWARD, 0.5, "Step: Forward"),
        (PACKET_DESCEND, 0.5, "Step: Descend"),
        (PACKET_FORWARD, 0.5, "Step: Forward"),
        (PACKET_DESCEND, 0.5, "Step: Descend"),
        (PACKET_FORWARD, 1.0, "Step: Final Forward"),
    ]
    execute_flight_sequence(steps)

def run_vertical_circle_sequence():
    """Replicates the sequence from drone_ver_circle.py."""
    steps = [
        (PACKET_FORWARD, 2.5, "Step: Moving forward..."),
        (PACKET_ASCEND, 2.0, "Step: Ascending..."),
        (PACKET_BACKWARD, 2.5, "Step: Moving backward..."),
        (PACKET_DESCEND, 1.5, "Step: Descending..."),
        (PACKET_FORWARD, 2.5, "Step: Moving forward again..."),
        (PACKET_STOP, 2.0, "Step: Stabilizing..."),
    ]
    execute_flight_sequence(steps)

# --- GUI Application ---
class DroneControlApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Drone Control")
        self.geometry("800x650")
        self.configure(bg="#F0F2F5")

        self.style = ttk.Style(self)
        self.style.theme_use('clam')
        
        # Modern & Clean (Light Theme)
        self.style.configure("TFrame", background="#F0F2F5")
        self.style.configure("TLabel", background="#F0F2F5", foreground="#333333", font=('Helvetica', 12))
        self.style.configure("Header.TLabel", font=('Helvetica', 24, 'bold'))
        self.style.configure("Status.TLabel", font=('Helvetica', 16, 'bold'))
        self.style.configure("Conn.Status.TLabel", font=('Helvetica', 12, 'italic'))
        self.style.configure("TLabelframe", background="#F0F2F5", bordercolor="#CCCCCC")
        self.style.configure("TLabelframe.Label", background="#F0F2F5", foreground="#333333", font=('Helvetica', 12, 'bold'))

        self.style.configure("TButton", padding=10, relief="flat", background="#000000", foreground="white", font=('Helvetica', 12, 'bold'))
        self.style.map("TButton", background=[('active', "#000000")])
        
        self.style.configure("Emergency.TButton", background="#DC3545", foreground="white")
        self.style.map("Emergency.TButton", background=[('active', '#c82333')])
        
        self.style.configure("Connect.TButton", background="#28A745", foreground="white")
        self.style.map("Connect.TButton", background=[('active', '#218838')])

        self.control_widgets = []
        self.create_widgets()
        self.bind_keys()
        self.update_status_labels()
        self.set_controls_state(tk.DISABLED)

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(expand=True, fill=tk.BOTH)

        header_label = ttk.Label(main_frame, text="Drone Control", style="Header.TLabel", anchor="center")
        header_label.pack(pady=10, fill=tk.X)

        conn_frame = ttk.Frame(main_frame)
        conn_frame.pack(pady=10, fill=tk.X)
        
        self.connect_button = ttk.Button(conn_frame, text="Connect to Drone", command=self.connect_to_drone, style="Connect.TButton")
        self.connect_button.pack(side=tk.LEFT, expand=True, padx=5)
        
        self.connection_status_label = ttk.Label(conn_frame, text="Status: Disconnected", style="Conn.Status.TLabel", anchor="center")
        self.connection_status_label.pack(side=tk.LEFT, expand=True)

        status_frame = ttk.Frame(main_frame)
        status_frame.pack(pady=10, fill=tk.X)
        
        self.battery_label = ttk.Label(status_frame, text="Battery: --%", style="Status.TLabel", anchor="w")
        self.battery_label.pack(side=tk.LEFT, expand=True)

        self.altitude_label = ttk.Label(status_frame, text="Altitude: -- cm", style="Status.TLabel", anchor="e")
        self.altitude_label.pack(side=tk.RIGHT, expand=True)

        auto_frame = ttk.LabelFrame(main_frame, text="Automated Sequences", padding="15")
        auto_frame.pack(pady=10, fill=tk.X)

        auto_buttons = [
            ("Circle", lambda: run_sequence_in_thread(run_circle_sequence)),
            ("Rectangle", lambda: run_sequence_in_thread(run_rectangle_sequence)),
            ("Step", lambda: run_sequence_in_thread(run_step_sequence)),
            ("Vertical Circle", lambda: run_sequence_in_thread(run_vertical_circle_sequence))
        ]
        for i, (text, command) in enumerate(auto_buttons):
            btn = ttk.Button(auto_frame, text=text, command=command)
            btn.grid(row=0, column=i, padx=5, pady=5, sticky="ew")
            self.control_widgets.append(btn)
        auto_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        manual_frame = ttk.LabelFrame(main_frame, text="Manual Controls", padding="15")
        manual_frame.pack(pady=10, expand=True, fill=tk.BOTH)
        
        manual_controls = {
            "Forward (W)": (PACKET_FORWARD, 0, 1), "Backward (S)": (PACKET_BACKWARD, 2, 1),
            "Up (↑)": (PACKET_ASCEND, 1, 1), "Down (↓)": (PACKET_DESCEND, 3, 1),
            "Strafe L (A)": (PACKET_LEFT, 1, 0), "Strafe R (D)": (PACKET_RIGHT, 1, 2),
            "Yaw L (Q)": (PACKET_YAW_LEFT, 0, 0), "Yaw R (E)": (PACKET_YAW_RIGHT, 0, 2),
        }
        for text, (packet, r, c) in manual_controls.items():
            btn = ttk.Button(manual_frame, text=text)
            btn.bind("<ButtonPress>", lambda e, p=packet: command_queue.put(p))
            btn.bind("<ButtonRelease>", lambda e: command_queue.put(PACKET_STOP))
            btn.grid(row=r, column=c, padx=5, pady=5, sticky="nsew")
            self.control_widgets.append(btn)

        manual_frame.grid_columnconfigure((0, 1, 2), weight=1)
        manual_frame.grid_rowconfigure((0, 1, 2, 3), weight=1)

        system_frame = ttk.Frame(main_frame)
        system_frame.pack(pady=10, fill=tk.X)

        takeoff_btn = ttk.Button(system_frame, text="Take Off (T)", command=lambda: execute_manual_override(PACKET_TAKEOFF))
        takeoff_btn.pack(side=tk.LEFT, expand=True, padx=5)
        self.control_widgets.append(takeoff_btn)

        land_btn = ttk.Button(system_frame, text="Land (L)", command=lambda: execute_manual_override(PACKET_LAND))
        land_btn.pack(side=tk.LEFT, expand=True, padx=5)
        self.control_widgets.append(land_btn)

        emergency_btn = ttk.Button(system_frame, text="EMERGENCY STOP (Space)", style="Emergency.TButton", command=emergency_stop)
        emergency_btn.pack(side=tk.RIGHT, expand=True, padx=5, fill=tk.X)
        self.control_widgets.append(emergency_btn)

    def bind_keys(self):
        """Binds keyboard keys to drone actions."""
        self.bind_all('<KeyPress-t>', lambda e: execute_manual_override(PACKET_TAKEOFF))
        self.bind_all('<KeyPress-l>', lambda e: execute_manual_override(PACKET_LAND))
        self.bind_all('<space>', lambda e: emergency_stop())
        
        key_mappings = {
            'w': PACKET_FORWARD, 's': PACKET_BACKWARD,
            'a': PACKET_LEFT, 'd': PACKET_RIGHT,
            'q': PACKET_YAW_LEFT, 'e': PACKET_YAW_RIGHT,
            'Up': PACKET_ASCEND, 'Down': PACKET_DESCEND,
        }
        for key, packet in key_mappings.items():
            self.bind_all(f'<KeyPress-{key}>', lambda e, p=packet: command_queue.put(p))
            self.bind_all(f'<KeyRelease-{key}>', lambda e: command_queue.put(PACKET_STOP))

    def set_controls_state(self, state):
        """Enable or disable all drone control widgets."""
        for widget in self.control_widgets:
            widget.config(state=state)

    def connect_to_drone(self):
        """Handles the connection logic."""
        global client_socket
        self.connection_status_label.config(text="Status: Connecting...")
        self.update_idletasks()
        
        try:
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.settimeout(2.0) 
            client_socket.connect((HOST, PORT))
            
            is_running.set()
            threading.Thread(target=command_loop_thread, daemon=True).start()
            threading.Thread(target=data_receive_thread, daemon=True).start()
            
            self.connection_status_label.config(text="Status: Connected")
            self.connect_button.config(state=tk.DISABLED)
            self.set_controls_state(tk.NORMAL)
            
            print("Performing Gyro Calibration...")
            for _ in range(5): send_packet(PACKET_GYRO_CAL); time.sleep(0.1)
            command_queue.put(PACKET_STOP)
            time.sleep(2)
            print("Calibration complete.")
            messagebox.showinfo("Success", "Drone connected and calibrated successfully!")

        except socket.error as e:
            print(f"Failed to connect to drone: {e}")
            self.connection_status_label.config(text="Status: Connection Failed")
            client_socket = None
            messagebox.showerror("Connection Failed", f"Could not connect to the drone at {HOST}:{PORT}.\n\nPlease ensure you are on the correct Wi-Fi network and the drone is on.")

    def update_status_labels(self):
        """Periodically updates the battery and altitude labels."""
        self.battery_label.config(text=f"Battery: {latest_battery_percent}%")
        self.altitude_label.config(text=f"Altitude: {latest_altitude_cm:.1f} cm")
        self.after(500, self.update_status_labels)

    def on_closing(self):
        """Handles the window closing event."""
        print("Closing application...")
        is_running.clear()
        if client_socket:
            try:
                client_socket.close()
            except Exception as e:
                print(f"Error closing socket: {e}")
        self.destroy()

if __name__ == "__main__":
    app = DroneControlApp()
    app.mainloop()
