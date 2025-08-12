import asyncio
from bleak import BleakScanner, BleakClient
import platform
import sys
import struct # Import struct for safe parsing

# --- NEW: Windows-specific asyncio event loop policy fix ---
# This is the definitive fix for ensuring BLE notifications are received reliably on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Device prefix and UUIDs
FILTER_PREFIXES = ["APEX"]
WRITE_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"
# We will use AE02, as it's the only one that has sent any data back,
# even if the format was unexpected. This is our best candidate.
NOTIFY_UUID = "0000ae02-0000-1000-8000-00805f9b34fb" 

# --- Global variables to store the latest sensor data ---
latest_altitude_cm = 0.0
latest_battery_percent = 0
latest_raw_data = "Waiting for data..." # For debugging

# --- Reliable 13-Byte Command Generator with Correct Checksum ---
def create_13byte_command(throttle=0x80, yaw=0x80, pitch=0x80, roll=0x80, func1=0x00):
    """
    Creates a valid 13-byte drone command packet by calculating the correct checksum.
    """
    payload = bytearray([
        throttle,   # Byte 1: Throttle
        yaw,        # Byte 2: Yaw
        0x00,       # Byte 3: Function
        pitch,      # Byte 4: Pitch
        roll,       # Byte 5: Roll
        0x40,       # Byte 6: Pitch Trim
        0x40,       # Byte 7: Roll Trim
        func1,      # Byte 8: Function1 (Takeoff/Land)
        0x00,       # Byte 9: Function2
        0x00        # Byte 10: Function3
    ])
    
    checksum = 0
    for byte in payload:
        checksum ^= byte
    checksum ^= 0x80

    command = bytearray([0xCC]) + payload + bytearray([checksum, 0x33])
    return bytes(command)

# --- Command Definitions ---
SEND_DATA_STOP = create_13byte_command()
SEND_DATA_TAKE_OFF = create_13byte_command(func1=0x08)
SEND_DATA_LAND = create_13byte_command(func1=0x08)
SEND_DATA_ASCEND = create_13byte_command(throttle=0xA0)
SEND_DATA_DESCEND = create_13byte_command(throttle=0x70)
SEND_DATA_FORWARD = create_13byte_command(pitch=0xFF)
SEND_DATA_BACKWARD = create_13byte_command(pitch=0x00)
SEND_DATA_LEFT = create_13byte_command(roll=0x00)
SEND_DATA_RIGHT = create_13byte_command(roll=0xFF)


# State variable to hold the currently active continuous command
active_command = SEND_DATA_STOP

# --- Command Mapping ---
DISCRETE_COMMANDS = {
    "1": ("Take Off", SEND_DATA_TAKE_OFF),
    "2": ("Land", SEND_DATA_LAND),
}

CONTINUOUS_COMMANDS = {
    "w": ("Start Ascend", SEND_DATA_ASCEND),
    "s": ("Start Descend", SEND_DATA_DESCEND),
    "f": ("Start Forward", SEND_DATA_FORWARD),
    "b": ("Start Backward", SEND_DATA_BACKWARD),
    "a": ("Start Left", SEND_DATA_LEFT),
    "d": ("Start Right", SEND_DATA_RIGHT),
    "x": ("Stop (Hover)", SEND_DATA_STOP),
}

# --- Notification Handler for Sensor Data ---
def notification_handler(sender, data):
    """
    This function is called every time the drone sends a notification packet.
    It parses the data and updates the global variables for altitude and battery.
    """
    global latest_altitude_cm, latest_battery_percent, latest_raw_data
    latest_raw_data = data.hex(' ') # Store the raw data for debugging
    
    # The official protocol sheet specifies a 16-byte packet for sensor data.
    if len(data) >= 16:
        try:
            # Byte 8 & 9: Altitude from barometer in millimeters (signed short)
            altitude_mm = int.from_bytes(data[8:10], byteorder='little', signed=True)
            latest_altitude_cm = altitude_mm / 10.0
            
            # Byte 10: Battery percentage (0-100)
            latest_battery_percent = data[10]
        except (IndexError, struct.error):
            # If parsing fails, just update the raw data and keep old values
            pass
        

# --- Main command loop for continuous actions ---
async def send_command_loop(client):
    """
    Continuously sends the command stored in the `active_command` variable.
    """
    print("🔥 Continuous command loop started.")
    pulse_counter = 0
    while True:
        command_to_send = active_command

        if active_command == SEND_DATA_DESCEND:
            pulse_counter += 1
            if pulse_counter % 5 > 1:
                command_to_send = SEND_DATA_STOP
        else:
            pulse_counter = 0

        await client.write_gatt_char(WRITE_UUID, command_to_send)
        await asyncio.sleep(0.1)

# --- Function for discrete actions ---
async def send_discrete_command(client, command_bytes, command_name):
    """
    Sends a discrete command reliably by sending it in a quick burst.
    """
    global active_command
    print(f"\n📤 [Discrete Command] Executing: {command_name}")
    
    active_command = SEND_DATA_STOP
    await client.write_gatt_char(WRITE_UUID, SEND_DATA_STOP) 
    await asyncio.sleep(0.1)

    for _ in range(5):
        await client.write_gatt_char(WRITE_UUID, command_bytes)
        await asyncio.sleep(0.05)
        
    active_command = SEND_DATA_STOP
    print(f"✅ Discrete command '{command_name}' finished.")


async def async_input(prompt: str = ""):
    """Async input function."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, prompt)

# --- The input loop for user control ---
async def user_input_loop(client):
    """Handles user input to change the drone's state."""
    global active_command
    while True:
        # Display the latest sensor data before the menu
        print("\n" + "="*50)
        print(f" Altitude: {latest_altitude_cm:6.1f} cm | Battery: {latest_battery_percent:3d}%")
        print(f" Raw Data: {latest_raw_data}")
        print("="*50)
        
        print("--- Drone Control Menu ---")
        print("1. Take Off")
        print("2. Land")
        print("---")
        print("w. Ascend (Hold)   | s. Descend (Hold)")
        print("f. Forward (Hold)  | b. Backward (Hold)")
        print("a. Left (Hold)     | d. Right (Hold)")
        print("x. Stop (Hover)")
        print("---")
        print("q. Quit")

        choice = (await async_input("Enter command: ")).strip().lower()
        if choice == "q":
            break
        
        if choice in DISCRETE_COMMANDS:
            name, cmd = DISCRETE_COMMANDS[choice]
            await send_discrete_command(client, cmd, name)
        
        elif choice in CONTINUOUS_COMMANDS:
            name, cmd = CONTINUOUS_COMMANDS[choice]
            print(f"\n🔄 [State Change] Setting active command to: {name}")
            active_command = cmd
        
        else:
            print("\n❌ Invalid selection")

async def main():
    """Main function for connection and setup."""
    print("🔍 Scanning for BLE devices...")
    devices = await BleakScanner.discover()
    filtered_devices = [d for d in devices if any(d.name and d.name.startswith(p) for p in FILTER_PREFIXES)]

    if not filtered_devices:
        print("⚠️ No matching BLE device found")
        return

    print("\n📋 Available BLE devices:")
    for idx, device in enumerate(filtered_devices):
        print(f"{idx}: {device.name} ({device.address})")

    try:
        choice = int(input("\nEnter the device number to connect: "))
        selected_device = filtered_devices[choice]
    except (ValueError, IndexError):
        print("❌ Invalid selection")
        return

    print(f"🔗 Connecting to: {selected_device.name} ({selected_device.address})")

    async with BleakClient(selected_device.address) as client:
        if not client.is_connected:
            print("❌ Connection failed")
            return

        print("✅ Connected successfully")

        # --- Subscribe to sensor data notifications ---
        print(f"📡 Subscribing to sensor data from UUID {NOTIFY_UUID}...")
        await client.start_notify(NOTIFY_UUID, notification_handler)

        try:
            command_task = asyncio.create_task(send_command_loop(client))
            await user_input_loop(client)
        finally:
            await client.stop_notify(NOTIFY_UUID)
            command_task.cancel()
            try:
                await command_task
            except asyncio.CancelledError:
                print("\n🛑 Command loop terminated")

        print("🔌 Disconnected")

if __name__ == "__main__":
    asyncio.run(main())
