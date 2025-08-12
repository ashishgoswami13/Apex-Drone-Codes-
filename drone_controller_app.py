import tkinter as tk
from tkinter import ttk, messagebox
import socket
import json
import struct
import time
import threading
import queue

class DroneController:
    """
    Handles all backend communication and logic for controlling the drone.
    This class contains no GUI code.
    """
    def __init__(self):
        self.HOST = '192.168.1.1'
        self.PORT = 3333
        self.client_socket = None
        self.is_running = threading.Event()
        self.sequence_running = threading.Event()
        self.command_queue = queue.Queue()
        self.latest_altitude_cm = 0.0
        self.latest_battery_percent = 0
        self.active_command_packet = None

    def create_wifi_command(self, throttle=128, yaw=128, pitch=128, roll=128, func1=0, func_byte3=0):
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

    def create_packet(self, topic, content):
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

    def connect(self):
        """Connects to the drone and starts background threads."""
        if self.client_socket:
            return True
        try:
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_socket.settimeout(2.0)
            self.client_socket.connect((self.HOST, self.PORT))
            self.is_running.set()
            
            threading.Thread(target=self.command_loop_thread, daemon=True).start()
            threading.Thread(target=self.data_receive_thread, daemon=True).start()
            
            print("✅ Drone connected successfully.")
            return True
        except socket.error as e:
            print(f"❌ Failed to connect to drone: {e}")
            self.client_socket = None
            return False

    def disconnect(self):
        """Disconnects from the drone and stops all threads."""
        if self.is_running.is_set():
            print("🛑 Disconnecting...")
            self.is_running.clear()
            time.sleep(0.5)
            if self.client_socket:
                self.client_socket.close()
                self.client_socket = None
            print("🔌 Disconnected.")

    def send_packet(self, packet):
        """Sends a packet to the drone."""
        if self.client_socket and self.is_running.is_set():
            try:
                self.client_socket.sendall(packet)
                return True
            except (socket.error, BrokenPipeError):
                print("Connection lost.")
                self.disconnect()
                return False
        return False

    def command_loop_thread(self):
        """Sends a continuous stream of commands to keep the connection alive."""
        self.active_command_packet = self.get_packet("stop")
        while self.is_running.is_set():
            if not self.sequence_running.is_set():
                if not self.command_queue.empty():
                    self.active_command_packet = self.command_queue.get()
                self.send_packet(self.active_command_packet)
            time.sleep(0.05)

    def data_receive_thread(self):
        """Listens for and parses incoming data packets from the drone."""
        buffer = bytearray()
        while self.is_running.is_set():
            if not self.client_socket:
                time.sleep(1)
                continue
            try:
                data = self.client_socket.recv(1024)
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
                            self.latest_altitude_cm = altitude_mm / 10.0
                            self.latest_battery_percent = int(params.get("D10", 0))
            except socket.timeout:
                continue
            except (socket.error, IndexError, json.JSONDecodeError, struct.error):
                buffer = bytearray()
                time.sleep(1)

    def get_packet(self, command_name):
        """Helper function to get pre-defined command packets."""
        commands = {
            "stop": self.create_wifi_command(),
            "takeoff": self.create_wifi_command(func1=8),
            "land": self.create_wifi_command(func1=8),
            "ascend": self.create_wifi_command(throttle=200),
            "descend": self.create_wifi_command(throttle=80),
            "forward": self.create_wifi_command(pitch=200),
            "backward": self.create_wifi_command(pitch=50),
            "left": self.create_wifi_command(roll=0),
            "right": self.create_wifi_command(roll=255),
            "yaw_left": self.create_wifi_command(yaw=0),
            "yaw_right": self.create_wifi_command(yaw=255),
            "gyro_cal": self.create_wifi_command(func_byte3=1),
            "circle": self.create_wifi_command(pitch=255, yaw=255)
        }
        json_cmd = commands.get(command_name, commands["stop"])
        return self.create_packet("GENERIC_CMD", json_cmd)

    def run_sequence_in_thread(self, sequence_func, app_callback):
        """Runs a flight sequence in a separate thread to prevent freezing the GUI."""
        if self.sequence_running.is_set():
            print("A sequence is already running.")
            return
        
        def sequence_wrapper():
            self.sequence_running.set()
            app_callback(True) # Notify GUI that sequence has started
            try:
                sequence_func()
            finally:
                self.safe_land_and_stop()
                print(f"--- ✅ SEQUENCE {sequence_func.__name__.upper().replace('SEQUENCE_', '')} COMPLETE ---")
                self.sequence_running.clear()
                app_callback(False) # Notify GUI that sequence has finished
        
        threading.Thread(target=sequence_wrapper, daemon=True).start()

    def safe_land_and_stop(self):
        """Stops all movement and lands the drone."""
        print("🛑 EMERGENCY STOP/LAND INITIATED...")
        self.sequence_running.clear()
        while not self.command_queue.empty():
            self.command_queue.get()
            
        self.command_queue.put(self.get_packet("stop"))
        time.sleep(0.2)
        if self.latest_altitude_cm > 5.0:
            print("    Drone is airborne. Sending land command...")
            for _ in range(10):
                self.send_packet(self.get_packet("land"))
                time.sleep(0.1)
        else:
            print("    Drone is on the ground.")
        self.command_queue.put(self.get_packet("stop"))

    # --- Automated Flight Sequences (Corrected to match source files) ---
    def sequence_rectangle(self):
        """FIX: This sequence now correctly matches the logic from drone_rectangle.py"""
        print("🚀 STARTING RECTANGLE SEQUENCE")
        for _ in range(5): self.send_packet(self.get_packet("takeoff")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(30): self.send_packet(self.get_packet("stop")); time.sleep(0.1)
        
        if not self.sequence_running.is_set(): return
        for _ in range(20): self.send_packet(self.get_packet("forward")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(10): self.send_packet(self.get_packet("stop")); time.sleep(0.1)
        
        if not self.sequence_running.is_set(): return
        for _ in range(15): self.send_packet(self.get_packet("right")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(10): self.send_packet(self.get_packet("stop")); time.sleep(0.1)

        if not self.sequence_running.is_set(): return
        for _ in range(20): self.send_packet(self.get_packet("backward")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(10): self.send_packet(self.get_packet("stop")); time.sleep(0.1)

        if not self.sequence_running.is_set(): return
        for _ in range(15): self.send_packet(self.get_packet("left")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(10): self.send_packet(self.get_packet("stop")); time.sleep(0.1)

    def sequence_circle(self):
        print("🚀 STARTING CIRCLE SEQUENCE")
        for _ in range(5): self.send_packet(self.get_packet("takeoff")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        self.command_queue.put(self.get_packet("stop")); time.sleep(3.0)
        
        if not self.sequence_running.is_set(): return
        self.command_queue.put(self.get_packet("circle")); time.sleep(6.5)
        
        if not self.sequence_running.is_set(): return
        self.command_queue.put(self.get_packet("stop")); time.sleep(1.0)

    def sequence_vertical_circle(self):
        print("🚀 STARTING VERTICAL CIRCLE SEQUENCE")
        # Step 1: Take Off and Stabilize
        self.send_packet(self.get_packet("takeoff")); time.sleep(0.05)
        if not self.sequence_running.is_set(): return
        self.send_packet(self.get_packet("takeoff")); time.sleep(0.05)
        if not self.sequence_running.is_set(): return
        for _ in range(40):
            if not self.sequence_running.is_set(): return
            self.send_packet(self.get_packet("stop"))
            time.sleep(0.1)

        # Step 2: Forward for 2.5 sec
        for _ in range(25):
            if not self.sequence_running.is_set(): return
            self.send_packet(self.get_packet("forward"))
            time.sleep(0.1)

        # Step 3: Ascend for 2 sec
        for _ in range(20):
            if not self.sequence_running.is_set(): return
            self.send_packet(self.get_packet("ascend"))
            time.sleep(0.1)

        # Step 4: Backward for 2.5 sec
        for _ in range(25):
            if not self.sequence_running.is_set(): return
            self.send_packet(self.get_packet("backward"))
            time.sleep(0.1)

        # Step 5: Descend for 1.5 sec
        for _ in range(15):
            if not self.sequence_running.is_set(): return
            self.send_packet(self.get_packet("descend"))
            time.sleep(0.1)

        # Step 6: Forward for 2.5 sec
        for _ in range(25):
            if not self.sequence_running.is_set(): return
            self.send_packet(self.get_packet("forward"))
            time.sleep(0.1)
        
        # Step 7: Final Stabilization
        for _ in range(20):
            if not self.sequence_running.is_set(): return
            self.send_packet(self.get_packet("stop"))
            time.sleep(0.1)

    def sequence_step(self):
        """FIX: This sequence now correctly matches the logic from drone_step.py"""
        print("🚀 STARTING STEP SEQUENCE")
        self.send_packet(self.get_packet("takeoff")); time.sleep(0.05)
        if not self.sequence_running.is_set(): return
        self.send_packet(self.get_packet("takeoff")); time.sleep(0.05)
        if not self.sequence_running.is_set(): return
        for _ in range(40): self.send_packet(self.get_packet("stop")); time.sleep(0.1)

        if not self.sequence_running.is_set(): return
        for _ in range(10): self.send_packet(self.get_packet("forward")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(5): self.send_packet(self.get_packet("ascend")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(10): self.send_packet(self.get_packet("forward")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(5): self.send_packet(self.get_packet("ascend")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(5): self.send_packet(self.get_packet("forward")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(5): self.send_packet(self.get_packet("descend")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(5): self.send_packet(self.get_packet("forward")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(5): self.send_packet(self.get_packet("descend")); time.sleep(0.1)
        if not self.sequence_running.is_set(): return
        for _ in range(10): self.send_packet(self.get_packet("forward")); time.sleep(0.1)

class DroneApp(tk.Tk):
    """
    The main Tkinter application window with an improved UI.
    """
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.title("Drone Control Panel")
        self.geometry("620x700")
        self.configure(bg="#f0f0f0")
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Style configuration
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure("TButton", padding=6, relief="flat", background="#cccccc", font=('Helvetica', 10))
        style.map("TButton", background=[('active', '#b0b0b0')])
        style.configure("Emergency.TButton", foreground="white", background="#e74c3c", font=('Helvetica', 10, 'bold'))
        style.map("Emergency.TButton", background=[('active', '#c0392b')])
        style.configure("TLabelFrame", padding=10, borderwidth=1, relief="solid")
        style.configure("TLabelFrame.Label", font=('Helvetica', 11, 'bold'), foreground="#333333")

        self.connection_status = tk.StringVar(value="Status: Disconnected")
        self.battery_status = tk.StringVar(value="Battery: --%")
        self.altitude_status = tk.StringVar(value="Altitude: -- cm")
        self.sequence_status = tk.StringVar(value="Sequence: Idle")

        self.create_widgets()
        self.update_telemetry()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Connection and Status Frame ---
        status_frame = ttk.LabelFrame(main_frame, text="1. Connection", padding="10")
        status_frame.pack(fill=tk.X, pady=5)
        
        self.connect_button = ttk.Button(status_frame, text="Connect to Drone", command=self.toggle_connection)
        self.connect_button.pack(side=tk.LEFT, padx=5, ipadx=10)
        ttk.Label(status_frame, textvariable=self.connection_status, font=('Helvetica', 10, 'bold')).pack(side=tk.LEFT, padx=10)

        # --- Drone Status Frame ---
        telemetry_frame = ttk.LabelFrame(main_frame, text="2. Drone Status", padding="10")
        telemetry_frame.pack(fill=tk.X, pady=10)
        ttk.Label(telemetry_frame, textvariable=self.battery_status, font=('Helvetica', 10)).pack(side=tk.LEFT, padx=10, expand=True)
        ttk.Label(telemetry_frame, textvariable=self.altitude_status, font=('Helvetica', 10)).pack(side=tk.LEFT, padx=10, expand=True)
        ttk.Label(telemetry_frame, textvariable=self.sequence_status, font=('Helvetica', 10, 'italic')).pack(side=tk.LEFT, padx=10, expand=True)

        # --- Automated Flight Frame ---
        self.auto_frame = ttk.LabelFrame(main_frame, text="3. Automated Flight Patterns", padding="10")
        self.auto_frame.pack(fill=tk.X, pady=10)
        
        self.auto_buttons = []
        auto_sequences = {
            "Circle": self.controller.sequence_circle,
            "Rectangle": self.controller.sequence_rectangle,
            "Vertical Circle": self.controller.sequence_vertical_circle,
            "Step": self.controller.sequence_step
        }
        for name, cmd in auto_sequences.items():
            btn = ttk.Button(self.auto_frame, text=name, command=lambda c=cmd: self.controller.run_sequence_in_thread(c, self.update_sequence_status))
            btn.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X, ipady=5)
            self.auto_buttons.append(btn)

        # --- Manual Control Frame ---
        manual_frame = ttk.LabelFrame(main_frame, text="4. Manual Control", padding="10")
        manual_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        basic_cmd_frame = ttk.Frame(manual_frame)
        basic_cmd_frame.pack(fill=tk.X, pady=10)
        ttk.Button(basic_cmd_frame, text="Take Off (T)", command=lambda: self.send_toggle_command("takeoff")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5, ipady=10)
        ttk.Button(basic_cmd_frame, text="Land (L)", command=lambda: self.send_toggle_command("land")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5, ipady=10)
        ttk.Button(basic_cmd_frame, text="EMERGENCY STOP (Space)", style="Emergency.TButton", command=self.controller.safe_land_and_stop).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5, ipady=10)

        movement_grid = ttk.Frame(manual_frame, padding="10")
        movement_grid.pack(expand=True)
        
        # Altitude and Yaw (Left Side)
        left_controls = ttk.Frame(movement_grid)
        left_controls.pack(side=tk.LEFT, padx=40, expand=True)
        ttk.Label(left_controls, text="Up (R)", font=('Helvetica', 10)).pack()
        ttk.Button(left_controls, text="▲", width=5).pack(pady=5)
        ttk.Label(left_controls, text="Yaw Left (Q)", font=('Helvetica', 10)).pack()
        ttk.Button(left_controls, text="↶", width=5).pack(pady=5)
        ttk.Label(left_controls, text="Down (F)", font=('Helvetica', 10)).pack()
        ttk.Button(left_controls, text="▼", width=5).pack(pady=5)
        ttk.Label(left_controls, text="Yaw Right (E)", font=('Helvetica', 10)).pack()
        ttk.Button(left_controls, text="↷", width=5).pack(pady=5)
        
        # Pitch and Roll (Right Side - D-pad style)
        right_controls = ttk.Frame(movement_grid)
        right_controls.pack(side=tk.RIGHT, padx=40, expand=True)
        ttk.Button(right_controls, text="Forward (W)").grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(right_controls, text="Left (A)").grid(row=1, column=0, padx=5, pady=5)
        ttk.Button(right_controls, text="Stop (S)").grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(right_controls, text="Right (D)").grid(row=1, column=2, padx=5, pady=5)
        ttk.Button(right_controls, text="Backward (X)").grid(row=2, column=1, padx=5, pady=5)

        self.bind_keys()

    def bind_keys(self):
        """Binds keyboard keys to drone commands."""
        continuous_key_map = {
            'w': "forward", 's': "stop", 'x': "backward",
            'a': "left", 'd': "right",
            'q': "yaw_left", 'e': "yaw_right",
            'r': "ascend", 'f': "descend",
        }
        for key, command in continuous_key_map.items():
            self.bind(f"<KeyPress-{key}>", lambda event, cmd=command: self.send_command(cmd))
            self.bind(f"<KeyRelease-{key}>", lambda event: self.send_command("stop"))

        toggle_key_map = {
            't': "takeoff", 'l': "land",
            'space': "emergency"
        }
        for key, command in toggle_key_map.items():
            self.bind(f"<KeyPress-{key}>", lambda event, cmd=command: self.send_toggle_command(cmd))

    def send_command(self, command_name):
        packet = self.controller.get_packet(command_name)
        self.controller.command_queue.put(packet)

    def send_toggle_command(self, command_name):
        if command_name == "emergency":
            self.controller.safe_land_and_stop()
            return
        def command_burst():
            packet = self.controller.get_packet(command_name)
            for _ in range(5):
                self.controller.send_packet(packet)
                time.sleep(0.05)
            self.controller.command_queue.put(self.controller.get_packet("stop"))
        threading.Thread(target=command_burst, daemon=True).start()

    def toggle_connection(self):
        if self.controller.client_socket:
            self.controller.disconnect()
        else:
            if self.controller.connect():
                # Perform gyro calibration on connect
                messagebox.showinfo("Calibration", "Connected! Performing Gyro Calibration. Ensure the drone is on a flat surface.")
                for _ in range(5): self.controller.send_packet(self.controller.get_packet("gyro_cal")); time.sleep(0.1)
                self.controller.command_queue.put(self.controller.get_packet("stop"))
            else:
                messagebox.showerror("Connection Failed", "Could not connect to the drone. Check WiFi and ensure the drone is on.")

    def update_telemetry(self):
        if self.controller.is_running.is_set():
            self.connection_status.set("Status: Connected")
            self.connect_button.config(text="Disconnect")
        else:
            self.connection_status.set("Status: Disconnected")
            self.connect_button.config(text="Connect to Drone")

        self.battery_status.set(f"Battery: {self.controller.latest_battery_percent}%")
        self.altitude_status.set(f"Altitude: {self.controller.latest_altitude_cm:.1f} cm")
        self.after(1000, self.update_telemetry)

    def update_sequence_status(self, is_running):
        """Callback function for the controller to update the GUI."""
        if is_running:
            self.sequence_status.set("Sequence: Running...")
            for btn in self.auto_buttons:
                btn.config(state=tk.DISABLED)
        else:
            self.sequence_status.set("Sequence: Idle")
            for btn in self.auto_buttons:
                btn.config(state=tk.NORMAL)

    def on_closing(self):
        if messagebox.askokcancel("Quit", "Do you want to quit? This will disconnect and attempt to land the drone."):
            self.controller.safe_land_and_stop()
            self.controller.disconnect()
            self.destroy()

if __name__ == "__main__":
    drone_controller = DroneController()
    app = DroneApp(drone_controller)
    app.mainloop()

